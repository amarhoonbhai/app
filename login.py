# login.py — OTP-only login (authenticate & save session; no menus)
import asyncio
import json
from getpass import getpass
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

CONFIG_DIR = Path("users"); CONFIG_DIR.mkdir(exist_ok=True)
SESS_DIR = Path("sessions"); SESS_DIR.mkdir(exist_ok=True)

TEMPLATE = {
    "phone": "",
    "api_id": 0,
    "api_hash": "",
    "targets": [],                  # set later from Saved Messages via .addgroup
    "send_interval_seconds": 30,    # .time (per-message delay)
    "forward_gap_seconds": 2,       # .gap (between group forwards; broadcast mode)
    "quiet_start": None,            # .quiet HH:MM
    "quiet_end": None,              # .quiet HH:MM
    "autonight": False,             # .autonight on|off (uses quiet or 23:00–07:00)
    "rotation_mode": "broadcast",   # .rotate off => broadcast | on => roundrobin
    "rot_index": 0
}

def write_config(phone: str, api_id: int, api_hash: str) -> Path:
    cfg_path = CONFIG_DIR / f"{phone}.json"
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = TEMPLATE.copy()
    cfg["phone"] = phone
    cfg["api_id"] = api_id
    cfg["api_hash"] = api_hash
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg_path

async def do_login(phone: str, api_id: int, api_hash: str):
    session_file = str(SESS_DIR / phone)
    client = TelegramClient(session_file, api_id, api_hash)
    await client.connect()

    try:
        try:
            authorized = await client.is_user_authorized()
        except TypeError:
            authorized = client.is_user_authorized()
        if authorized:
            me = await client.get_me()
            print(f"✔ Already logged in as {me.first_name} ({me.id}). Session: {session_file}")
            return

        await client.send_code_request(phone)
        for _ in range(3):
            code = input("Enter the OTP you received: ").strip()
            try:
                await client.sign_in(phone=phone, code=code)
                break
            except PhoneCodeInvalidError:
                print("✖ Invalid code. Try again.")
            except SessionPasswordNeededError:
                pw = getpass("Two-step password: ").strip()
                await client.sign_in(password=pw)
                break

        try:
            authorized = await client.is_user_authorized()
        except TypeError:
            authorized = client.is_user_authorized()
        if not authorized:
            raise RuntimeError("Login failed; not authorized.")
        me = await client.get_me()
        print(f"✅ Logged in as {me.first_name} ({me.id}). Session saved at: {session_file}")
    finally:
        await client.disconnect()

def main():
    print("=== Telegram OTP Login ===")
    phone = input("Phone (+countrycode): ").strip()
    if not phone:
        print("Phone is required."); return
    api_id_str = input("API_ID: ").strip()
    if not api_id_str.isdigit():
        print("API_ID must be a number."); return
    api_id = int(api_id_str)
    api_hash = getpass("API_HASH (hidden): ").strip()
    if not api_hash:
        print("API_HASH is required."); return

    cfg_path = write_config(phone, api_id, api_hash)
    print(f"• Config written: {cfg_path}")

    asyncio.run(do_login(phone, api_id, api_hash))

if __name__ == "__main__":
    main()
