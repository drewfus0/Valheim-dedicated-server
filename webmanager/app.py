import json
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    authenticate_user,
    create_session,
    create_user,
    delete_user,
    destroy_session,
    get_session_user,
    list_users,
    load_users,
    update_password,
    update_user_role,
)
from core.backups import (
    create_backup,
    create_world_metadata,
    delete_backup,
    generate_random_seed,
    get_backup_path,
    get_world_seed,
    list_available_worlds,
    list_backups,
    restore_backup,
)
from core.config import load_config
from core.logs import log_stream_generator, read_log_tail
from core.paths import SERVER_HISTORY_LOG
from core.playit import get_detected_playit_tunnel, start_playit_monitor
from core.server import SERVER_MANAGER
from core.server_history import SERVER_HISTORY
from core.system import (
    configure_system_power_and_performance,
    execute_host_reboot,
    get_cpu_frequencies,
    get_system_power_status,
)

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[FastAPI] Valheim Server Manager starting up...")
    load_users()  # Ensure default admin account is provisioned
    start_playit_monitor()
    configure_system_power_and_performance()  # Confirm & configure performance profile, no-sleep, and boot service
    SERVER_MANAGER.check_auto_start_on_boot()  # Auto-start Valheim server on boot if configured
    yield
    print("[FastAPI] Valheim Server Manager shutting down...")


app = FastAPI(title="Valheim Server Manager", lifespan=lifespan)

# Mount static files and templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon():
    ico_path = BASE_DIR / "static" / "favicon.ico"
    if ico_path.exists():
        return FileResponse(ico_path, media_type="image/x-icon")
    return Response(status_code=404)


@app.api_route("/site.webmanifest", methods=["GET", "HEAD"], include_in_schema=False)
async def webmanifest():
    manifest_path = BASE_DIR / "static" / "site.webmanifest"
    if manifest_path.exists():
        return FileResponse(manifest_path, media_type="application/manifest+json")
    return Response(status_code=404)



def make_toast_headers(message: str, toast_type: str = "info", **extra_triggers) -> dict:
    """Helper to generate HTMX HX-Trigger response headers for toasts."""
    trigger_data = {
        "showToast": {"value": message, "type": toast_type},
        "statusChanged": True,
        "configUpdated": True,
        "serverHistoryUpdated": True,
        **extra_triggers,
    }
    return {"HX-Trigger": json.dumps(trigger_data)}


def get_current_user_from_request(request: Request) -> Optional[dict]:
    """Retrieve authenticated user object from session cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return get_session_user(token)


def require_auth(request: Request) -> Optional[Response]:
    """Check auth and return redirect response if unauthenticated."""
    user = get_current_user_from_request(request)
    if not user:
        if request.headers.get("HX-Request") == "true":
            return Response(headers={"HX-Redirect": "/login"})
        return RedirectResponse(url="/login", status_code=303)
    return None


# ==========================================
# Authentication & Login Routes
# ==========================================


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user_from_request(request)
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/api/auth/login")
async def auth_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    user = authenticate_user(username, password)
    if not user:
        error_html = (
            '<div id="login-error-box" class="login-error" style="display: block;">'
            "Invalid username or password. Please check your credentials."
            "</div>"
        )
        return HTMLResponse(content=error_html, status_code=200)

    token = create_session(user)
    response = Response(headers={"HX-Redirect": "/"})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/auth/register")
async def auth_register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    try:
        new_user = create_user(username=username, password=password, role="viewer")
        token = create_session(new_user)
        response = Response(headers={"HX-Redirect": "/"})
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
        )
        return response
    except Exception as e:
        error_html = (
            f'<div id="login-error-box" class="login-error" style="display: block;">'
            f"{e}"
            f"</div>"
        )
        return HTMLResponse(content=error_html, status_code=200)


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    destroy_session(token)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.post("/api/auth/change-password", response_class=HTMLResponse)
async def auth_change_password(
    request: Request,
    new_password: str = Form(...),
):
    user = get_current_user_from_request(request)
    if not user:
        return HTMLResponse(content="<div style='color: #fca5a5;'>Unauthorized</div>", status_code=401)

    try:
        update_password(user["username"], new_password)
        return HTMLResponse(
            content="<div style='background: rgba(16, 185, 129, 0.15); border: 1px solid var(--status-running); color: #6ee7b7; padding: 0.6rem 0.8rem; border-radius: var(--radius-sm); font-size: 0.85rem;'>✅ Password successfully updated!</div>"
        )
    except Exception as e:
        return HTMLResponse(
            content=f"<div style='background: rgba(239, 68, 68, 0.15); border: 1px solid var(--status-stopped); color: #fca5a5; padding: 0.6rem 0.8rem; border-radius: var(--radius-sm); font-size: 0.85rem;'>❌ {e}</div>"
        )


# ==========================================
# Web UI Pages & Partial Views
# ==========================================


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    status = SERVER_MANAGER.get_status_data()
    backups = list_backups()
    available_worlds = list_available_worlds()
    users = list_users() if user and user.get("role") == "admin" else []

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "user": user,
            "status": status,
            "config": status["config"],
            "backups": backups,
            "available_worlds": available_worlds,
            "users": users,
            "current_user": user,
        },
    )


@app.get("/partials/status", response_class=HTMLResponse)
async def partial_status(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    status = SERVER_MANAGER.get_status_data()
    return templates.TemplateResponse(
        request=request,
        name="partials/status_card.html",
        context={"status": status, "user": user},
    )


@app.get("/partials/header-status", response_class=HTMLResponse)
async def partial_header_status(request: Request):
    status = SERVER_MANAGER.get_status_data()
    return templates.TemplateResponse(
        request=request,
        name="partials/header_status.html",
        context={"status": status},
    )


@app.get("/partials/players", response_class=HTMLResponse)
async def partial_players(request: Request, panel_id: Optional[str] = "players-panel"):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    status = SERVER_MANAGER.get_status_data()
    return templates.TemplateResponse(
        request=request,
        name="partials/player_list.html",
        context={"status": status, "user": user, "panel_id": panel_id},
    )


@app.get("/partials/players-tab", response_class=HTMLResponse)
async def partial_players_tab(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    status = SERVER_MANAGER.get_status_data()
    return templates.TemplateResponse(
        request=request,
        name="partials/players_tab.html",
        context={"status": status, "user": user},
    )


@app.get("/partials/session-history", response_class=HTMLResponse)
async def partial_session_history(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        return HTMLResponse(
            content="<div class='card' style='color: #fca5a5;'>Access Restricted to Administrators</div>",
            status_code=403,
        )

    status = SERVER_MANAGER.get_status_data()
    return templates.TemplateResponse(
        request=request,
        name="partials/session_history.html",
        context={"status": status, "user": user},
    )


@app.get("/partials/server-history", response_class=HTMLResponse)
async def partial_server_history(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    status = SERVER_MANAGER.get_status_data()
    return templates.TemplateResponse(
        request=request,
        name="partials/server_history.html",
        context={"status": status, "user": user},
    )


@app.get("/partials/backups", response_class=HTMLResponse)
async def partial_backups(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    backups = list_backups()
    return templates.TemplateResponse(
        request=request,
        name="partials/backup_table.html",
        context={"backups": backups, "user": user},
    )


@app.get("/partials/config", response_class=HTMLResponse)
async def partial_config(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    cfg = load_config()
    available_worlds = list_available_worlds()
    return templates.TemplateResponse(
        request=request,
        name="partials/config_form.html",
        context={"config": cfg, "available_worlds": available_worlds, "user": user},
    )


@app.get("/partials/connection", response_class=HTMLResponse)
async def partial_connection(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    status = SERVER_MANAGER.get_status_data()
    return templates.TemplateResponse(
        request=request,
        name="partials/connection_card.html",
        context={"config": status["config"], "user": user},
    )


@app.get("/partials/accounts", response_class=HTMLResponse)
async def partial_accounts(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        return HTMLResponse(content="<div class='card' style='color: #fca5a5;'>Access Restricted to Administrators</div>", status_code=403)

    users = list_users()
    return templates.TemplateResponse(
        request=request,
        name="partials/accounts_tab.html",
        context={"users": users, "current_user": user, "user": user},
    )


# ==========================================
# Account Management Actions (Admin Only)
# ==========================================


@app.post("/api/users/create", response_class=HTMLResponse)
async def user_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
):
    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        return HTMLResponse(content="Unauthorized", status_code=403)

    try:
        create_user(username, password, role)
        headers = make_toast_headers(f"Account '{username}' successfully created.", "success", accountsUpdated=True)
    except Exception as e:
        headers = make_toast_headers(f"Failed to create user: {e}", "error")

    users = list_users()
    return templates.TemplateResponse(
        request=request,
        name="partials/accounts_tab.html",
        context={"users": users, "current_user": user, "user": user},
        headers=headers,
    )


@app.delete("/api/users/delete/{target_username}", response_class=HTMLResponse)
async def user_delete(request: Request, target_username: str):
    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        return HTMLResponse(content="Unauthorized", status_code=403)

    try:
        delete_user(target_username)
        headers = make_toast_headers(f"Account '{target_username}' deleted.", "info", accountsUpdated=True)
    except Exception as e:
        headers = make_toast_headers(f"Failed to delete user: {e}", "error")

    users = list_users()
    return templates.TemplateResponse(
        request=request,
        name="partials/accounts_tab.html",
        context={"users": users, "current_user": user, "user": user},
        headers=headers,
    )


@app.post("/api/users/update-role/{target_username}", response_class=HTMLResponse)
async def user_update_role(
    request: Request,
    target_username: str,
    new_role: str = Form(...),
):
    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        return HTMLResponse(content="Unauthorized", status_code=403)

    try:
        update_user_role(target_username, new_role)
        headers = make_toast_headers(f"Role updated for '{target_username}' to {new_role.capitalize()}.", "success", accountsUpdated=True)
    except Exception as e:
        headers = make_toast_headers(f"Failed to update role: {e}", "error")

    users = list_users()
    return templates.TemplateResponse(
        request=request,
        name="partials/accounts_tab.html",
        context={"users": users, "current_user": user, "user": user},
        headers=headers,
    )


# ==========================================
# HTMX & Server Control Actions (RBAC Protected)
# ==========================================


@app.post("/api/server/start", response_class=HTMLResponse)
async def server_start(request: Request):
    user = get_current_user_from_request(request)
    if not user or user.get("role") not in ("admin", "operator"):
        headers = make_toast_headers("Permission denied: Operator or Admin access required.", "error")
        status = SERVER_MANAGER.get_status_data()
        return templates.TemplateResponse(
            request=request,
            name="partials/status_card.html",
            context={"status": status, "user": user},
            headers=headers,
        )

    ok, msg = SERVER_MANAGER.start_server()
    status = SERVER_MANAGER.get_status_data()
    toast_type = "success" if ok else "error"
    return templates.TemplateResponse(
        request=request,
        name="partials/status_card.html",
        context={"status": status, "user": user},
        headers=make_toast_headers(msg, toast_type),
    )


@app.post("/api/server/stop", response_class=HTMLResponse)
async def server_stop(request: Request):
    user = get_current_user_from_request(request)
    if not user or user.get("role") not in ("admin", "operator"):
        headers = make_toast_headers("Permission denied: Operator or Admin access required.", "error")
        status = SERVER_MANAGER.get_status_data()
        return templates.TemplateResponse(
            request=request,
            name="partials/status_card.html",
            context={"status": status, "user": user},
            headers=headers,
        )

    ok, msg = SERVER_MANAGER.stop_server()
    status = SERVER_MANAGER.get_status_data()
    toast_type = "success" if ok else "error"
    return templates.TemplateResponse(
        request=request,
        name="partials/status_card.html",
        context={"status": status, "user": user},
        headers=make_toast_headers(msg, toast_type),
    )


@app.post("/api/server/restart", response_class=HTMLResponse)
async def server_restart(request: Request):
    user = get_current_user_from_request(request)
    if not user or user.get("role") not in ("admin", "operator"):
        headers = make_toast_headers("Permission denied: Operator or Admin access required.", "error")
        status = SERVER_MANAGER.get_status_data()
        return templates.TemplateResponse(
            request=request,
            name="partials/status_card.html",
            context={"status": status, "user": user},
            headers=headers,
        )

    ok, msg = SERVER_MANAGER.restart_server()
    status = SERVER_MANAGER.get_status_data()
    toast_type = "success" if ok else "error"
    return templates.TemplateResponse(
        request=request,
        name="partials/status_card.html",
        context={"status": status, "user": user},
        headers=make_toast_headers(msg, toast_type),
    )


@app.post("/api/system/reboot", response_class=HTMLResponse)
async def system_reboot(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        return HTMLResponse(content="Unauthorized", status_code=401)

    cpu_info = get_cpu_frequencies()
    is_admin = user.get("role") == "admin"
    is_operator = user.get("role") == "operator"
    is_throttled = cpu_info.get("all_under_600", False)

    # Admins can reboot anytime; Operators can reboot ONLY IF throttled under 600 MHz
    if not is_admin and not (is_operator and is_throttled):
        headers = make_toast_headers(
            "Permission denied: Host reboot restricted (Operators may only reboot during emergency CPU throttling <600MHz).",
            "error",
        )
        status = SERVER_MANAGER.get_status_data()
        return templates.TemplateResponse(
            request=request,
            name="partials/status_card.html",
            context={"status": status, "user": user},
            headers=headers,
        )

    ok, msg = execute_host_reboot(SERVER_MANAGER)
    status = SERVER_MANAGER.get_status_data()
    toast_type = "success" if ok else "error"
    return templates.TemplateResponse(
        request=request,
        name="partials/status_card.html",
        context={"status": status, "user": user},
        headers=make_toast_headers(msg, toast_type),
    )


@app.get("/api/system/power")
async def get_system_power(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return get_system_power_status()


@app.post("/api/system/power/performance", response_class=HTMLResponse)
async def configure_power_performance_endpoint(request: Request):
    user = get_current_user_from_request(request)
    if not user or user.get("role") not in ("admin", "operator"):
        headers = make_toast_headers(
            "Permission denied: Modifying system power settings restricted to Admins/Operators.",
            "error",
        )
        status = SERVER_MANAGER.get_status_data()
        return templates.TemplateResponse(
            request=request,
            name="partials/status_card.html",
            context={"status": status, "user": user},
            headers=headers,
        )

    res = configure_system_power_and_performance()
    status = SERVER_MANAGER.get_status_data()
    msg = f"System configured: {res.get('profile', 'performance').capitalize()} profile active & sleep disabled (display blanking/lock allowed)."
    return templates.TemplateResponse(
        request=request,
        name="partials/status_card.html",
        context={"status": status, "user": user},
        headers=make_toast_headers(msg, "success"),
    )


@app.post("/api/config", response_class=HTMLResponse)
async def update_configuration(
    request: Request,
    server_name: str = Form(...),
    world_name: str = Form(...),
    password: str = Form(...),
    port: str = Form(...),
    playit_address: Optional[str] = Form(""),
    auto_start_on_boot: Optional[str] = Form(None),
    auto_restart: Optional[str] = Form(None),
    mod_preset: Optional[str] = Form(""),
    mod_combat: Optional[str] = Form(""),
    mod_death_penalty: Optional[str] = Form(""),
    mod_resources: Optional[str] = Form(""),
    mod_raids: Optional[str] = Form(""),
    mod_portals: Optional[str] = Form(""),
    mod_nobuildcost: Optional[str] = Form(None),
    mod_playerevents: Optional[str] = Form(None),
    mod_passivemobs: Optional[str] = Form(None),
    mod_nomap: Optional[str] = Form(None),
    mod_reset_modifiers: Optional[str] = Form(None),
):
    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        headers = make_toast_headers("Permission denied: Configuration modification restricted to Administrators.", "error")
        cfg = load_config()
        available_worlds = list_available_worlds()
        return templates.TemplateResponse(
            request=request,
            name="partials/config_form.html",
            context={
                "config": cfg,
                "available_worlds": available_worlds,
                "message": "Permission denied: Admin role required.",
                "success": False,
                "user": user,
            },
            headers=headers,
        )

    new_cfg = {
        "server_name": server_name.strip(),
        "world_name": world_name.strip(),
        "password": password.strip(),
        "port": port.strip(),
        "playit_address": playit_address.strip() if playit_address else "",
        "auto_start_on_boot": auto_start_on_boot is not None,
        "auto_restart": auto_restart is not None,
        "modifiers": {
            "preset": (mod_preset or "").strip().lower(),
            "combat": (mod_combat or "").strip().lower(),
            "death_penalty": (mod_death_penalty or "").strip().lower(),
            "resources": (mod_resources or "").strip().lower(),
            "raids": (mod_raids or "").strip().lower(),
            "portals": (mod_portals or "").strip().lower(),
            "nobuildcost": mod_nobuildcost is not None,
            "playerevents": mod_playerevents is not None,
            "passivemobs": mod_passivemobs is not None,
            "nomap": mod_nomap is not None,
            "reset_modifiers": mod_reset_modifiers is not None,
        },
    }

    ok, msg = SERVER_MANAGER.update_config(new_cfg)
    cfg = load_config()
    available_worlds = list_available_worlds()
    toast_type = "success" if ok else "error"

    return templates.TemplateResponse(
        request=request,
        name="partials/config_form.html",
        context={
            "config": cfg,
            "available_worlds": available_worlds,
            "message": msg,
            "success": ok,
            "user": user,
        },
        headers=make_toast_headers(msg, toast_type),
    )


@app.get("/api/world/random-seed")
async def get_random_world_seed(request: Request):
    """Return a newly generated random 10-character Viking seed."""
    return {"seed": generate_random_seed()}


@app.post("/api/world/create", response_class=HTMLResponse)
async def create_new_world_endpoint(
    request: Request,
    new_world_name: str = Form(...),
    new_world_seed: Optional[str] = Form(""),
    backup_current: Optional[str] = Form(None),
    switch_active: Optional[str] = Form(None),
    restart_server: Optional[str] = Form(None),
):
    """Admin endpoint to create a new world with custom/random seed, pre-creation backup, and auto-switch."""
    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        headers = make_toast_headers("Permission denied: Creating worlds is restricted to Administrators.", "error")
        cfg = load_config()
        return templates.TemplateResponse(
            request=request,
            name="partials/config_form.html",
            context={
                "config": cfg,
                "available_worlds": list_available_worlds(),
                "message": "Permission denied: Admin role required.",
                "success": False,
                "user": user,
            },
            headers=headers,
        )

    clean_name = re.sub(r"[^a-zA-Z0-9_\- ]", "", new_world_name.strip())
    if not clean_name:
        headers = make_toast_headers("Invalid world name. Use letters, numbers, spaces, dashes, or underscores.", "error")
        cfg = load_config()
        return templates.TemplateResponse(
            request=request,
            name="partials/config_form.html",
            context={
                "config": cfg,
                "available_worlds": list_available_worlds(),
                "message": "Invalid world name.",
                "success": False,
                "user": user,
            },
            headers=headers,
        )

    clean_seed = new_world_seed.strip() if new_world_seed else generate_random_seed()
    do_backup = (backup_current is not None)
    do_switch = (switch_active is not None)
    do_restart = (restart_server is not None)

    cfg = load_config()
    current_world = cfg.get("world_name", "ValheimTest")
    was_running = (SERVER_MANAGER.state == "Running")

    # Step 1: Graceful shutdown if server is active to flush current world state
    if was_running and (do_switch or do_backup):
        print(f"[Manager] Gracefully stopping server for world creation/backup...")
        SERVER_MANAGER.stop_server()

    # Step 2: Backup current active world before switching
    backup_details = ""
    if do_backup:
        ok_b, msg_b, _ = create_backup(world_name=current_world)
        if ok_b:
            backup_details = f" • Pre-creation backup saved."
        else:
            print(f"[Manager] Warning: Pre-creation backup failed: {msg_b}")

    # Step 3: Create world metadata (.fwl) with seed
    ok_w, msg_w = create_world_metadata(world_name=clean_name, seed_name=clean_seed)
    if not ok_w:
        headers = make_toast_headers(msg_w, "error")
        if was_running and do_restart:
            SERVER_MANAGER.start_server()
        return templates.TemplateResponse(
            request=request,
            name="partials/config_form.html",
            context={
                "config": cfg,
                "available_worlds": list_available_worlds(),
                "message": msg_w,
                "success": False,
                "user": user,
            },
            headers=headers,
        )

    # Step 4: Switch active world if requested
    if do_switch:
        cfg["world_name"] = clean_name
        SERVER_MANAGER.update_config(cfg)

    # Step 5: Start server with new world if requested
    if do_restart or (was_running and do_switch):
        SERVER_MANAGER.start_server()

    success_msg = f"⚔️ New World '{clean_name}' created (Seed: '{clean_seed}'){backup_details}"
    cfg = load_config()
    available_worlds = list_available_worlds()
    headers = make_toast_headers(success_msg, "success")

    return templates.TemplateResponse(
        request=request,
        name="partials/config_form.html",
        context={
            "config": cfg,
            "available_worlds": available_worlds,
            "message": success_msg,
            "success": True,
            "user": user,
        },
        headers=headers,
    )


# ==========================================
# Backup Management Actions (RBAC Protected)
# ==========================================


@app.post("/api/backups/create", response_class=HTMLResponse)
async def backup_create(request: Request):
    user = get_current_user_from_request(request)
    if not user or user.get("role") not in ("admin", "operator"):
        headers = make_toast_headers("Permission denied: Backup creation restricted to Operators & Admins.", "error")
        backups = list_backups()
        return templates.TemplateResponse(
            request=request,
            name="partials/backup_table.html",
            context={"backups": backups, "user": user},
            headers=headers,
        )

    cfg = load_config()
    world_name = cfg.get("world_name", "world")
    ok, msg, _ = create_backup(world_name=world_name)
    backups = list_backups()
    toast_type = "success" if ok else "error"
    return templates.TemplateResponse(
        request=request,
        name="partials/backup_table.html",
        context={"backups": backups, "user": user},
        headers=make_toast_headers(msg, toast_type),
    )


@app.post("/api/backups/restore/{filename}", response_class=HTMLResponse)
async def backup_restore(request: Request, filename: str):
    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        headers = make_toast_headers("Permission denied: Backup restoration restricted to Administrators.", "error")
        backups = list_backups()
        return templates.TemplateResponse(
            request=request,
            name="partials/backup_table.html",
            context={"backups": backups, "user": user},
            headers=headers,
        )

    ok, msg = restore_backup(filename)
    backups = list_backups()
    toast_type = "success" if ok else "error"
    return templates.TemplateResponse(
        request=request,
        name="partials/backup_table.html",
        context={"backups": backups, "user": user},
        headers=make_toast_headers(msg, toast_type),
    )


@app.delete("/api/backups/delete/{filename}", response_class=HTMLResponse)
async def backup_delete(request: Request, filename: str):
    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        headers = make_toast_headers("Permission denied: Backup deletion restricted to Administrators.", "error")
        backups = list_backups()
        return templates.TemplateResponse(
            request=request,
            name="partials/backup_table.html",
            context={"backups": backups, "user": user},
            headers=headers,
        )

    ok, msg = delete_backup(filename)
    backups = list_backups()
    toast_type = "success" if ok else "error"
    return templates.TemplateResponse(
        request=request,
        name="partials/backup_table.html",
        context={"backups": backups, "user": user},
        headers=make_toast_headers(msg, toast_type),
    )


@app.get("/api/backups/download/{filename}")
async def backup_download(request: Request, filename: str):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = get_current_user_from_request(request)
    if not user or user.get("role") not in ("admin", "operator"):
        raise HTTPException(
            status_code=403,
            detail="Permission denied: Operator or Admin role required to download backups.",
        )

    file_path = get_backup_path(filename)
    if not file_path:
        raise HTTPException(status_code=404, detail="Backup file not found.")
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/zip",
    )


# ==========================================
# Real-Time SSE Log Streaming
# ==========================================


@app.get("/api/stream/logs")
async def stream_logs(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return StreamingResponse(
        log_stream_generator(request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==========================================
# JSON REST API (Compatibility)
# ==========================================


@app.get("/api/status")
async def api_status(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    status_data = SERVER_MANAGER.get_status_data()
    if user.get("role") != "admin":
        status_data = dict(status_data)
        status_data["player_sessions"] = []
    return status_data


@app.get("/api/config")
async def api_config(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return SERVER_MANAGER.config


@app.get("/api/backups")
async def api_backups(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return list_backups()


@app.get("/api/logs")
async def api_logs(request: Request, lines: int = 250):
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return Response(content=read_log_tail(num_lines=lines), media_type="text/plain")


@app.get("/api/server/history")
async def api_server_history(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    status = SERVER_MANAGER.get_status_data()
    return status.get("server_history", {})


@app.get("/api/server/history/log")
async def api_server_history_log(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not SERVER_HISTORY_LOG.exists():
        return Response(content="No server history log found.", media_type="text/plain")
    with open(SERVER_HISTORY_LOG, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    return Response(content=content, media_type="text/plain")
