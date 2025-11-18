#!/usr/bin/env python3
import os
import sys
import json
import random
import string
from datetime import datetime, timedelta
from getpass import getpass
import subprocess

from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
USERS_DIR = os.path.join(BASE_DIR, "users")
CODES_PATH = os.path.join(BASE_DIR, "plan_codes.json")
DEFAULT_TZ = "Asia/Kolkata"


# --------------------
# Config handling (no .env, no Mongo)
# --------------------
def load_or_init_config() -> dict:
    cfg: dict = {}

    # Try to load existing config.json
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    def ask(key: str, label: str, cast=str):
        # If already in config and looks valid, keep it
        if key in cfg and cfg[key]:
            return

        while True:
            val = input(label + ": ").strip()
            if not val:
                print("This value cannot be empty.")
                continue
            if cast is int:
                try:
                    val = int(val)
                except ValueError:
                    print("Please enter a valid integer.")
                    continue
            cfg[key] = val
            break

    if not cfg:
        print("=== Spinify CLI Setup (first run) ===")
    else:
        print("=== Loaded config.json ===")

    ask("API_ID", "Telegram API ID", cast=int)
    ask("API_HASH", "Telegram API Hash", cast=str)

    # Save back to file
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"[✓] Config saved to {CONFIG_PATH}")
    except Exception as e:
        print(f"[!] Failed to save config.json: {e}")

    return cfg


CFG = load_or_init_config()

API_ID = int(CFG["API_ID"])
API_HASH = str(CFG["API_HASH"])

os.makedirs(USERS_DIR, exist_ok=True)


# --------------------
# Helpers: users & codes
# --------------------
def user_file_path(phone: str) -> str:
    safe = phone.replace(" ", "")
    return os.path.join(USERS_DIR, f"{safe}.json")


def load_user(phone: str) -> dict | None:
    path = user_file_path(phone)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_user(user: dict) -> None:
    phone = user.get("phone")
    if not phone:
        return
    path = user_file_path(phone)
    user["updated_at"] = datetime.utcnow().isoformat()
    if "created_at" not in user:
        user["created_at"] = user["updated_at"]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(user, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Failed to save user {phone}: {e}")


def get_all_users() -> list[dict]:
    users: list[dict] = []
    for name in os.listdir(USERS_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(USERS_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                u = json.load(f)
                users.append(u)
        except Exception:
            continue
    # sort by created_at if present
    users.sort(key=lambda u: u.get("created_at", ""))
    return users


def load_codes() -> list[dict]:
    if not os.path.exists(CODES_PATH):
        return []
    try:
        with open(CODES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_codes(codes: list[dict]) -> None:
    try:
        with open(CODES_PATH, "w", encoding="utf-8") as f:
            json.dump(codes, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Failed to save plan codes: {e}")


def generate_code(length: int = 10) -> str:
    alphabet = string.ascii_uppercase + string.digits
    codes = load_codes()
    existing = {c.get("code") for c in codes}
    while True:
        c = "".join(random.choice(alphabet) for _ in range(length))
        if c not in existing:
            return c


def parse_date(prompt: str) -> datetime:
    """
    Ask for a date like YYYY-MM-DD and return datetime at 23:59:59 of that date.
    """
    while True:
        val = input(prompt).strip()
        try:
            d = datetime.strptime(val, "%Y-%m-%d")
            return d + timedelta(days=1) - timedelta(seconds=1)
        except ValueError:
            print("Invalid date. Format must be YYYY-MM-DD (example: 2025-12-31).")


def parse_hour(prompt: str, default: int | None = None) -> int:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        val = input(prompt + suffix + ": ").strip()
        if not val and default is not None:
            return default
        if not val:
            print("Please enter an hour between 0–23.")
            continue
        try:
            h = int(val)
            if 0 <= h <= 23:
                return h
            print("Hour must be between 0 and 23.")
        except ValueError:
            print("Invalid number, try again.")


def choose_user() -> dict | None:
    all_users = get_all_users()
    if not all_users:
        print("No users found.")
        return None

    print("\n--- Users ---")
    for idx, u in enumerate(all_users, start=1):
        phone = u.get("phone")
        name = u.get("name") or ""
        plan_expiry = u.get("plan_expiry")
        if plan_expiry:
            try:
                dt = datetime.fromisoformat(plan_expiry)
                plan_str = dt.strftime("%Y-%m-%d")
            except Exception:
                plan_str = str(plan_expiry)
        else:
            plan_str = "∞"
        active = "YES" if u.get("active", True) else "NO"
        print(f"{idx}) {phone} | {name} | plan till: {plan_str} | active: {active}")

    while True:
        sel = input("Select user # (or blank to cancel): ").strip()
        if not sel:
            return None
        try:
            sel_i = int(sel)
            if 1 <= sel_i <= len(all_users):
                return all_users[sel_i - 1]
            print("Invalid choice.")
        except ValueError:
            print("Enter a number.")


# --------------------
# Actions
# --------------------
def list_users():
    all_users = get_all_users()
    if not all_users:
        print("No users in DB yet.")
        return

    print("\n--- Users in DB ---")
    for u in all_users:
        phone = u.get("phone")
        name = u.get("name") or ""
        plan_expiry = u.get("plan_expiry")
        plan_name = u.get("plan_name") or "NA"
        auto = u.get("auto_night") or {}
        auto_enabled = auto.get("enabled", False)
        start = auto.get("start", "23:00")
        end = auto.get("end", "07:00")
        active = u.get("active", True)

        if plan_expiry:
            try:
                dt = datetime.fromisoformat(plan_expiry)
                plan_str = dt.strftime("%Y-%m-%d")
            except Exception:
                plan_str = str(plan_expiry)
        else:
            plan_str = "∞"

        print(
            f"- {phone} | {name} | plan: {plan_name} till {plan_str} | "
            f"auto-night: {'ON' if auto_enabled else 'OFF'} ({start}→{end}) | "
            f"active: {'YES' if active else 'NO'}"
        )


def login_new_user():
    print("\n--- Login new Telegram user ---")
    phone = input("Phone (with country code, e.g. +919xxxxxxxxx): ").strip()
    if not phone:
        print("Phone cannot be empty.")
        return

    if load_user(phone):
        print("User with this phone already exists.")
        return

    name = input("Name/label for this account (for your reference): ").strip() or phone

    print("Logging into Telegram via Telethon...")
    session_str = None
    with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        client.connect()
        client.send_code_request(phone)
        code = input("Enter the login code sent by Telegram: ").strip()

        try:
            client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            pw = getpass("2FA password: ")
            client.sign_in(password=pw)

        session_str = client.session.save()

    print("Login successful.")

    # Initial plan
    use_plan = input("Set an initial plan expiry now? (y/N): ").strip().lower()
    plan_expiry_iso = None
    plan_name = "free"
    if use_plan == "y":
        plan_expiry_dt = parse_date("Plan expiry date (YYYY-MM-DD): ")
        plan_expiry_iso = plan_expiry_dt.isoformat()
        plan_name = input("Plan label (e.g. PRO30): ").strip() or "custom"

    # Per-user auto-night (structure compatible with runner.py)
    enable_an = input("Enable Auto-Night for this user? (y/N): ").strip().lower()
    if enable_an == "y":
        sh = parse_hour("Start hour (0–23)", 23)
        eh = parse_hour("End hour (0–23)", 7)
        auto_cfg = {
            "enabled": True,
            "start": f"{sh:02d}:00",
            "end": f"{eh:02d}:00",
            "tz": DEFAULT_TZ,
        }
    else:
        auto_cfg = {
            "enabled": False,
            "start": "23:00",
            "end": "07:00",
            "tz": DEFAULT_TZ,
        }

    now_iso = datetime.utcnow().isoformat()
    user = {
        "phone": phone,
        "name": name,
        "string_session": session_str,
        "plan_name": plan_name,
        "plan_expiry": plan_expiry_iso,  # None = unlimited
        "auto_night": auto_cfg,
        "groups": [],  # will be updated via .addgroup inside runner.py
        "settings": {
            "msg_delay_sec": 5,
            "cycle_delay_min": 15,
        },
        "active": True,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    save_user(user)
    print(f"[+] User {phone} added.")


def delete_user():
    user = choose_user()
    if not user:
        return
    phone = user.get("phone")
    sure = input(f"Are you sure you want to delete {phone}? (y/N): ").strip().lower()
    if sure != "y":
        print("Cancelled.")
        return
    path = user_file_path(phone)
    try:
        os.remove(path)
        print(f"[x] Deleted user {phone}.")
    except FileNotFoundError:
        print("User file not found (already deleted?).")
    except Exception as e:
        print(f"[!] Failed to delete user file: {e}")


def show_auto_night():
    all_users = get_all_users()
    if not all_users:
        print("No users in DB.")
        return

    print("\n--- Per-user Auto-Night ---")
    for u in all_users:
        phone = u.get("phone")
        name = u.get("name") or ""
        auto = u.get("auto_night") or {}
        enabled = auto.get("enabled", False)
        start = auto.get("start", "23:00")
        end = auto.get("end", "07:00")
        tz = auto.get("tz", DEFAULT_TZ)
        print(
            f"- {phone} | {name} -> "
            f"{'ON' if enabled else 'OFF'} ({start}→{end}) [{tz}]"
        )


def edit_auto_night():
    user = choose_user()
    if not user:
        return

    phone = user.get("phone")
    auto = user.get("auto_night") or {
        "enabled": False,
        "start": "23:00",
        "end": "07:00",
        "tz": DEFAULT_TZ,
    }

    start_str = auto.get("start", "23:00")
    end_str = auto.get("end", "07:00")
    tz = auto.get("tz", DEFAULT_TZ)

    def _hour_from_str(s: str, default: int) -> int:
        try:
            return int((s or "").split(":")[0])
        except Exception:
            return default

    start_h_current = _hour_from_str(start_str, 23)
    end_h_current = _hour_from_str(end_str, 7)

    print(f"\nCurrent Auto-Night for {phone}:")
    print(
        f"  enabled: {auto.get('enabled', False)}, "
        f"{start_str}→{end_str} [{tz}]"
    )

    en = input("Enable Auto-Night for this user? (y/N): ").strip().lower()
    if en == "y":
        sh = parse_hour("Start hour (0–23)", start_h_current)
        eh = parse_hour("End hour (0–23)", end_h_current)
        new_auto = {
            "enabled": True,
            "start": f"{sh:02d}:00",
            "end": f"{eh:02d}:00",
            "tz": tz,
        }
    else:
        new_auto = {
            "enabled": False,
            "start": start_str,
            "end": end_str,
            "tz": tz,
        }

    user["auto_night"] = new_auto
    save_user(user)
    print("[✓] Auto-Night updated.")


def generate_plan_code():
    print("\n--- Generate plan code ---")
    plan_name = input("Plan label (e.g. PRO30): ").strip() or "custom"
    expiry_dt = parse_date("Plan expiry (YYYY-MM-DD): ")
    expiry_iso = expiry_dt.isoformat()

    code = generate_code(10)

    codes = load_codes()
    codes.append(
        {
            "code": code,
            "plan_name": plan_name,
            "plan_expiry": expiry_iso,
            "used": False,
            "used_by": None,
            "used_at": None,
            "created_at": datetime.utcnow().isoformat(),
        }
    )
    save_codes(codes)

    print("\n[+] Code generated:")
    print(f"  Code       : {code}")
    print(f"  Plan       : {plan_name}")
    print(f"  Plan expiry: {expiry_dt.strftime('%Y-%m-%d')}")
    print("\nShare this code with the user. It can be redeemed only once.")


def redeem_plan_code():
    print("\n--- Redeem plan code for a user ---")
    user = choose_user()
    if not user:
        return

    code_str = input("Enter code: ").strip().upper()
    if not code_str:
        print("Code cannot be empty.")
        return

    codes = load_codes()
    idx = None
    code_doc = None
    for i, c in enumerate(codes):
        if c.get("code") == code_str:
            idx = i
            code_doc = c
            break

    if not code_doc:
        print("Invalid code.")
        return
    if code_doc.get("used"):
        used_by = code_doc.get("used_by")
        print(f"This code has already been used (used_by={used_by}).")
        return

    plan_expiry_iso = code_doc["plan_expiry"]
    plan_name = code_doc.get("plan_name", "custom")

    # Update user
    user["plan_name"] = plan_name
    user["plan_expiry"] = plan_expiry_iso
    save_user(user)

    # Mark code used
    codes[idx]["used"] = True
    codes[idx]["used_by"] = user.get("phone")
    codes[idx]["used_at"] = datetime.utcnow().isoformat()
    save_codes(codes)

    try:
        exp_dt = datetime.fromisoformat(plan_expiry_iso)
        exp_str = exp_dt.strftime("%Y-%m-%d")
    except Exception:
        exp_str = str(plan_expiry_iso)

    phone = user.get("phone")
    print(
        f"[✓] Code applied to user {phone}. "
        f"Plan `{plan_name}` valid till {exp_str}."
    )


def start_runner():
    """
    Very simple starter: runs `python3 runner.py` in background.
    If you use systemd instead, you can ignore this.
    """
    print("\n--- Start runner ---")
    cmd = "python3 runner.py"
    print(f"Starting: {cmd}")
    try:
        subprocess.Popen(cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[✓] runner.py started (check logs separately).")
    except Exception as e:
        print(f"[!] Failed to start runner: {e}")


# --------------------
# Menu
# --------------------
MENU = """
--- Telethon Multi-User Manager (CLI) ---
1. List users
2. Login new user
3. Delete user
4. Show Auto-Night (per-user)
5. Edit Auto-Night (per-user)
6. Generate plan code
7. Redeem plan code for user
8. Start runner (if not running)
0. Exit
"""


def main():
    while True:
        print(MENU)
        choice = input("Select option: ").strip()

        if choice == "1":
            list_users()
        elif choice == "2":
            login_new_user()
        elif choice == "3":
            delete_user()
        elif choice == "4":
            show_auto_night()
        elif choice == "5":
            edit_auto_night()
        elif choice == "6":
            generate_plan_code()
        elif choice == "7":
            redeem_plan_code()
        elif choice == "8":
            start_runner()
        elif choice == "0":
            print("Bye.")
            break
        else:
            print("Invalid option.")


if __name__ == "__main__":
    main()
