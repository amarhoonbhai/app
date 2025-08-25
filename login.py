#!/usr/bin/env python3
# Multi-user login CLI with better UX.
# After login, auto-starts runner in background.

import sys, subprocess, json
from pathlib import Path
from telethon.sync import TelegramClient
from telethon import errors

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


def start_runner(phone: str):
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
    api_id = int(input("Enter API_ID: ").strip())
    api_hash = input("Enter API_HASH: ").strip()
    save_user(phone, api_id, api_hash)

    sess = session_path(phone)
    client = TelegramClient(str(sess), api_id, api_hash)
    try:
        client.start(phone=lambda: phone)
        me = client.get_me()
        print(f"{GREEN}✔ Logged in as {me.first_name} (@{me.username}) id={me.id}{RESET}")
        client.disconnect()
        start_runner(phone)   # auto-run background
        print(
            f"\n{CYAN}➻ Now you can use these commands in Telegram:{RESET}\n"
            "  ➻ .addgroup <link>   → instantly join groups (folder links supported)\n"
            "  ➻ .listgroups        → list groups\n"
            "  ➻ .delgroup <id>     → remove group\n"
            "  ➻ .delay <s>         → set forward delay\n"
            "  ➻ .time <m>          → set cycle interval\n"
            "  ➻ .status / .info    → check status or info\n"
            "  ➻ .help              → show help menu\n"
            "  ➻ .clear             → clear all saved messages\n\n"
            "📌 Send any message to Saved Messages → it will be forwarded each cycle."
        )
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


def menu():
    while True:
        print(f"\n{CYAN}==== Telegram Forwarder CLI ===={RESET}")
        print("  [1] Login (new user, auto-runner)")
        print("  [2] Delete user")
        print("  [3] Exit")
        choice = input("➻ Choose option: ").strip()
        if choice == "1":
            do_login()
        elif choice == "2":
            do_delete()
        elif choice == "3":
            print(f"{CYAN}Exiting...{RESET}")
            break
        else:
            print(f"{RED}✘ Invalid choice{RESET}")


if __name__ == "__main__":
    menu()
    
