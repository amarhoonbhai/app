import json
import os
import subprocess
import sys
import asyncio
from datetime import datetime

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeFloodError,
    PhoneNumberInvalidError,
    PhoneNumberBannedError,
)

USERS_DIR = "users"
SESS_DIR = "sessions"
os.makedirs(USERS_DIR, exist_ok=True)
os.makedirs(SESS_DIR, exist_ok=True)

MENU = """
==== Telegram Forwarder CLI ====
  [1] Login with QR (no phone input)
  [2] Login with phone (OTP to Telegram app)
  [3] Delete user
  [4] List users
  [5] Restart runner
  [6] Set/Show Expire Date (default 2026-01-10)
  [7] Exit
"""

DEFAULT_CFG = {
    "targets": [],
    "interval_seconds": 30,                 # interval between Saved-message jobs
    "gap_seconds": 5,                       # delay between targets in broadcast mode
    "mode": "rotation",                     # rotation | broadcast
    "quiet": {"enabled": True, "start": "23:00", "end": "07:00"},
    "expire_date": "2026-01-10",            # default expiry
    "rot_index": 0,
}

def user_cfg_path(label: str) -> str:
    return os.path.join(USERS_DIR, f"{label}.json")

def session_path(label: str) -> str:
    # Telethon will create sessions/<label>.session
    return os.path.join(SESS_DIR, label)

def start_runner(cfg_path: str):
    print(f"▶ Starting runner for {cfg_path}")
    subprocess.Popen(
        [sys.executable, "runner.py", cfg_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )

def get_api_creds():
    api_id = os.getenv("API_ID") or input("Enter API_ID: ").strip()
    api_hash = os.getenv("API_HASH") or input("Enter API_HASH: ").strip()
    return int(api_id), api_hash

# ---------------- ASYNC FLOWS ----------------
async def do_qr_login(label: str, api_id: int, api_hash: str):
    async with TelegramClient(session_path(label), api_id, api_hash) as client:
        print("\nGenerating QR login link…")
        token = await client.qr_login()
        print("\nOpen this ON YOUR PHONE (any logged-in Telegram app) to approve:\n")
        print(token.url)
        try:
            await token.wait()  # approve in Telegram
        except SessionPasswordNeededError:
            pwd = input("Enter your 2FA password: ").strip()
            await client.check_password(pwd)
        me = await client.get_me()
        print(f"✔ Authorized as @{getattr(me, 'username', None) or me.first_name}")

async def do_phone_login(label: str, api_id: int, api_hash: str, phone: str):
    async with TelegramClient(session_path(label), api_id, api_hash) as client:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✔ Already authorized as @{getattr(me, 'username', None) or me.first_name}")
            return

        try:
            print("Sending code to your Telegram app (check the 'Telegram' service chat)…")
            # force_sms=False ensures delivery to Telegram app first (not SMS)
            await client.send_code_request(phone, force_sms=False)
        except PhoneNumberInvalidError:
            print("✖ Phone number looks invalid. Use +countrycode, e.g. +447…")
            return
        except PhoneNumberBannedError:
            print("✖ This phone number is banned by Telegram.")
            return
        except PhoneCodeFloodError as e:
            secs = getattr(e, "seconds", None)
            print(f"⏳ Flood-wait. Try again later{f' (~{secs}s)' if secs else ''}.")
            return

        code = input("Enter the code you received in the Telegram app: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            pwd = input("2FA is enabled. Enter your password: ").strip()
            await client.sign_in(password=pwd)

        me = await client.get_me()
        print(f"✔ Authorized as @{getattr(me, 'username', None) or me.first_name}")

# ---------------- SYNC WRAPPERS ----------------
def login_with_qr():
    label = input("Choose a session label (e.g. main, work): ").strip()
    api_id, api_hash = get_api_creds()
    asyncio.run(do_qr_login(label, api_id, api_hash))

    cfg = {**DEFAULT_CFG, "phone": label, "api_id": api_id, "api_hash": api_hash}
    cfg_path = user_cfg_path(label)
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"✔ Saved user config: {cfg_path}")
    start_runner(cfg_path)

def login_with_phone():
    phone = input("Enter phone (+countrycode): ").strip()
    api_id, api_hash = get_api_creds()
    label = phone  # use phone as session label

    asyncio.run(do_phone_login(label, api_id, api_hash, phone))

    cfg = {**DEFAULT_CFG, "phone": label, "api_id": api_id, "api_hash": api_hash}
    cfg_path = user_cfg_path(label)
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"✔ Saved user config: {cfg_path}")
    start_runner(cfg_path)

# ---------------- Housekeeping ----------------
def delete_user():
    label = input("Label/phone to delete: ").strip()
    cfg = user_cfg_path(label)
    sess = session_path(label) + ".session"
    sess_journal = sess + "-journal"
    for p in [cfg, sess, sess_journal]:
        try:
            os.remove(p)
            print(f"🗑️ Deleted {p}")
        except FileNotFoundError:
            pass

def list_users():
    print("Users:")
    for f in os.listdir(USERS_DIR):
        if not f.endswith(".json"):
            continue
        path = os.path.join(USERS_DIR, f)
        try:
            with open(path) as fp:
                cfg = json.load(fp)
            exp = cfg.get("expire_date", "—")
            print(f" - {f[:-5]} | expire={exp}")
        except Exception:
            print(f" - {f[:-5]} (invalid json)")

def restart_runner():
    label = input("Label/phone to restart: ").strip()
    cfg = user_cfg_path(label)
    if not os.path.exists(cfg):
        print("No such user.")
        return
    start_runner(cfg)

def set_or_show_expiry():
    label = input("Label/phone to set/show expiry: ").strip()
    cfg_path = user_cfg_path(label)
    if not os.path.exists(cfg_path):
        print("No such user.")
        return
    with open(cfg_path) as f:
        cfg = json.load(f)
    current = cfg.get("expire_date", "2026-01-10")
    print(f"Current expire date: {current}")
    newv = input("Enter new date YYYY-MM-DD (leave blank to keep): ").strip()
    if not newv:
        return
    try:
        datetime.strptime(newv, "%Y-%m-%d")
    except ValueError:
        print("Invalid date format. Use YYYY-MM-DD (e.g., 2026-01-10).")
        return
    cfg["expire_date"] = newv
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"✔ Updated expire date to {newv}")

def main():
    while True:
        print(MENU)
        choice = input("➻ Choose option: ").strip()
        if choice == "1":
            login_with_qr()
        elif choice == "2":
            login_with_phone()
        elif choice == "3":
            delete_user()
        elif choice == "4":
            list_users()
        elif choice == "5":
            restart_runner()
        elif choice == "6":
            set_or_show_expiry()
        elif choice == "7":
            print("Bye.")
            break
        else:
            print("Invalid choice.")

if __name__ == "__main__":
    main()
