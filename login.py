#!/usr/bin/env python3
import os
import sys
import random
import string
from datetime import datetime, timedelta
from getpass import getpass
import subprocess

from dotenv import load_dotenv
from pymongo import MongoClient
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

# --------------------
# Env / DB bootstrap
# --------------------
load_dotenv()

API_ID = int(os.getenv("TG_API_ID") or os.getenv("API_ID") or 0)
API_HASH = os.getenv("TG_API_HASH") or os.getenv("API_HASH") or ""
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("SPINIFY_DB_NAME", "spinify")

if not API_ID or not API_HASH:
    print("[!] Set TG_API_ID/API_ID and TG_API_HASH/API_HASH in .env")
    sys.exit(1)

mongo = MongoClient(MONGO_URI)
db = mongo[DB_NAME]
users = db.users          # users collection
codes = db.plan_codes     # subscription codes collection


# --------------------
# Helpers
# --------------------
def generate_code(length: int = 10) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        c = "".join(random.choice(alphabet) for _ in range(length))
        if not codes.find_one({"code": c}):
            return c


def parse_date(prompt: str) -> datetime:
    """
    Ask for a date like YYYY-MM-DD and return datetime at 23:59:59 of that date.
    """
    while True:
        val = input(prompt).strip()
        try:
            d = datetime.strptime(val, "%Y-%m-%d")
            # expiry at the end of that day
            return d + timedelta(days=1) - timedelta(seconds=1)
        except ValueError:
            print("Invalid date. Format must be YYYY-MM-DD (example: 2025-12-31).")


def parse_hour(prompt: str, default: int | None = None) -> int:
    while True:
        val = input(prompt + (f" [{default}]" if default is not None else "") + ": ").strip()
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


def get_all_users():
    return list(users.find().sort("created_at", 1))


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
        plan_str = plan_expiry.strftime("%Y-%m-%d") if plan_expiry else "∞"
        print(f"{idx}) {phone} | {name} | plan till: {plan_str}")

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
        sh = auto.get("start_hour", 23)
        eh = auto.get("end_hour", 7)
        active = u.get("active", True)

        plan_str = plan_expiry.strftime("%Y-%m-%d") if plan_expiry else "∞"
        print(
            f"- {phone} | {name} | plan: {plan_name} till {plan_str} | "
            f"auto-night: {'ON' if auto_enabled else 'OFF'} ({sh}:00→{eh}:00) | "
            f"active: {'YES' if active else 'NO'}"
        )


def login_new_user():
    print("\n--- Login new Telegram user ---")
    phone = input("Phone (with country code, e.g. +919xxxxxxxxx): ").strip()
    if not phone:
        print("Phone cannot be empty.")
        return

    existing = users.find_one({"phone": phone})
    if existing:
        print("User with this phone already exists in DB.")
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

    # Initial plan (can be extended later with codes)
    use_plan = input("Set an initial plan expiry now? (y/N): ").strip().lower()
    plan_expiry = None
    plan_name = "free"
    if use_plan == "y":
        plan_expiry = parse_date("Plan expiry date (YYYY-MM-DD): ")
        plan_name = input("Plan label (e.g. PRO30): ").strip() or "custom"

    # Per-user auto-night
    enable_an = input("Enable Auto-Night for this user? (y/N): ").strip().lower()
    auto_cfg = {"enabled": False, "start_hour": 23, "end_hour": 7}
    if enable_an == "y":
        sh = parse_hour("Start hour (0–23)", 23)
        eh = parse_hour("End hour (0–23)", 7)
        auto_cfg = {"enabled": True, "start_hour": sh, "end_hour": eh}

    now = datetime.utcnow()
    users.insert_one(
        {
            "phone": phone,
            "name": name,
            "string_session": session_str,
            "plan_name": plan_name,
            "plan_expiry": plan_expiry,  # None = unlimited
            "auto_night": auto_cfg,
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
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
    users.delete_one({"_id": user["_id"]})
    print(f"[x] Deleted user {phone} from DB.")


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
        sh = auto.get("start_hour", 23)
        eh = auto.get("end_hour", 7)
        print(
            f"- {phone} | {name} -> "
            f"{'ON' if enabled else 'OFF'} ({sh}:00→{eh}:00)"
        )


def edit_auto_night():
    user = choose_user()
    if not user:
        return

    phone = user.get("phone")
    auto = user.get("auto_night") or {"enabled": False, "start_hour": 23, "end_hour": 7}

    print(f"\nCurrent Auto-Night for {phone}:")
    print(
        f"  enabled: {auto.get('enabled', False)}, "
        f"{auto.get('start_hour', 23)}:00→{auto.get('end_hour', 7)}:00"
    )

    en = input("Enable Auto-Night for this user? (y/N): ").strip().lower()
    if en == "y":
        sh = parse_hour("Start hour (0–23)", auto.get("start_hour", 23))
        eh = parse_hour("End hour (0–23)", auto.get("end_hour", 7))
        new_auto = {"enabled": True, "start_hour": sh, "end_hour": eh}
    else:
        new_auto = {"enabled": False, "start_hour": auto.get("start_hour", 23), "end_hour": auto.get("end_hour", 7)}

    users.update_one(
        {"_id": user["_id"]},
        {"$set": {"auto_night": new_auto, "updated_at": datetime.utcnow()}},
    )
    print("[✓] Auto-Night updated.")


def generate_plan_code():
    print("\n--- Generate plan code ---")
    plan_name = input("Plan label (e.g. PRO30): ").strip() or "custom"
    expiry_dt = parse_date("Plan expiry (YYYY-MM-DD): ")

    code = generate_code(10)

    codes.insert_one(
        {
            "code": code,
            "plan_name": plan_name,
            "plan_expiry": expiry_dt,
            "used": False,
            "used_by": None,
            "used_at": None,
            "created_at": datetime.utcnow(),
        }
    )
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

    code_doc = codes.find_one({"code": code_str})
    if not code_doc:
        print("Invalid code.")
        return
    if code_doc.get("used"):
        used_by = code_doc.get("used_by")
        print(f"This code has already been used (user_id={used_by}).")
        return

    plan_expiry = code_doc["plan_expiry"]
    plan_name = code_doc.get("plan_name", "custom")

    # Update user
    users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "plan_name": plan_name,
                "plan_expiry": plan_expiry,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    # Mark code used
    codes.update_one(
        {"_id": code_doc["_id"]},
        {
            "$set": {
                "used": True,
                "used_by": user["_id"],
                "used_at": datetime.utcnow(),
            }
        },
    )

    phone = user.get("phone")
    print(
        f"[✓] Code applied to user {phone}. "
        f"Plan `{plan_name}` valid till {plan_expiry.strftime('%Y-%m-%d')}."
    )


def start_runner():
    """
    Very simple starter: runs `python3 runner.py` in background.
    If you use systemd instead, you can ignore this.
    """
    print("\n--- Start runner ---")
    cmd = os.getenv("RUNNER_CMD", "python3 runner.py")
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
--- Telethon Multi-User Manager ---
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
