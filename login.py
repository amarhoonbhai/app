#!/usr/bin/env python3
import os
import json
import getpass
import asyncio
import logging
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError, PhoneCodeInvalidError, PhoneNumberBannedError

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("login")

USERS_DIR = "users"
SESSIONS_DIR = "sessions"

def ask_env(name: str, prompt: str) -> str:
    v = os.getenv(name, "").strip()
    if v:
        return v
    val = input(prompt).strip()
    if not val:
        raise SystemExit(f"{name} is required.")
    return val

async def main():
    os.makedirs(USERS_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)

    api_id  = int(ask_env("API_ID",  "Enter API_ID: "))
    api_hash = ask_env("API_HASH",   "Enter API_HASH: ")
    phone    = input("Enter phone (+countrycode): ").strip()

    session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
    client = TelegramClient(session_path, api_id, api_hash)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            try:
                sent = await client.send_code_request(phone)
                code = input("Enter the code you received: ").strip()
                try:
                    await client.sign_in(phone=phone, code=code)
                except SessionPasswordNeededError:
                    pw = getpass.getpass("2FA password: ")
                    
