import datetime
import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths import LOGIN_HISTORY_FILE, USERS_FILE

SESSION_COOKIE_NAME = "valheim_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 days
MAX_LOGIN_HISTORY = 100

VALID_ROLES = {"admin", "operator", "viewer"}

_LOCK = threading.Lock()
_SESSIONS: Dict[str, Dict[str, Any]] = {}


def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 iterations."""
    if salt is None:
        salt = secrets.token_bytes(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return salt.hex(), pw_hash.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Verify password against stored salt and hash using constant-time comparison."""
    try:
        salt = bytes.fromhex(salt_hex)
        computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        return secrets.compare_digest(computed.hex(), hash_hex)
    except Exception:
        return False


def load_users() -> Dict[str, Any]:
    """Load users from JSON file, initializing default admin if missing."""
    with _LOCK:
        if not USERS_FILE.exists():
            default_salt, default_hash = hash_password("admin")
            initial_data = {
                "users": [
                    {
                        "username": "admin",
                        "salt": default_salt,
                        "hash": default_hash,
                        "role": "admin",
                        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                ]
            }
            tmp_path = USERS_FILE.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(initial_data, f, indent=4)
            os.replace(tmp_path, USERS_FILE)
            print("[Auth] Initial admin account provisioned (Username: admin, Password: admin)")
            return initial_data

        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Auth] Error reading {USERS_FILE}: {e}")
            return {"users": []}


def save_users(data: Dict[str, Any]) -> None:
    """Atomically save user data to disk."""
    with _LOCK:
        tmp_path = USERS_FILE.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_path, USERS_FILE)


def list_users() -> List[Dict[str, Any]]:
    """Return list of public user info (excluding hashes)."""
    data = load_users()
    users = []
    for u in data.get("users", []):
        users.append(
            {
                "username": u.get("username"),
                "role": u.get("role", "viewer"),
                "created_at": u.get("created_at", ""),
            }
        )
    return users


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Validate credentials and return user info dictionary without secrets."""
    username = username.strip()
    data = load_users()
    for u in data.get("users", []):
        if u.get("username", "").lower() == username.lower():
            if verify_password(password, u.get("salt", ""), u.get("hash", "")):
                return {
                    "username": u.get("username"),
                    "role": u.get("role", "viewer"),
                    "created_at": u.get("created_at", ""),
                }
            break
    return None


def create_user(username: str, password: str, role: str) -> Dict[str, Any]:
    """Create a new user. Raises ValueError if validation fails."""
    username = username.strip()
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters long.")
    if not username.isalnum() and "_" not in username and "-" not in username:
        raise ValueError("Username can only contain alphanumeric characters, underscores, or hyphens.")
    if len(password) < 4:
        raise ValueError("Password must be at least 4 characters long.")
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")

    data = load_users()
    users = data.get("users", [])
    for u in users:
        if u.get("username", "").lower() == username.lower():
            raise ValueError(f"User '{username}' already exists.")

    salt, pw_hash = hash_password(password)
    new_user = {
        "username": username,
        "salt": salt,
        "hash": pw_hash,
        "role": role,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    users.append(new_user)
    data["users"] = users
    save_users(data)

    return {
        "username": new_user["username"],
        "role": new_user["role"],
        "created_at": new_user["created_at"],
    }


def update_password(username: str, new_password: str) -> bool:
    """Update a user's password."""
    if len(new_password) < 4:
        raise ValueError("Password must be at least 4 characters long.")

    data = load_users()
    users = data.get("users", [])
    found = False
    for u in users:
        if u.get("username", "").lower() == username.lower():
            salt, pw_hash = hash_password(new_password)
            u["salt"] = salt
            u["hash"] = pw_hash
            found = True
            break

    if not found:
        raise ValueError(f"User '{username}' not found.")

    data["users"] = users
    save_users(data)
    return True


def update_user_role(username: str, new_role: str) -> bool:
    """Update a user's role, preventing removal of the last admin."""
    if new_role not in VALID_ROLES:
        raise ValueError(f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")

    data = load_users()
    users = data.get("users", [])
    
    # Check if target is admin and we are demoting them
    admin_count = sum(1 for u in users if u.get("role") == "admin")
    found_user = None
    for u in users:
        if u.get("username", "").lower() == username.lower():
            found_user = u
            break

    if not found_user:
        raise ValueError(f"User '{username}' not found.")

    if found_user.get("role") == "admin" and new_role != "admin" and admin_count <= 1:
        raise ValueError("Cannot demote the last remaining administrator.")

    found_user["role"] = new_role
    data["users"] = users
    save_users(data)
    return True


def delete_user(username: str) -> bool:
    """Delete a user, preventing deletion of the last admin."""
    data = load_users()
    users = data.get("users", [])
    
    admin_count = sum(1 for u in users if u.get("role") == "admin")
    user_to_delete = None
    for u in users:
        if u.get("username", "").lower() == username.lower():
            user_to_delete = u
            break

    if not user_to_delete:
        raise ValueError(f"User '{username}' not found.")

    if user_to_delete.get("role") == "admin" and admin_count <= 1:
        raise ValueError("Cannot delete the last remaining administrator.")

    data["users"] = [u for u in users if u.get("username", "").lower() != username.lower()]
    save_users(data)
    return True


# ==========================================
# Session Management
# ==========================================


def create_session(user: Dict[str, Any]) -> str:
    """Generate a secure session token for an authenticated user."""
    cleanup_expired_sessions()
    token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.timezone.utc).timestamp() + SESSION_TTL_SECONDS
    with _LOCK:
        _SESSIONS[token] = {
            "username": user["username"],
            "role": user["role"],
            "expires_at": expires_at,
        }
    return token


def get_session_user(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve active user from session token if valid and not expired."""
    if not token:
        return None
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    with _LOCK:
        sess = _SESSIONS.get(token)
        if not sess:
            return None
        if sess["expires_at"] < now:
            del _SESSIONS[token]
            return None
        # Slide expiration forward on activity
        sess["expires_at"] = now + SESSION_TTL_SECONDS
        return {
            "username": sess["username"],
            "role": sess["role"],
        }


def destroy_session(token: Optional[str]) -> None:
    """Destroy session token upon logout."""
    if not token:
        return
    with _LOCK:
        _SESSIONS.pop(token, None)


def cleanup_expired_sessions() -> None:
    """Remove expired sessions from in-memory store."""
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    with _LOCK:
        expired = [t for t, s in _SESSIONS.items() if s["expires_at"] < now]
        for t in expired:
            del _SESSIONS[t]


# ==========================================
# Login Activity & Audit Logging
# ==========================================


def parse_user_agent(ua: Optional[str]) -> str:
    """Format user agent into a clean Client (Platform) string."""
    if not ua or ua == "Unknown":
        return "Unknown Client"
    if "curl" in ua.lower():
        return "cURL CLI"
    if "python" in ua.lower():
        return "Python API"

    browser = "Browser"
    if "Firefox/" in ua:
        browser = "Firefox"
    elif "Edg/" in ua or "Edge/" in ua:
        browser = "Edge"
    elif "Chrome/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"

    os_name = "Device"
    if "Linux" in ua:
        os_name = "Linux"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Macintosh" in ua or "Mac OS" in ua:
        os_name = "macOS"
    elif "Android" in ua:
        os_name = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        os_name = "iOS"

    return f"{browser} ({os_name})"


def load_login_history() -> List[Dict[str, Any]]:
    """Load login history log from disk."""
    with _LOCK:
        if not LOGIN_HISTORY_FILE.exists():
            return []
        try:
            with open(LOGIN_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("history", [])
        except Exception as e:
            print(f"[Auth] Error reading {LOGIN_HISTORY_FILE}: {e}")
            return []


def save_login_history(history: List[Dict[str, Any]]) -> None:
    """Atomically save login history log to disk, capped to MAX_LOGIN_HISTORY entries."""
    with _LOCK:
        try:
            data = {"history": history[:MAX_LOGIN_HISTORY]}
            tmp_path = LOGIN_HISTORY_FILE.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, LOGIN_HISTORY_FILE)
        except Exception as e:
            print(f"[Auth] Error saving {LOGIN_HISTORY_FILE}: {e}")


def record_login_event(
    username: str,
    status: str,  # "success", "failed", "registered"
    ip: str = "127.0.0.1",
    user_agent: str = "Unknown",
    role: Optional[str] = None,
    failure_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a login or authentication attempt to the audit log."""
    history = load_login_history()
    now = datetime.datetime.now()
    entry = {
        "id": secrets.token_hex(6),
        "username": username.strip() or "Anonymous",
        "role": role or "viewer",
        "status": status,
        "ip": ip or "127.0.0.1",
        "user_agent": parse_user_agent(user_agent),
        "raw_user_agent": user_agent or "",
        "failure_reason": failure_reason,
        "timestamp": now.strftime("%m/%d/%Y %H:%M:%S"),
        "iso_timestamp": now.isoformat(),
    }
    # Prepend newest first
    history.insert(0, entry)
    save_login_history(history)
    return entry


def get_login_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Return recent login audit records, seeding an initial entry if brand new."""
    history = load_login_history()
    if not history and not LOGIN_HISTORY_FILE.exists():
        # Seed initial baseline record for the active admin
        record_login_event(
            username="admin",
            status="success",
            ip="127.0.0.1",
            user_agent="Browser (Linux)",
            role="admin",
        )
        history = load_login_history()
    return history[:limit]


def clear_login_history() -> None:
    """Clear all recorded login history."""
    save_login_history([])
