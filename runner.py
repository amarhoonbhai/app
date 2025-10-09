import json
import os
import subprocess
import sys
from datetime import datetime

from telethon import TelegramClient

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

DEFAULT = {
    "targets": [],
    "interval_seconds": 30,
    "gap_seconds": 5,
    "mode": "rotation",
    "quiet": {"enabled": True, "start": "23:00", "end": "07:00"},
    "expire_date": "2026-01-10",
    "rot_index": 0
}

def user_cfg_path(phone: str) -> str:
    return os.path.join(USERS_DIR, f"{phone}.json")

def start_runner(cfg_path: str):
    print(f"▶ Starting runner for {cfg_path}")
    subprocess.Popen([sys.executable, "runner.py", cfg_path],
                     stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

def login_flow():
    phone = input("Enter phone (+countrycode): ").strip()
    api_id = int(input("Enter API_ID: ").strip())
    api_hash = input("Enter API_HASH: ").strip()

    session_name = os.path.join(SESS_DIR, phone)
    client = TelegramClient(session_name, api_id, api_hash)

    print("Connecting…")
    client.connect()
    if not client.is_user_authorized():
        client.send_code_request(phone)
        code = input("Enter the code you received: ").strip()
        try:
            client.sign_in(phone, code)
        except Exception as e:
            # 2FA password, if enabled
            if "SESSION_PASSWORD_NEEDED" in str(e):
                pwd = input("Enter your 2FA password: ").strip()
                client.sign_in(password=pwd)
            else:
                raise
    client.disconnect()

    cfg = {
        **DEFAULT,
        "phone": phone,
        "api_id": api_id,
        "api_hash": api_hash,
    }
    with open(user_cfg_path(phone), "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"✔ Saved user config: {user_cfg_path(phone)}")

    start_runner(user_cfg_path(phone))

def delete_user():
    phone = input("Phone to delete (+countrycode): ").strip()
    cfg = user_cfg_path(phone)
    sess = os.path.join(SESS_DIR, phone + ".session")
    for p in [cfg, sess]:
        try:
            os.remove(p)
            print(f"🗑️ Deleted {p}")
        except FileNotFoundError:
            pass

def list_users():
    print("Users:")
    for f in os.listdir(USERS_DIR):
        if f.endswith(".json"):
            path = os.path.join(USERS_DIR, f)
            try:
                with open(path) as fp:
                    cfg = json.load(fp)
                print(f" - {f[:-5]} | expire={cfg.get('expire_date','')}")
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
    cur = cfg.get("expire_date", "2026-01-10")
    print(f"Current expire date: {cur}")
    newv = input("Enter new date YYYY-MM-DD (leave blank to keep): ").strip()
    if newv:
        try:
            datetime.strptime(newv, "%Y-%m-%d")
        except ValueError:
            print("Invalid date format.")
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
    
