import json
import os
import subprocess
import sys
from datetime import datetime

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

USERS_DIR = "users"
SESS_DIR = "sessions"
os.makedirs(USERS_DIR, exist_ok=True)
os.makedirs(SESS_DIR, exist_ok=True)

MENU = """
==== Telegram Forwarder CLI ====
  [1] Login (new user, auto-runner)
  [2] Delete user
  [3] List users
  [4] Restart runner
  [5] Set/Show Expire Date (default 2026-01-10)
  [6] Exit
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

def user_cfg_path(phone: str) -> str:
    return os.path.join(USERS_DIR, f"{phone}.json")

def session_path(phone: str) -> str:
    return os.path.join(SESS_DIR, f"{phone}.session")

def start_runner(cfg_path: str):
    """Detach a runner for the given user config."""
    print(f"▶ Starting runner for {cfg_path}")
    subprocess.Popen(
        [sys.executable, "runner.py", cfg_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )

def login_flow():
    phone = input("Enter phone (+countrycode): ").strip()
    api_id = int(input("Enter API_ID: ").strip())
    api_hash = input("Enter API_HASH: ").strip()

    sess_name = os.path.join(SESS_DIR, phone)
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

def delete_user():
    phone = input("Phone to delete (+countrycode): ").strip()
    cfg = user_cfg_path(phone)
    sess = session_path(phone)
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
    phone = input("Phone to restart: ").strip()
    cfg = user_cfg_path(phone)
    if not os.path.exists(cfg):
        print("No such user.")
        return
    start_runner(cfg)

def set_or_show_expiry():
    phone = input("Phone to set/show expiry: ").strip()
    cfg_path = user_cfg_path(phone)
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
            login_flow()
        elif choice == "2":
            delete_user()
        elif choice == "3":
            list_users()
        elif choice == "4":
            restart_runner()
        elif choice == "5":
            set_or_show_expiry()
        elif choice == "6":
            print("Bye.")
            break
        else:
            print("Invalid choice.")

if __name__ == "__main__":
    main()
    
