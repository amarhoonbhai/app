import json
import os
import subprocess
import sys
import asyncio
from datetime import datetime

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

USERS_DIR = "users"
SESS_DIR = "sessions"
os.makedirs(USERS_DIR, exist_ok=True)
os.makedirs(SESS_DIR, exist_ok=True)

MENU = """
==== Telegram Forwarder CLI ====
  [1] Login with QR (no phone input)
  [2] Login with phone (OTP / 2FA)
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
    return os.path.join(SESS_DIR, f"{label}")

def start_runner(cfg_path: str):
    """Detach a runner for the given user config."""
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

# ---------------- QR LOGIN (no phone input) ----------------
async def do_qr_login(label: str, api_id: int, api_hash: str):
    sess_name = session_path(label)
    client = TelegramClient(sess_name, api_id, api_hash)
    await client.connect()
    print("\nGenerating QR login link...")
    qr = await client.qr_login()  # shows a tg://login?token=... URL internally
    print("\nOpen this ON YOUR PHONE (or any logged-in Telegram app) to approve login:\n")
    print(qr.url)  # Opening this tg:// URL in Telegram approves the session. 2FA handled below.
    try:
        await qr.wait()  # wait until the login is accepted on your device
    except SessionPasswordNeededError:
        pwd = input("Enter your 2FA password: ").strip()
        await client.check_password(pwd)

    me = await client.get_me()
    print(f"✔ Authorized as @{getattr(me, 'username', None) or me.first_name}")
    await client.disconnect()

def login_with_qr():
    print("== QR Login ==")
    label = input("Choose a session label (e.g. main, work): ").strip()
    api_id, api_hash = get_api_creds()
    asyncio.run(do_qr_login(label, api_id, api_hash))

    cfg = {
        **DEFAULT_CFG,
        # NOTE: runner.py uses CFG['phone'] only to form the session filename.
        # We store the chosen label here; it doesn't need to be an actual phone number.
        "phone": label,
        "api_id": api_id,
        "api_hash": api_hash,
    }
    cfg_path = user_cfg_path(label)
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"✔ Saved user config: {cfg_path}")
    start_runner(cfg_path)

# ---------------- PHONE LOGIN (fallback) ----------------
def login_with_phone():
    print("== Phone Login ==")
    phone = input("Enter phone (+countrycode): ").strip()
    api_id, api_hash = get_api_creds()

    sess_name = session_path(phone)
    client = TelegramClient(sess_name, api_id, api_hash)

    print("Connecting…")
    client.connect()
    if not client.is_user_authorized():
        client.send_code_request(phone)
        code = input("Enter the code you received: ").strip()
        try:
            client.sign_in(phone, code)
        except SessionPasswordNeededError:
            pwd = input("Enter your 2FA password: ").strip()
            client.sign_in(password=pwd)
    client.disconnect()

    cfg = {
        **DEFAULT_CFG,
        "phone": phone,
        "api_id": api_id,
        "api_hash": api_hash,
    }
    cfg_path = user_cfg_path(phone)
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
