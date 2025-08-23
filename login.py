#!/usr/bin/env python3
"""
Interactive login for a Telegram account/session.

- Creates a Telethon session under SESSIONS_DIR.
- Ensures a matching user JSON exists under USERS_DIR.
- Works with 2FA.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

from telethon.sync import TelegramClient
from telethon import errors

from app.config import load_config
from app.storage import Storage, UserConfig


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        print("\nInput aborted.", file=sys.stderr)
        sys.exit(1)


def main():
    cfg = load_config()

    # Get phone from env or prompt
    phone = (sys.argv[1].strip() if len(sys.argv) > 1 else None) or ask("Phone number (e.g. +15551234567): ")
    if not phone.startswith("+"):
        print("Tip: include the country code, e.g. +1...", file=sys.stderr)

    # Prepare paths
    sessions_dir: Path = cfg.sessions_dir
    users_dir: Path = cfg.users_dir
    sessions_dir.mkdir(parents=True, exist_ok=True)
    users_dir.mkdir(parents=True, exist_ok=True)

    session_path = sessions_dir / f"{phone}.session"
    user_json_path = users_dir / f"{phone}.json"

    # Login
    print(f"\nCreating/using session at: {session_path}")
    client = TelegramClient(str(session_path), cfg.api_id, cfg.api_hash)

    def code_cb():
        return ask("Enter the login code you just received: ")

    def pwd_cb(hint: str):
        return ask(f"Two-step password required (hint: {hint!r}). Enter password: ")

    try:
        # start() drives the sign-in flow; it will ask for code and password via our callbacks
        client.start(
            phone=phone,
            code_callback=lambda: code_cb(),
            password=lambda hint: pwd_cb(hint or ""),
        )
    except errors.ApiIdInvalidError:
        print("Your API_ID/API_HASH look invalid. Check environment variables.", file=sys.stderr)
        sys.exit(1)
    except errors.PhoneCodeInvalidError:
        print("Invalid code. Please re-run and enter the correct code.", file=sys.stderr)
        sys.exit(1)
    except errors.PasswordHashInvalidError:
        print("Wrong 2FA password. Please re-run with the correct password.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Verify
    me = client.get_me()
    print(f"\n✅ Logged in as: {getattr(me, 'first_name', '')} @{getattr(me, 'username', '')} (id={me.id})")

    # Ensure user JSON exists
    if not user_json_path.exists():
        default_expiry = (date.today() + timedelta(days=30)).isoformat()
        cfg_obj = UserConfig(
            phone=phone,
            plan_expiry=default_expiry,
            cycle_minutes=10,
            groups=set(),
        )
        Storage(user_json_path).save(cfg_obj)
        print(f"Created user config: {user_json_path} (plan_expiry={default_expiry})")
    else:
        print(f"User config already exists: {user_json_path}")

    print("\nDone. You can now run:  python runner.py")
    client.disconnect()


if __name__ == "__main__":
    main()
  
