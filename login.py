# login.py — minimal menu: Add user (OTP), Delete user, Exit
import asyncio
import json
import os
import subprocess
import sys
from getpass import getpass
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

# ---- paths ----
CONFIG_DIR = Path("users"); CONFIG_DIR.mkdir(exist_ok=True)
SESS_DIR = Path("sessions"); SESS_DIR.mkdir(exist_ok=True)
RUNNER_PID = Path("runner.pid")

# ---- default config template (other fields used by runner.py) ----
TEMPLATE = {
    "phone": "",
    "api_id": 0,
    "api_hash": "",
    "targets": [],
    "send_interval_seconds": 30,
    "forward_gap_seconds": 2,
    "quiet_start": None,
    "quiet_end": None,
    "autonight": False,
    "rotation_mode": "broadcast",
    "rot_index": 0,
}

# ==== runner helpers ====
def _alive_pid(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def ensure_runner():
    """
    Start runner.py if not already running (uses runner.pid written by runner).
    """
    if RUNNER_PID.exists():
        try:
            pid = int(RUNNER_PID.read_text().strip())
            if _alive_pid(pid):
                print(f"[runner] already running (pid {pid})")
                return
        except Exception:
            pass
    print("[runner] starting…")
    # detach runner; silence output
    subprocess.Popen([sys.executable, "runner.py"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

# ==== config & login ====
def write_config(phone: str, api_id: int, api_hash: str) -> Path:
    cfg_path = CONFIG_DIR / f"{phone}.json"
    cfg = TEMPLATE.copy()
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            try:
                cfg.update(json.load(f))
            except Exception:
                pass
    cfg["phone"] = phone
    cfg["api_id"] = api_id
    cfg["api_hash"] = api_hash
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg_path

async def do_otp_login(phone: str, api_id: int, api_hash: str):
    session_file = str(SESS_DIR / phone)
    client = TelegramClient(session_file, api_id, api_hash)
    await client.connect()

    # Handle both coroutine & sync is_user_authorized (telethon version differences)
    try:
        authorized = await client.is_user_authorized()
    except TypeError:
        authorized = client.is_user_authorized()

    if authorized:
        me = await client.get_me()
        print(f"✔ already logged in as {getattr(me, 'first_name', '')} ({me.id})")
        await client.disconnect()
        return

    # Send OTP and sign in
    await client.send_code_request(phone)
    for _ in range(3):
        code = input("Enter Telegram OTP: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
            break
        except PhoneCodeInvalidError:
            print("✖ invalid code, try again.")
        except SessionPasswordNeededError:
            pw = getpass("Two-step password: ").strip()
            await client.sign_in(password=pw)
            break

    try:
        try:
            authorized = await client.is_user_authorized()
        except TypeError:
            authorized = client.is_user_authorized()
        if not authorized:
            raise RuntimeError("login failed; not authorized")
        me = await client.get_me()
        print(f"✅ logged in as {getattr(me, 'first_name', '')} ({me.id}); session saved at {session_file}")
    finally:
        await client.disconnect()

# ==== menu flows ====
def flow_add_user():
    phone = input("Phone (+countrycode): ").strip()
    if not phone:
        print("phone is required."); return
    api_id_str = input("API_ID: ").strip()
    if not api_id_str.isdigit():
        print("API_ID must be a number."); return
    api_id = int(api_id_str)
    api_hash = getpass("API_HASH (hidden): ").strip()
    if not api_hash:
        print("API_HASH is required."); return

    cfg_path = write_config(phone, api_id, api_hash)
    print(f"• config written: {cfg_path}")

    asyncio.run(do_otp_login(phone, api_id, api_hash))

    # auto-start runner so you can test the new user immediately
    ensure_runner()
    print("Tip: in Telegram, send `.status` (from any chat) to verify settings.\n"
          "      Add groups via `.addgroup @g1,@g2` in Saved or any chat (self).")

def flow_delete_user():
    # Show available users to help pick
    files = sorted(CONFIG_DIR.glob("*.json"))
    if files:
        print("Existing users:")
        for p in files:
            print(" -", p.stem)
    phone = input("Phone to delete (+countrycode): ").strip()
    if not phone:
        print("phone is required."); return
    cfg_file = CONFIG_DIR / f"{phone}.json"
    sess_file = SESS_DIR / phone
    removed_any = False
    if cfg_file.exists():
        cfg_file.unlink()
        print("• deleted config:", cfg_file.name)
        removed_any = True
    if sess_file.exists():
        # Telethon session is usually a .session file; our name has no ext, so both may exist
        try:
            sess_file.unlink()
            print("• deleted session:", sess_file.name)
            removed_any = True
        except Exception:
            pass
        # Also try session with .session suffix
        ss = Path(str(sess_file) + ".session")
        if ss.exists():
            ss.unlink()
            print("• deleted session:", ss.name)
            removed_any = True
    if not removed_any:
        print("nothing to delete for that phone")

# ==== UI ====
def main():
    ensure_runner()  # keep runner alive in background
    while True:
        print("\n==== Forwarder Login ====")
        print("  1) Add user (OTP login)")
        print("  2) Delete user")
        print("  3) Exit")
        choice = input("➤ Choose: ").strip()
        if choice == "1":
            flow_add_user()
        elif choice == "2":
            flow_delete_user()
        elif choice == "3":
            sys.exit(0)
        else:
            print("invalid choice.")

if __name__ == "__main__":
    main()
