#!/usr/bin/env python3
import os
import re
import json
import subprocess
from datetime import datetime, timedelta, time
from typing import Dict, Any

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
    "start": "23:00",
    "end": "07:00",
    "tz": "Asia/Kolkata",
}

RUNNER_CMD = ["python3", "runner.py"]
RUNNER_LOG = "runner.log"


# ---------- Files & Config ----------
def ensure_dirs() -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(USERS_DIR, exist_ok=True)
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
    if not os.path.exists(AUTONIGHT_FILE):
        with open(AUTONIGHT_FILE, "w", encoding="utf-8") as f:
            json.dump(AUTONIGHT_DEFAULT, f, ensure_ascii=False, indent=2)


def load_users() -> Dict[str, Any]:
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            return {str(k): v for k, v in data.items()}
    except Exception:
        return {}


def save_users(users: Dict[str, Any]) -> None:
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def save_user_config(phone: str, data: Dict[str, Any]) -> Dict[str, Any]:
    user_config = {
        "name": data["name"],
        "phone": phone,
        "api_id": int(data["api_id"]),
        "api_hash": data["api_hash"],
        # defaults for runner.py
        "cycle_delay_min": data.get("cycle_delay_min", 15),
        "msg_delay_sec": data.get("msg_delay_sec", 5),
        "groups": data.get("groups", []),
        # simple 30-day plan by default
        "plan_expiry": (datetime.now() + timedelta(days=30)).isoformat(),
    }
    with open(os.path.join(USERS_DIR, f"{phone}.json"), "w", encoding="utf-8") as f:
        json.dump(user_config, f, ensure_ascii=False, indent=2)
    return user_config


# ---------- UI Helpers ----------
def _format_plan(phone: str) -> str:
    """
    Used in list_users() to show plan info.
    """
    cfg_path = os.path.join(USERS_DIR, f"{phone}.json")
    if not os.path.exists(cfg_path):
        return "no config"
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        exp = cfg.get("plan_expiry")
        if not exp:
            return "∞"
        try:
            exp_dt = datetime.fromisoformat(exp)
        except Exception:
            return str(exp)
        now = datetime.now(exp_dt.tzinfo) if exp_dt.tzinfo else datetime.now()
        delta = exp_dt - now
        days = delta.days
        if days >= 0:
            return f"till {exp_dt.date()} ({days}d left)"
        else:
            return f"expired {exp_dt.date()}"
    except Exception:
        return "?"


def list_users(users: Dict[str, Any]) -> None:
    if not users:
        print(Fore.YELLOW + "No users logged in yet.")
        return

    print(Style.BRIGHT + Fore.CYAN + "Logged in users:")
    for phone, data in users.items():
        name = data.get("name", "?")
        api_id = data.get("api_id")
        plan = _format_plan(phone)
        print(f"- {name} ({phone})  | api_id={api_id}  | plan={plan}")


# ---------- Runner management ----------
def is_runner_running() -> bool:
    try:
        # Check if any python process with runner.py is alive
        out = subprocess.run(
            ["pgrep", "-af", "runner.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return bool(out.stdout.strip())
    except Exception:
        # Fallback: naive ps scan
        ps = subprocess.run(["ps", "aux"], stdout=subprocess.PIPE, text=True)
        return "runner.py" in ps.stdout


def start_runner_if_needed() -> None:
    if is_runner_running():
        print(Fore.GREEN + "[▶] runner.py already running.")
        return

    with open(RUNNER_LOG, "ab") as logf:
        # Start detached with nohup-like behavior
        subprocess.Popen(
            RUNNER_CMD,
            stdout=logf,
            stderr=logf,
            start_new_session=True,
        )
    print(Fore.CYAN + f"[🔁] runner.py started in background → {RUNNER_LOG}")


# ---------- Auto-Night editor ----------
def _parse_hhmm(s: str) -> time:
    s = s.strip()
    # Accept "7", "07", "7:00", "07:00"
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


def show_autonight() -> None:
    with open(AUTONIGHT_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    state = "ON ✅" if cfg.get("enabled", True) else "OFF ❌"
    print(Style.BRIGHT + "Auto-Night")
    print(f"  State : {state}")
    print(f"  Window: {cfg.get('start','23:00')} → {cfg.get('end','07:00')}")
    print(f"  TZ    : {cfg.get('tz','Asia/Kolkata')}")


def edit_autonight() -> None:
    with open(AUTONIGHT_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    print(Style.BRIGHT + "\nEdit Auto-Night (press Enter to keep current)")
    en = input(
        f"Enable? [y/n] (current: {'on' if cfg.get('enabled', True) else 'off'}): "
    ).strip().lower()
    if en in {"y", "yes", "on"}:
        cfg["enabled"] = True
    elif en in {"n", "no", "off"}:
        cfg["enabled"] = False

    start_in = input(
        f"Start HH[:MM] (current {cfg.get('start','23:00')}): "
    ).strip()
    if start_in:
        try:
            t = _parse_hhmm(start_in)
            cfg["start"] = f"{t.hour:02d}:{t.minute:02d}"
        except ValueError as e:
            print(Fore.RED + f"Invalid start time: {e}")

    end_in = input(
        f"End HH[:MM] (current {cfg.get('end','07:00')}): "
    ).strip()
    if end_in:
        try:
            t = _parse_hhmm(end_in)
            cfg["end"] = f"{t.hour:02d}:{t.minute:02d}"
        except ValueError as e:
            print(Fore.RED + f"Invalid end time: {e}")

    tz_in = input(
        f"Timezone (current {cfg.get('tz','Asia/Kolkata')}): "
    ).strip()
    if tz_in:
        cfg["tz"] = tz_in

    with open(AUTONIGHT_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(Fore.GREEN + "Saved Auto-Night settings.")
    show_autonight()


# ---------- Login / Delete ----------
def login_new_user(users: Dict[str, Any]) -> None:
    print(Style.BRIGHT + "\nNew user login")
    name = input("Enter a name for this user (optional): ").strip()

    # Try to reuse API ID / HASH from any existing user to avoid re-typing
    default_api_id: str = ""
    default_api_hash: str = ""
    if users:
        any_user = next(iter(users.values()))
        if any_user.get("api_id"):
            default_api_id = str(any_user["api_id"])
        if any_user.get("api_hash"):
            default_api_hash = any_user["api_hash"]

    if default_api_id:
        api_id = input(f"API ID [{default_api_id}]: ").strip() or default_api_id
    else:
        api_id = input("API ID: ").strip()

    if default_api_hash:
        api_hash = input(f"API HASH [{default_api_hash}]: ").strip() or default_api_hash
    else:
        api_hash = input("API HASH: ").strip()

    phone = input("Phone number (with country code): ").strip()

    if not api_id.isdigit():
        print(Fore.RED + "API ID must be a number.")
        return
    if not api_hash:
        print(Fore.RED + "API HASH cannot be empty.")
        return
    if not phone:
        print(Fore.RED + "Phone number cannot be empty.")
        return

    session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
    client = TelegramClient(session_path, int(api_id), api_hash)

    try:
        client.connect()
        if not client.is_user_authorized():
            try:
                client.send_code_request(phone)
            except FloodWaitError as e:
                print(
                    Fore.RED
                    + f"FloodWait: wait {getattr(e, 'seconds', '?')} seconds."
                )
                return

            code = input("Enter the code sent to Telegram: ").strip()
            try:
                client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = input("Enter 2FA password: ").strip()
                client.sign_in(password=password)
            except PhoneCodeInvalidError:
                print(Fore.RED + "Invalid login code.")
                return
            except PhoneNumberBannedError:
                print(Fore.RED + "This phone number is banned by Telegram.")
                return

        me = client.get_me()
        who = (
            (getattr(me, "first_name", "") or getattr(me, "username", "") or str(me.id))
            .strip()
        )
        print(Fore.GREEN + f"[✔] {who or name} logged in successfully.")

        # persist minimal info in users.json
        users[phone] = {
            "name": name or who,
            "api_id": int(api_id),
            "api_hash": api_hash,
        }
        save_users(users)
        cfg = save_user_config(phone, users[phone])

        # Small UX: show plan expiry
        exp = cfg.get("plan_expiry")
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp)
                print(
                    Fore.CYAN
                    + f"Plan expiry set to: {exp_dt.strftime('%Y-%m-%d')} (30 days from now)"
                )
            except Exception:
                pass

    finally:
        try:
            client.disconnect()
        except Exception:
            pass

    # Start runner only once
    start_runner_if_needed()


def delete_user(users: Dict[str, Any]) -> None:
    phone = input("Enter the phone number of the user to delete: ").strip()
    if phone in users:
        session_file = os.path.join(SESSIONS_DIR, f"{phone}.session")
        config_file = os.path.join(USERS_DIR, f"{phone}.json")
        if os.path.exists(session_file):
            os.remove(session_file)
        if os.path.exists(config_file):
            os.remove(config_file)
        users.pop(phone)
        save_users(users)
        print(Fore.RED + f"User {phone} deleted.")
    else:
        print(Fore.YELLOW + "User not found.")


# ---------- Main Menu ----------
def start() -> None:
    ensure_dirs()
    while True:
        users = load_users()
        print(Style.BRIGHT + "\n--- Telethon Multi-User Manager ---")
        print("1. List users")
        print("2. Login new user")
        print("3. Delete user")
        print("4. Show Auto-Night")
        print("5. Edit Auto-Night")
        print("6. Start runner (if not running)")
        print("7. Exit")
        choice = input("Choose an option: ").strip()

        if choice == "1":
            list_users(users)
        elif choice == "2":
            login_new_user(users)
        elif choice == "3":
            delete_user(users)
        elif choice == "4":
            show_autonight()
        elif choice == "5":
            edit_autonight()
        elif choice == "6":
            start_runner_if_needed()
        elif choice == "7":
            print("Goodbye.")
            break
        else:
            print("Invalid choice. Try again.")


if __name__ == "__main__":
    start()
