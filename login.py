
import os
import json
from datetime import datetime, timedelta
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError
from colorama import Fore, Style, init

init(autoreset=True)

SESSIONS_DIR = "sessions"
USERS_DIR = "users"
USERS_FILE = "users.json"

def ensure_dirs():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(USERS_DIR, exist_ok=True)
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'w') as f:
            json.dump({}, f)

def load_users():
    with open(USERS_FILE, 'r') as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def list_users(users):
    if not users:
        print(Fore.YELLOW + "No users logged in yet.")
        return
    print(Fore.CYAN + "Logged in users:")
    for phone, data in users.items():
        print(f"- {data['name']} ({phone})")

def save_user_config(phone, data):
    user_config = {
        "name": data["name"],
        "phone": phone,
        "api_id": data["api_id"],
        "api_hash": data["api_hash"],
        "cycle_delay_min": 15,
        "msg_delay_sec": 5,
        "groups": [],
        "plan_expiry": (datetime.now() + timedelta(days=30)).isoformat()
    }
    with open(os.path.join(USERS_DIR, f"{phone}.json"), 'w') as f:
        json.dump(user_config, f, indent=2)

def login_new_user(users):
    name = input("Enter a name for this user: ")
    api_id = input("API ID: ")
    api_hash = input("API HASH: ")
    phone = input("Phone number (with country code): ")

    session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
    client = TelegramClient(session_path, int(api_id), api_hash)

    client.connect()
    if not client.is_user_authorized():
        client.send_code_request(phone)
        code = input("Enter the code sent to Telegram: ")
        try:
            client.sign_in(phone, code)
        except SessionPasswordNeededError:
            password = input("Enter 2FA password: ")
            client.sign_in(password=password)

    print(Fore.GREEN + f"[✔] {name} logged in successfully.")
    users[phone] = {
        "name": name,
        "api_id": api_id,
        "api_hash": api_hash
    }
    save_users(users)
    save_user_config(phone, users[phone])
    client.disconnect()

def delete_user(users):
    phone = input("Enter the phone number of the user to delete: ")
    if phone in users:
        session_file = os.path.join(SESSIONS_DIR, f"{phone}.session")
        config_file = os.path.join(USERS_DIR, f"{phone}.json")
        if os.path.exists(session_file):
            os.remove(session_file)
        if os.path.exists(config_file):
            os.remove(config_file)
        users.pop(phone)
        save_users(users)
        print(Fore.RED + f"User {phone} deleted.")
    else:
        print(Fore.YELLOW + "User not found.")

def start():
    ensure_dirs()
    while True:
        users = load_users()
        print(Style.BRIGHT + "\n--- Telethon Multi-User Manager ---")
        print("1. List users")
        print("2. Login new user")
        print("3. Delete user")
        print("4. Exit")
        choice = input("Choose an option: ").strip()
        if choice == '1':
            list_users(users)
        elif choice == '2':
            login_new_user(users)
        elif choice == '3':
            delete_user(users)
        elif choice == '4':
            print("Goodbye.")
            break
        else:
            print("Invalid choice. Try again.")

if __name__ == "__main__":
    start()
