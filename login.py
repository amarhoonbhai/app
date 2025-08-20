import json
import asyncio
from datetime import datetime
from pathlib import Path
from functools import lru_cache
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from colorama import Fore, init

init(autoreset=True)

SESSIONS_DIR = Path("sessions")
USERS_DIR = Path("users")
USERS_FILE = Path("users.json")

def ensure_dirs():
    SESSIONS_DIR.mkdir(exist_ok=True)
    USERS_DIR.mkdir(exist_ok=True)
    if not USERS_FILE.exists():
        save_users({})

def load_users():
    try:
        with USERS_FILE.open('r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_users(users):
    try:
        with USERS_FILE.open('w') as f:
            json.dump(users, f, indent=2)
    except IOError as e:
        print(Fore.RED + f"Error saving users: {e}")

def save_user_config(phone, data):
    user_config = {
        "name": data.get("name", ""),
        "phone": phone,
        "logged_in": True,
        "last_login": datetime.now().isoformat(),
        "api_id": data.get("api_id"),
        "api_hash": data.get("api_hash"),
        "plan": data.get("plan", "free"),
        "expiry": data.get("expiry", "")
    }
    user_file = USERS_DIR / f"{phone}.json"
    try:
        with user_file.open('w') as f:
            json.dump(user_config, f, indent=2)
    except IOError as e:
        print(Fore.RED + f"Error saving user config for {phone}: {e}")

def is_subscription_valid(user_data):
    plan = user_data.get("plan", "free")
    expiry = user_data.get("expiry")
    if plan != "premium" or not expiry:
        return False
    try:
        return datetime.fromisoformat(expiry) > datetime.now()
    except ValueError:
        return False

def cleanup_expired_users():
    users = load_users()
    updated_users = users.copy()

    for phone, data in users.items():
        if not is_subscription_valid(data):
            session_file = SESSIONS_DIR / f"{phone}.session"
            user_file = USERS_DIR / f"{phone}.json"

            if session_file.exists():
                session_file.unlink()
            if user_file.exists():
                user_file.unlink()
            updated_users.pop(phone, None)

            print(Fore.YELLOW + f"🔥 Removed expired user: {phone}")

    save_users(updated_users)

async def run_bot(phone, api_id, api_hash):
    session_path = SESSIONS_DIR / phone
    client = TelegramClient(str(session_path), api_id, api_hash)

    @client.on(events.NewMessage())
    async def message_handler(event):
        print(Fore.CYAN + f"[{phone}] Message received in chat {event.chat_id}")

    await client.start()
    print(Fore.GREEN + f"[{phone}] Bot is now running.")
    await client.run_until_disconnected()

def login_and_run(phone, api_id, api_hash):
    session_path = SESSIONS_DIR / phone
    client = TelegramClient(str(session_path), api_id, api_hash)

    async def async_login():
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            code = input("Enter the code you received: ")
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = input("Two-step verification enabled. Enter your password: ")
                await client.sign_in(password=password)
        user = await client.get_me()
        users = load_users()

        users[phone] = {
            "name": user.first_name,
            "api_id": api_id,
            "api_hash": api_hash,
            "plan": users.get(phone, {}).get("plan", "free"),
            "expiry": users.get(phone, {}).get("expiry", "")
        }
        save_users(users)
        save_user_config(phone, users[phone])

        if not is_subscription_valid(users[phone]):
            print(Fore.RED + "❌ Access denied: Subscription is not active or has expired.")
            return

        print(Fore.GREEN + f"Login successful for {user.first_name} ({phone})")
        await client.disconnect()

        # Start bot after login only if subscription is valid
        await run_bot(phone, api_id, api_hash)

    asyncio.run(async_login())

# Prompt user to login
if __name__ == "__main__":
    ensure_dirs()
    cleanup_expired_users()
    phone = input("Enter your phone number (with country code): ")
    api_id = int(input("Enter your Telegram API ID: "))
    api_hash = input("Enter your Telegram API Hash: ")
    login_and_run(phone, api_id, api_hash)
