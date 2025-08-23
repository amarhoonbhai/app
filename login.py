#!/usr/bin/env python3
# Multi-user login CLI. After login, auto-starts runner in background.

import sys, subprocess, json
from pathlib import Path
from telethon.sync import TelegramClient
from telethon import errors

USERS_DIR = Path("users")
SESSIONS_DIR = Path("sessions")
LOGS_DIR = Path("logs")

for d in (USERS_DIR, SESSIONS_DIR, LOGS_DIR):
    d.mkdir(exist_ok=True)


def user_file(phone: str) -> Path:
    return USERS_DIR / f"{phone}.json"


def save_user(phone: str, api_id: int, api_hash: str):
    f = user_file(phone)
    data = {"phone": phone, "api_id": api_id, "api_hash": api_hash}
    with open(f, "w") as fp:
        json.dump(data, fp, indent=2)
    print(f"💾 Saved user config: {f}")


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
    print(f"✅ Runner started for {phone}. Logs: {log_file}")


def do_login():
    phone = input("Phone (+countrycode): ").strip()
    api_id = int(input("API_ID: ").strip())
    api_hash = input("API_HASH: ").strip()
    save_user(phone, api_id, api_hash)

    sess = session_path(phone)
    client = TelegramClient(str(sess), api_id, api_hash)
    try:
        client.start(phone=lambda: phone)
        me = client.get_me()
        print(f"✅ Logged in as {me.first_name} (@{me.username}) id={me.id}")
        client.disconnect()
        start_runner(phone)   # auto-run background
        print(
            "\n🚀 Now you can use commands in Telegram:\n"
            "  • .addgroup <link> → instantly join groups (folder links supported)\n"
            "  • .listgroups / .delgroup <id>\n"
            "  • .delay <s> → set forward delay\n"
            "  • .time <m> → set cycle interval (minutes)\n"
            "  • .status / .info / .help\n"
            "📌 Send any message to Saved Messages → it will be included in the cycle forward."
        )
    except errors.ApiIdInvalidError:
        print("❌ Invalid API_ID/API_HASH")
    except errors.PhoneCodeInvalidError:
        print("❌ Wrong login code")
    except errors.PasswordHashInvalidError:
        print("❌ Wrong 2FA password")
    except Exception as e:
        print(f"❌ Login failed: {e}")


def do_delete():
    phone = input("Phone to delete: ").strip()
    if session_path(phone).exists():
        session_path(phone).unlink()
        print(f"🗑️ Deleted session for {phone}")
    if user_file(phone).exists():
        user_file(phone).unlink()
        print(f"🗑️ Deleted config for {phone}")


def menu():
    while True:
        print("\n==== Multi-User Login Menu ====")
        print("[1] Login (new user, auto-runner)")
        print("[2] Delete user")
        print("[3] Exit")
        choice = input("> ").strip()
        if choice == "1":
            do_login()
        elif choice == "2":
            do_delete()
        elif choice == "3":
            break
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    menu()

