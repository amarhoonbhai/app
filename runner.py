#!/usr/bin/env python3
"""
Telegram forwarder runner.

- Loads user config and session.
- Listens for dot-commands (admin-only if ADMIN_USER_IDS set).
- Supports `.addgroup` with folder links, invite links, usernames, or IDs.
- Auto-joins with safe gaps + per-hour cap.
- Persistent join queue + ETA estimation.
- Forwards messages from stored groups (if you extend forwarding logic).
"""

import asyncio
import random
import re
import time
from pathlib import Path

import httpx
from telethon import TelegramClient, events, functions, types, errors

from app.config import load_config
from app.storage import Storage, JoinQueue

cfg = load_config()

# === Storage paths ===
users_dir = cfg.users_dir
phone = None
if users_dir.exists():
    files = list(users_dir.glob("*.json"))
    if not files:
        raise SystemExit("No user config found. Run login.py first.")
    user_json_path = files[0]  # assume first user if multiple
    phone = user_json_path.stem
else:
    raise SystemExit("No users/ directory found. Run login.py first.")

storage = Storage(user_json_path)
join_queue = JoinQueue(user_json_path.with_name(user_json_path.stem + "_join_queue.json"))

# === Client ===
sessions_dir = cfg.sessions_dir
session_path = sessions_dir / f"{phone}.session"
client = TelegramClient(str(session_path), cfg.api_id, cfg.api_hash)

# === Regex patterns ===
ADDLIST_RE = re.compile(r'(?:https?://)?t\.me/(?:addlist/)([A-Za-z0]()_
                        
