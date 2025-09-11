#!/usr/bin/env python3
# Multi-user login CLI with better UX.
# Features:
# - Login new user (with OTP prompt)
# - Delete user (with confirmation)
# - List users
# - Start/Restart runner with logging
# - Prevent duplicate runners
# - Force IPv4 connection

import sys, subprocess, json
from pathlib import Path
from telethon.sync import TelegramClient
from telethon import errors

# Optional: requires psutil for runner detection
try:
    import psutil
except ImportError:
    psutil = None

USERS_DIR = Path("users")
SESSIONS_DIR = Path("sessions")
LOGS_DIR = Path("logs")

for d in (USERS_DIR, SESSIONS_DIR, LOGS_DIR):
    d.mkdir(exist_ok=True)

GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"

def user_file(phone: str) -> Path:
    return USERS_DIR / f"{phone}.json"

def save_user(phone: str, api_id: int, api_hash: str):
    f = user_file(phone)
    data = {"phone": phone, "api_id": api_id, "api_hash": api_hash}
    with open(f, "w") as fp:
        json.dump(data, fp, indent=2)
    print(f"{GREEN}✔ Saved user config: {f}{RESET}")

def load_user(phone: str):
    f = user_file(phone)
    if not f.exists():
        return None
    with open(f) as fp:
        return json.load(fp)

def session_path(phone: str) -> Path:
    return SESSIONS_DIR / f"{phone}.session"

def is_runner_running(phone: str) -> bool:
    """Check if runner.py is already running for this phone"""
    if not psutil:
        return False
    for proc in psutil.process_iter(["cmdline"]):
        try:
            if proc.info["cmdline"] and "runner.py" in proc.info["cmdline"] and phone in proc.info["cmdline"]:
                return True
        except Exception:
            continue
    return False

def start_runner(phone: str, restart=False):
    if is_runner_running(phone) and not restart:
        print(f"{RED}✘ Runner already running for {phone}{RESET}")
        return
    log_file = LOGS_DIR / f"runner_{phone}.log"
    with open(log_file, "a") as lf:
        subprocess.Popen(
            [sys.executable, "runner.py", phone],
            stdout=lf, stderr=lf,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    print(f"{GREEN}✔ Runner started for {phone}. Logs: {log_file}{RESET}")

def do_login():
    print(f"\n{CYAN}➻ LOGIN PROCESS{RESET}")
    phone = input("Enter phone (+countrycode): ").strip()
    if user_file(phone).exists():
        print(f"{RED}✘ User already exists{RESET}")
        return
    try:
        api_id = int(input("Enter API_ID: ").strip())
    except ValueError:
        print(f"{RED}✘ Invalid API_ID{RESET}")
        return
    api_hash = input("Enter API_HASH: ").strip()
    save_user(phone, api_id, api_hash)

    sess = session_path(phone)
    # Force IPv4 with use_ipv6=False
    client = TelegramClient(str(sess), api_id, api_hash, use_ipv6=False)

    try:
        # Start login manually to control OTP input
        client.connect()
        if not client.is_user_authorized():
            client.send_code_request(phone)
            code = input("Enter the code you received: ").strip()
            try:
                client.sign_in(phone, code)
            except errors.SessionPasswordNeededError:
                pw = input("Two-step password enabled. Enter your password: ").strip()
                client.sign_in(password=pw)

        me = client.get_me()
        print(f"{GREEN}✔ Logged in as {me.first_name} (@{me.username}) id={me.id}{RESET}")
        client.disconnect()
        start_runner(phone)   # auto-run background
    except errors.ApiIdInvalidError:
        print(f"{RED}✘ Invalid API_ID/API_HASH{RESET}")
    except errors.PhoneCodeInvalidError:
        print(f"{RED}✘ Wrong login code{RESET}")
    except errors.PasswordHashInvalidError:
        print(f"{RED}✘ Wrong 2FA password{RESET}")
    except Exception as e:
        print(f"{RED}✘ Login failed: {e}{RESET}")

def do_delete():
    print(f"\n{CYAN}➻ DELETE USER{RESET}")
    phone = input("Enter phone to delete: ").strip()
    confirm = input(f"Type 'YES' to confirm delete {phone}: ")
    if confirm != "YES":
        print(f"{RED}✘ Cancelled{RESET}")
        return
    deleted = False
    if session_path(phone).exists():
        session_path(phone).unlink()
        print(f"{GREEN}✔ Deleted session for {phone}{RESET}")
        deleted = True
    if user_file(phone).exists():
        user_file(phone).unlink()
        print(f"{GREEN}✔ Deleted config for {phone}{RESET}")
        deleted = True
    if not deleted:
        print(f"{RED}✘ No session/config found for {phone}{RESET}")

def list_users():
    users = [f.stem for f in USERS_DIR.glob("*.json")]
    if not users:
        print(f"{RED}✘ No users found{RESET}")
    else:
        print(f"{CYAN}Users:{RESET}")
        for u in users:
            print(f" - {u}")

def restart_runner():
    phone = input("Enter phone to restart runner: ").strip()
    start_runner(phone, restart=True)

def menu():
    while True:
        print(f"\n{CYAN}==== Telegram Forwarder CLI ===={RESET}")
        print("  [1] Login (new user, auto-runner)")
        print("  [2] Delete user")
        print("  [3] List users")
        print("  [4] Restart runner")
        print("  [5] Exit")
        choice = input("➻ Choose option: ").strip()
        if choice == "1":
            do_login()
        elif choice == "2":
            do_delete()
        elif choice == "3":
            list_users()
        elif choice == "4":
            restart_runner()
        elif choice == "5":
            print(f"{CYAN}Exiting...{RESET}")
            break
        else:
            print(f"{RED}✘ Invalid choice{RESET}")

if __name__ == "__main__":
    menu()
    
