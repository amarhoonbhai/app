import os
import sys
import re
import json
import subprocess
import tempfile
import shutil
from datetime import datetime, timedelta, time
from typing import Dict, Any

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

from telethon.sync import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
)
from colorama import Fore, Style, init

# ---------- Init ----------
init(autoreset=True)

SESSIONS_DIR = "sessions"
USERS_DIR    = "users"
USERS_FILE   = "users.json"

AUTONIGHT_FILE = "autonight.json"
AUTONIGHT_DEFAULT = {
    "enabled": True,
    "start": "00:00",
    "end": "06:00",
    "tz": "Asia/Kolkata"
}

def atomic_save_json(path: str, data: Any) -> bool:
    """Save JSON data to a file atomically using a temporary file."""
    temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.replace(temp_path, path)
        except OSError:
            shutil.move(temp_path, path)
        return True
    except Exception as e:
        print(Fore.RED + f"  [!] Failed to save JSON to {path}: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False


RUNNER_CMD = ["python3", "runner.py"]
RUNNER_LOG = "runner.log"

# ---------- Files & Config ----------
def ensure_dirs():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(USERS_DIR, exist_ok=True)
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
    if not os.path.exists(AUTONIGHT_FILE):
        with open(AUTONIGHT_FILE, "w", encoding="utf-8") as f:
            json.dump(AUTONIGHT_DEFAULT, f, ensure_ascii=False, indent=2)

def load_users() -> Dict[str, Any]:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users: Dict[str, Any]) -> None:
    atomic_save_json(USERS_FILE, users)

def save_user_config(phone: str, data: Dict[str, Any]) -> None:
    user_config = {
        "name": data["name"],
        "phone": phone,
        "api_id": int(data["api_id"]),
        "api_hash": data["api_hash"],
        "cycle_delay_min": 7,
        "msg_delay_sec": 30,
        "groups": [],

        "plan_expiry": "Lifetime"
    }

    atomic_save_json(os.path.join(USERS_DIR, f"{phone}.json"), user_config)

# ---------- UI Helpers ----------
def list_users(users: Dict[str, Any]) -> None:
    if not users:
        print(Fore.YELLOW + "  [!] No users logged in yet.")
        return
    print(Fore.CYAN + Style.BRIGHT + "\n  ⭐ Registered Premium Users:")
    print(Fore.CYAN + "  " + "─" * 45)
    for i, (phone, data) in enumerate(users.items(), 1):
        name = data.get('name', 'Unknown')
        print(f"  {Fore.WHITE}{i:<2} {Fore.GREEN}{name:<15} {Fore.WHITE}| {Fore.YELLOW}{phone:<15} {Fore.BLUE}| Lifetime")
    print(Fore.CYAN + "  " + "─" * 45)
    input(Fore.WHITE + "\n  Press Enter to return to menu...")


# ---------- Runner management ----------
def is_runner_running() -> bool:
    pid_file = "runner.pid"
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file, "r") as f:
            pid = int(f.read().strip())
    except Exception:
        return False

    if pid <= 0:
        return False

    if os.name == 'nt':
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            err = ctypes.windll.kernel32.GetLastError()
            return err == 5
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

def stop_runner():
    pid_file = "runner.pid"
    if not os.path.exists(pid_file):
        print(Fore.YELLOW + "  [!] No PID file found. Engine might not be running.")
        return
    try:
        with open(pid_file, "r") as f:
            pid = int(f.read().strip())
    except Exception:
        print(Fore.RED + "  [!] Failed to read PID file.")
        return

    print(Fore.YELLOW + f"  [🔁] Stopping background engine (PID: {pid})...")
    try:
        if os.name == 'nt':
            subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except Exception as e:
        print(Fore.RED + f"  [!] Failed to stop engine: {e}")
    finally:
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except Exception:
            pass

def start_runner_if_needed():
    if is_runner_running():
        print(Fore.GREEN + "  [✔] Background engine is already running.")
        return
    
    # Remove stale pid file if exists
    if os.path.exists("runner.pid"):
        try:
            os.remove("runner.pid")
        except Exception:
            pass

    import sys
    python_cmd = sys.executable
    if os.name == "nt":
        # Windows: Start in a new console window so logs are visible
        try:
            subprocess.Popen(
                [python_cmd, "runner.py"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            print(Fore.CYAN + f"  [🔁] Background engine started in a NEW window.")
        except Exception as e:
            print(Fore.RED + f"  [!] Failed to start engine: {e}")
    else:
        # Linux: Start in background with log file
        with open(RUNNER_LOG, "ab") as logf:
            subprocess.Popen(
                [python_cmd, "runner.py"],
                stdout=logf,
                stderr=logf,
                start_new_session=True
            )
        print(Fore.CYAN + f"  [🔁] Background engine started successfully.")


# ---------- Auto-Night editor ----------
def _parse_hhmm(s: str) -> time:
    s = s.strip()
    if re.fullmatch(r"\d{1,2}", s):
        h = int(s)
        if not (0 <= h <= 23):
            raise ValueError("Hour must be 0..23")
        return time(h, 0)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        raise ValueError("Time must be HH or HH:MM (24h)")
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        raise ValueError("Invalid time")
    return time(h, mm)

def show_autonight():
    with open(AUTONIGHT_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    state = f"{Fore.GREEN}ACTIVE ✅" if cfg.get("enabled", True) else f"{Fore.RED}DISABLED ❌"
    print(Fore.MAGENTA + Style.BRIGHT + "\n  🌙 Auto-Night Configuration")
    print(Fore.WHITE + f"  Current Status : {state}")
    print(Fore.WHITE + f"  Quiet Window   : {Fore.YELLOW}{cfg.get('start','00:00')} → {cfg.get('end','06:00')}")
    print(Fore.WHITE + f"  Timezone       : {Fore.BLUE}{cfg.get('tz','Asia/Kolkata')}")
    print(Fore.CYAN + "  " + "─" * 40)

def edit_autonight():
    with open(AUTONIGHT_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    print(Style.BRIGHT + "\n  Edit Auto-Night (Press Enter to skip)")
    en = input(f"  Enable? [y/n] (Current: {'on' if cfg.get('enabled',True) else 'off'}): ").strip().lower()
    if en in {"y", "yes", "on"}:
        cfg["enabled"] = True
    elif en in {"n", "no", "off"}:
        cfg["enabled"] = False

    start_in = input(f"  Start HH[:MM] (Current {cfg.get('start','00:00')}): ").strip()
    if start_in:
        try:
            t = _parse_hhmm(start_in)
            cfg["start"] = f"{t.hour:02d}:{t.minute:02d}"
        except ValueError as e:
            print(Fore.RED + f"  [!] Invalid start time: {e}")

    end_in = input(f"  End HH[:MM] (Current {cfg.get('end','06:00')}): ").strip()
    if end_in:
        try:
            t = _parse_hhmm(end_in)
            cfg["end"] = f"{t.hour:02d}:{t.minute:02d}"
        except ValueError as e:
            print(Fore.RED + f"  [!] Invalid end time: {e}")

    tz_in = input(f"  Timezone (Current {cfg.get('tz','Asia/Kolkata')}): ").strip()
    if tz_in:
        cfg["tz"] = tz_in

    atomic_save_json(AUTONIGHT_FILE, cfg)
    print(Fore.GREEN + "  [✔] Auto-Night settings updated.")
    show_autonight()

# ---------- Login / Delete ----------
def login_new_user(users: Dict[str, Any]):
    print(Fore.CYAN + "\n  [ NEW SESSION LOGIN ]")
    name = input("  Account Name: ").strip()
    api_id = input("  API ID: ").strip()
    api_hash = input("  API HASH: ").strip()
    phone_raw = input("  Phone (with +country): ").strip()

    if not api_id.isdigit():
        print(Fore.RED + "  [!] API ID must be numeric.")
        return

    phone = re.sub(r'[^\d+]', '', phone_raw)

    session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
    client = TelegramClient(session_path, int(api_id), api_hash)

    try:
        client.connect()
        if not client.is_user_authorized():
            try:
                client.send_code_request(phone)
            except FloodWaitError as e:
                print(Fore.RED + f"  [!] FloodWait: {e.seconds}s remaining.")
                return
            except Exception as e:
                print(Fore.RED + f"  [!] Failed to send verification code request.")
                print(Fore.RED + f"      Details: {e}")
                return

            code = input("  Enter Telegram Code: ").strip()
            try:
                client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = input("  2FA Password: ").strip()
                try:
                    client.sign_in(password=password)
                except Exception as e:
                    print(Fore.RED + f"  [!] 2FA Sign-in Failed: {e}")
                    return
            except Exception as e:
                print(Fore.RED + f"  [!] Sign-in Failed: {e}")
                return

        me = client.get_me()
        user_display = (getattr(me, "first_name", "") or getattr(me, "username", "") or str(me.id))
        print(Fore.GREEN + f"  [✔] Logged in as: {user_display}")

        users[phone] = {"name": name or user_display, "api_id": int(api_id), "api_hash": api_hash}
        save_users(users)
        save_user_config(phone, users[phone])

    finally:
        client.disconnect()
    
    start_runner_if_needed()

def delete_user(users: Dict[str, Any]):
    phone = input("  Phone number to delete: ").strip()
    if phone in users:
        session_file = os.path.join(SESSIONS_DIR, f"{phone}.session")
        config_file = os.path.join(USERS_DIR, f"{phone}.json")
        for f in [session_file, config_file]:
            if os.path.exists(f): os.remove(f)
        users.pop(phone)
        save_users(users)
        print(Fore.RED + f"  [✖] Account {phone} removed.")
    else:
        print(Fore.YELLOW + "  [!] Account not found.")

def check_account_health(users: Dict[str, Any]):
    if not users:
        print(Fore.YELLOW + "  [!] No sessions found.")
        return
    
    print(Fore.CYAN + "\n  [ VERIFYING SESSIONS ]")
    print(Fore.CYAN + "  " + "─" * 40)
    for phone, data in users.items():
        session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
        if not os.path.exists(session_path):
            print(Fore.RED + f"  ✖ {phone:<15} | File Missing")
            continue
            
        client = TelegramClient(session_path, data["api_id"], data["api_hash"])
        try:
            client.connect()
            if not client.is_user_authorized():
                print(Fore.RED + f"  ✖ {phone:<15} | Session Revoked")
            else:
                print(Fore.GREEN + f"  ✔ {phone:<15} | Healthy")
        except Exception as e:
            print(Fore.RED + f"  ✖ {phone:<15} | Error: {type(e).__name__}")
        finally:
            client.disconnect()
    print(Fore.CYAN + "  " + "─" * 40)
    input(Fore.WHITE + "\n  Scan complete. Press Enter...")

# ---------- Main Menu ----------
def start():
    ensure_dirs()
    while True:
        users = load_users()
        total_users = len(users)
        runner_status = f"{Fore.GREEN}RUNNING" if is_runner_running() else f"{Fore.RED}STOPPED"
        
        banner = f"""
{Fore.CYAN}{Style.BRIGHT}╔══════════════════════════════════════════════════════╗
║        {Fore.YELLOW}TELETHON MULTI-USER MANAGER V5 ELITE{Fore.CYAN}          ║
║           {Fore.GREEN}Lifetime Premium Access Enabled{Fore.CYAN}            ║
╠══════════════════════════════════════════════════════╣
║  {Fore.WHITE}Registered Sessions: {Fore.YELLOW}{total_users:<2}          {Fore.WHITE}Engine: {runner_status}{Fore.CYAN}  ║
╚══════════════════════════════════════════════════════╝{Style.RESET_ALL}"""

        print(banner)
        print(Fore.WHITE + Style.BRIGHT + "  [ MAIN CONTROL PANEL ]\n")
        print(f"  {Fore.CYAN}1.{Fore.WHITE} List Registered Users")
        print(f"  {Fore.CYAN}2.{Fore.WHITE} {Fore.GREEN}Login New Premium Session")
        print(f"  {Fore.CYAN}3.{Fore.WHITE} {Fore.RED}Delete User Data")
        print(f"  {Fore.CYAN}─" * 25)
        print(f"  {Fore.CYAN}4.{Fore.WHITE} View Auto-Night Status")
        print(f"  {Fore.CYAN}5.{Fore.WHITE} Config Auto-Night Mode")
        print(f"  {Fore.CYAN}─" * 25)
        print(f"  {Fore.CYAN}6.{Fore.WHITE} {Fore.YELLOW}RESTART BACKGROUND ENGINE")
        print(f"  {Fore.CYAN}7.{Fore.WHITE} {Fore.BLUE}Account Health Verification")
        print(f"  {Fore.CYAN}8.{Fore.WHITE} Close Manager")
        
        choice = input(Fore.YELLOW + "\n  ❯ Select an option [1-8]: " + Style.RESET_ALL).strip()

        if choice == '1':
            list_users(users)
        elif choice == '2':
            login_new_user(users)
        elif choice == '3':
            delete_user(users)
        elif choice == '4':
            show_autonight()
        elif choice == '5':
            edit_autonight()
        elif choice == '6':
            stop_runner()
            import time as pytime
            pytime.sleep(1.5)
            start_runner_if_needed()
        elif choice == '7':
            check_account_health(users)
        elif choice == '8':
            print(Fore.CYAN + "\n  Goodbye!")
            break
        else:
            print(Fore.RED + "  [!] Invalid selection.")

if __name__ == "__main__":
    try:
        start()
    except KeyboardInterrupt:
        print(Fore.CYAN + "\n  Exiting...")
