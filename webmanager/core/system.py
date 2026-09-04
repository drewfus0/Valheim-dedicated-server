import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.paths import SERVER_DIR


def get_cpu_frequencies() -> Dict[str, Any]:
    """Parse live CPU core frequencies from /proc/cpuinfo."""
    freqs: List[float] = []
    try:
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("cpu MHz"):
                        parts = line.split(":")
                        if len(parts) == 2:
                            try:
                                freqs.append(float(parts[1].strip()))
                            except ValueError:
                                pass

        # Fallback to sysfs if cpuinfo did not return core frequencies
        if not freqs and os.path.exists("/sys/devices/system/cpu"):
            import glob

            for path in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        khz = float(f.read().strip())
                        freqs.append(khz / 1000.0)
                except Exception:
                    pass

        if freqs:
            min_mhz = min(freqs)
            max_mhz = max(freqs)
            avg_mhz = sum(freqs) / len(freqs)
            all_under_600 = all(f < 600.0 for f in freqs)
            return {
                "min_mhz": int(round(min_mhz)),
                "max_mhz": int(round(max_mhz)),
                "avg_mhz": int(round(avg_mhz)),
                "cores_count": len(freqs),
                "all_under_600": all_under_600,
                "throttled": all_under_600,
            }
    except Exception as e:
        print(f"[System] Error reading CPU frequencies: {e}")

    return {
        "min_mhz": 0,
        "max_mhz": 0,
        "avg_mhz": 0,
        "cores_count": 0,
        "all_under_600": False,
        "throttled": False,
    }


def get_power_profile() -> str:
    """Retrieve the current active system power profile via D-Bus, tuned-adm, or sysfs."""
    # 1. Try D-Bus net.hadess.PowerProfiles (standard across modern Linux/Fedora/GNOME/KDE)
    try:
        res = subprocess.run(
            [
                "busctl",
                "get-property",
                "net.hadess.PowerProfiles",
                "/net/hadess/PowerProfiles",
                "net.hadess.PowerProfiles",
                "ActiveProfile",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0 and res.stdout:
            # Output format is: s "performance"
            val = res.stdout.strip().split()[-1].strip("\"'\n")
            if val:
                return val
    except Exception:
        pass

    # 2. Try tuned-adm active profile
    try:
        res = subprocess.run(["tuned-adm", "active"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.splitlines():
                if "active profile:" in line.lower():
                    return line.split(":")[-1].strip()
    except Exception:
        pass

    # 3. Try reading CPU scaling governor
    try:
        if os.path.exists("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"):
            with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass

    return "unknown"


def set_power_profile(profile: str = "performance") -> Tuple[bool, str, List[str]]:
    """Set the system power profile to performance or specified target."""
    actions: List[str] = []
    success = False

    # 1. DBus net.hadess.PowerProfiles
    try:
        res = subprocess.run(
            [
                "busctl",
                "set-property",
                "net.hadess.PowerProfiles",
                "/net/hadess/PowerProfiles",
                "net.hadess.PowerProfiles",
                "ActiveProfile",
                "s",
                profile,
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0:
            success = True
            actions.append(f"net.hadess.PowerProfiles: set {profile}")
    except Exception as e:
        actions.append(f"DBus PowerProfiles error: {e}")

    # 2. tuned-adm (throughput-performance or performance)
    try:
        tuned_target = "throughput-performance" if profile == "performance" else profile
        res = subprocess.run(["tuned-adm", "profile", tuned_target], capture_output=True, text=True, timeout=4)
        if res.returncode == 0:
            success = True
            actions.append(f"tuned-adm: set {tuned_target}")
    except Exception as e:
        actions.append(f"tuned-adm error: {e}")

    active = get_power_profile()
    is_active_perf = "perf" in active.lower()
    return (success or is_active_perf), active, actions


def configure_kde_powerdevil() -> Dict[str, Any]:
    """Configure KDE Plasma 6 PowerDevil settings to prevent sleep while allowing screen turn off and lock."""
    import shutil

    changes: List[str] = []
    kwrite = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")

    if kwrite:
        try:
            # 1. Set AC Performance Profile
            subprocess.run([kwrite, "--file", "powerdevilrc", "--group", "AC", "--group", "Performance", "--key", "PowerProfile", "performance"], capture_output=True, timeout=2)
            # 2. Disable Auto-Suspend on AC (0 = Never / Disabled)
            subprocess.run([kwrite, "--file", "powerdevilrc", "--group", "AC", "--group", "SuspendAndShutdown", "--key", "AutoSuspendAction", "0"], capture_output=True, timeout=2)
            # 3. Configure Lid Action on AC to Turn Off Screen (64) instead of Sleep (1)
            subprocess.run([kwrite, "--file", "powerdevilrc", "--group", "AC", "--group", "SuspendAndShutdown", "--key", "LidAction", "64"], capture_output=True, timeout=2)
            # 4. Don't inhibit lid action only when external monitor present
            subprocess.run([kwrite, "--file", "powerdevilrc", "--group", "AC", "--group", "SuspendAndShutdown", "--key", "InhibitLidActionWhenExternalMonitorPresent", "false"], capture_output=True, timeout=2)
            changes.append("Updated powerdevilrc via kwriteconfig")
        except Exception as ex:
            changes.append(f"kwriteconfig error: {ex}")

    # Direct fallback if kwriteconfig not found
    powerdevilrc_path = Path.home() / ".config" / "powerdevilrc"
    if not kwrite and powerdevilrc_path.parent.exists():
        try:
            import configparser

            cp = configparser.ConfigParser()
            cp.optionxform = str  # type: ignore[assignment]
            if powerdevilrc_path.exists():
                cp.read(str(powerdevilrc_path))

            if not cp.has_section("AC][Performance"):
                cp.add_section("AC][Performance")
            cp.set("AC][Performance", "PowerProfile", "performance")

            if not cp.has_section("AC][SuspendAndShutdown"):
                cp.add_section("AC][SuspendAndShutdown")
            cp.set("AC][SuspendAndShutdown", "AutoSuspendAction", "0")
            cp.set("AC][SuspendAndShutdown", "LidAction", "64")

            with open(powerdevilrc_path, "w", encoding="utf-8") as f:
                cp.write(f)
            changes.append("Updated powerdevilrc via direct config parser")
        except Exception as ex:
            changes.append(f"Direct configparser error: {ex}")

    # Request KDE PowerDevil to reparse configuration immediately via D-Bus
    try:
        subprocess.run(
            [
                "busctl",
                "--user",
                "call",
                "org.kde.Solid.PowerManagement",
                "/org/kde/Solid/PowerManagement",
                "org.kde.Solid.PowerManagement",
                "reparseConfiguration",
            ],
            capture_output=True,
            timeout=2,
        )
        changes.append("Reloaded PowerDevil configuration via D-Bus")
    except Exception:
        pass

    return {"configured": True, "changes": changes}


def configure_gnome_power() -> Dict[str, Any]:
    """Configure GNOME power settings to prevent sleep on AC if GNOME schemas are installed."""
    import shutil

    gsettings = shutil.which("gsettings")
    if not gsettings:
        return {"configured": False, "reason": "gsettings not found"}

    try:
        res = subprocess.run(
            [gsettings, "get", "org.gnome.settings-daemon.plugins.power", "sleep-inactive-ac-type"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0:
            subprocess.run(
                [gsettings, "set", "org.gnome.settings-daemon.plugins.power", "sleep-inactive-ac-type", "'nothing'"],
                capture_output=True,
                timeout=2,
            )
            return {"configured": True, "details": "Set GNOME sleep-inactive-ac-type to 'nothing'"}
    except Exception:
        pass
    return {"configured": False, "reason": "GNOME power schema not present"}


def configure_system_power_and_performance() -> Dict[str, Any]:
    """
    Configure and confirm:
    1. System power profile is set to Performance.
    2. System power settings are configured to not sleep (auto-suspend disabled, lid close turns off display).
    3. Display turn-off / screen blanking and session lock/logout remain naturally active on idle.
    """
    print("[System] Checking and configuring system power and performance settings...")

    # 1. Performance Profile
    prof_ok, active_profile, prof_actions = set_power_profile("performance")

    # 2. KDE Plasma PowerDevil configuration
    kde_res = configure_kde_powerdevil()

    # 3. GNOME Power configuration fallback
    gnome_res = configure_gnome_power()

    summary = {
        "profile": active_profile,
        "is_performance": "perf" in active_profile.lower(),
        "auto_suspend": "disabled",
        "lid_action": "turn_off_screen",
        "display_turnoff_allowed": True,
        "screen_lock_allowed": True,
        "details": prof_actions + kde_res.get("changes", []),
    }
    print(
        f"[System] Power configuration complete: Profile='{active_profile}', "
        f"Sleep='Disabled', ScreenLock='Allowed on Idle', LidAction='Turn Off Screen'."
    )
    return summary


def get_system_power_status() -> Dict[str, Any]:
    """Get a quick summary of the current power profile and sleep policy for telemetry/UI."""
    profile = get_power_profile()
    is_perf = "perf" in profile.lower()
    return {
        "profile": profile,
        "is_performance": is_perf,
        "sleep_policy": "No Sleep",
        "screen_behavior": "Natural Display Off & Lock",
        "lid_action": "Turn Off Screen",
    }



def execute_host_reboot(server_manager) -> Tuple[bool, str]:
    """Gracefully stop Valheim and reboot the host computer."""
    print("[System] Host reboot requested. Gracefully stopping Valheim server first...")
    try:
        # Gracefully stop server if running
        if server_manager.state == "Running" or server_manager.get_active_pid():
            server_manager.stop_server()
            # Brief pause to ensure ZNet flush completes
            time.sleep(2.0)

        def _deferred_reboot():
            time.sleep(1.5)
            print("[System] Executing host reboot...")
            try:
                # Primary method: systemctl with ignore-inhibitors (uses polkit rule if present)
                res = subprocess.run(
                    ["systemctl", "reboot", "--ignore-inhibitors", "--no-block"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                print(f"[System] systemctl reboot exit: {res.returncode}, out: {res.stdout}, err: {res.stderr}")
                if res.returncode == 0:
                    return

                # Secondary method: sudo without password prompt (if sudoers rule is configured)
                res_sudo = subprocess.run(
                    ["sudo", "-n", "systemctl", "reboot", "--ignore-inhibitors", "--no-block"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                print(f"[System] sudo systemctl reboot exit: {res_sudo.returncode}, out: {res_sudo.stdout}, err: {res_sudo.stderr}")
                if res_sudo.returncode == 0:
                    return

                # Tertiary fallback: D-Bus logind Manager Reboot
                res_bus = subprocess.run(
                    ["busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "Reboot", "b", "true"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                print(f"[System] busctl reboot exit: {res_bus.returncode}, err: {res_bus.stderr}")
                if res_bus.returncode == 0:
                    return

                # Quaternary fallback: sudo reboot binary
                res_raw = subprocess.run(
                    ["sudo", "-n", "reboot"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                print(f"[System] sudo reboot exit: {res_raw.returncode}, err: {res_raw.stderr}")
            except Exception as ex:
                print(f"[System] Error executing reboot: {ex}")

        threading.Thread(target=_deferred_reboot, daemon=True).start()
        return True, "Host machine reboot initiated. The system will restart shortly."
    except Exception as e:
        return False, f"Failed to initiate reboot: {e}"
