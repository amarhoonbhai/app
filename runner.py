import os
import json
import asyncio
import logging
import sqlite3
import re
import random
import signal
import tempfile
import shutil
from datetime import datetime, date, time, timedelta
from typing import Tuple, List, Optional, Any

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None  # will fall back to local time without TZ

from telethon import TelegramClient, events
from telethon.errors import (
    SessionPasswordNeededError, 
    RPCError, 
    FloodWaitError, 
    ChatWriteForbiddenError,
    SlowModeWaitError
)
import telethon.utils as tel_utils
from colorama import Fore, Style, init

init(autoreset=True)


# =========================
# Auto-Night configuration
# =========================
AUTONIGHT_PATH = os.path.join(os.path.dirname(__file__), "autonight.json")
DEFAULT_AUTONIGHT = {
    "enabled": True,
    "start": "00:00",        # 24h format HH:MM
    "end": "06:00",          # 24h format HH:MM
    "tz": "Asia/Kolkata"
}


def atomic_save_json(path: str, data: Any) -> bool:
    """Save JSON data to a file atomically using a temporary file."""
    temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # On Windows, os.replace might fail if the destination exists and is open
        # but shutil.move/os.replace is generally the way to go.
        try:
            os.replace(temp_path, path)
        except OSError:
            # Fallback if os.replace fails (e.g. permission issues on some environments)
            shutil.move(temp_path, path)
        return True
    except Exception as e:
        logger.error(f"Failed to save JSON to {path}: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False

def _load_autonight() -> dict:
    cfg = DEFAULT_AUTONIGHT.copy()
    try:
        if os.path.exists(AUTONIGHT_PATH):
            with open(AUTONIGHT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg.update({k: data.get(k, cfg[k]) for k in cfg})
    except Exception:
        pass
    return cfg

def _save_autonight(cfg: dict) -> None:
    atomic_save_json(AUTONIGHT_PATH, cfg)

def _parse_hhmm(s: str) -> time:
    s = s.strip()
    # Accept "7", "07", "7:00", "07:00"
    if re.fullmatch(r"\d{1,2}", s):
        h = int(s)
        if not (0 <= h <= 23):
            raise ValueError("Hour must be 0..23")
        return time(h, 0)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        raise ValueError("Time must be HH or HH:MM (24h)")
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        raise ValueError("Invalid time")
    return time(h, mm)

def _get_now_tz(tz_name: str) -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    # Fallback: naive local time
    return datetime.now()

def _get_cycle_seconds_with_jitter(cycle_min: float) -> int:
    if cycle_min in (7, 20):  # Both legacy default 20 and new default 7 map to 6-8 min (360-480s)
        return random.randint(360, 480)
    else:
        # Custom cycle: add ±15% jitter
        seconds = int(cycle_min * 60)
        jitter = int(seconds * 0.15)
        return random.randint(seconds - jitter, seconds + jitter)

def _in_window(now_t: time, start_t: time, end_t: time) -> bool:
    """True if now is within [start, end) with midnight wrap support."""
    if start_t <= end_t:
        return start_t <= now_t < end_t
    # crosses midnight, e.g., 23:00 -> 07:00
    return (now_t >= start_t) or (now_t < end_t)

def _seconds_until_quiet_end(cfg: dict) -> int:
    """Return seconds until the end of quiet window (>= 1), assuming we are currently in quiet."""
    tz = cfg.get("tz") or DEFAULT_AUTONIGHT["tz"]
    now = _get_now_tz(tz)
    start_t = _parse_hhmm(cfg.get("start", DEFAULT_AUTONIGHT["start"]))
    end_t   = _parse_hhmm(cfg.get("end", DEFAULT_AUTONIGHT["end"]))
    today = now.date()

    # Compute next end datetime
    if start_t <= end_t:
        # non-wrapping window (e.g., 02:00 -> 05:00)
        end_dt = datetime.combine(today, end_t, tzinfo=now.tzinfo)
        if now.time() >= end_t:
            end_dt = end_dt + timedelta(days=1)
    else:
        # wrapping window (e.g., 23:00 -> 07:00)
        if now.time() < end_t:
            end_dt = datetime.combine(today, end_t, tzinfo=now.tzinfo)
        else:
            end_dt = datetime.combine(today + timedelta(days=1), end_t, tzinfo=now.tzinfo)

    seconds = int((end_dt - now).total_seconds())
    return max(1, seconds)

def autonight_is_quiet(cfg: dict) -> bool:
    if not cfg.get("enabled", True):
        return False
    try:
        now = _get_now_tz(cfg.get("tz", DEFAULT_AUTONIGHT["tz"]))
        start_t = _parse_hhmm(cfg.get("start", DEFAULT_AUTONIGHT["start"]))
        end_t   = _parse_hhmm(cfg.get("end", DEFAULT_AUTONIGHT["end"]))
        return _in_window(now.time(), start_t, end_t)
    except Exception:
        # Fail open if config broken
        return False

def autonight_status_text(cfg: dict) -> str:
    state = "ACTIVE ✅" if cfg.get("enabled", True) else "DISABLED ❌"
    return (
        f"🌙 Auto-Night: **{state}**\n"
        f"Window: **{cfg.get('start','00:00')} → {cfg.get('end','06:00')}**\n"
        f"TZ: **{cfg.get('tz','Asia/Kolkata')}**"
    )


def autonight_parse_command(arg: str, cfg: dict) -> Tuple[str, dict]:
    """
    Returns (message_text, updated_cfg or same).
    Supported:
      .night
      .night on | off
      .night 23:00 to 07:00   (also supports -, – , —)
      .night 23-7
    """
    arg = (arg or "").strip()
    if not arg:
        return (autonight_status_text(cfg), cfg)

    low = arg.lower()
    if low in {"on", "enable", "enabled"}:
        cfg = cfg.copy()
        cfg["enabled"] = True
        _save_autonight(cfg)
        return ("✅ Auto-Night **enabled**.\n" + autonight_status_text(cfg), cfg)

    if low in {"off", "disable", "disabled"}:
        cfg = cfg.copy()
        cfg["enabled"] = False
        _save_autonight(cfg)
        return ("🚫 Auto-Night **disabled**.\n" + autonight_status_text(cfg), cfg)

    # Time range
    m = re.fullmatch(
        r"\s*(\d{1,2}(?::\d{2})?)\s*(?:to|–|—|-)\s*(\d{1,2}(?::\d{2})?)\s*",
        arg
    )
    if not m:
        return (
            "❗ Format: `.night 23:00 to 07:00`\n"
            "Also works with a dash: `.night 23:00-07:00` (24-hour times).",
            cfg
        )

    start_raw, end_raw = m.group(1), m.group(2)
    try:
        start_t = _parse_hhmm(start_raw)
        end_t   = _parse_hhmm(end_raw)
    except ValueError as e:
        return (f"❗ {e}", cfg)

    cfg = cfg.copy()
    cfg["start"] = f"{start_t.hour:02d}:{start_t.minute:02d}"
    cfg["end"]   = f"{end_t.hour:02d}:{end_t.minute:02d}"
    _save_autonight(cfg)
    return (f"🕒 Auto-Night window updated:\n**{cfg['start']} → {cfg['end']}** ({cfg.get('tz','Asia/Kolkata')})\n" + autonight_status_text(cfg), cfg)

# =========================
# Original forwarder logic
# =========================

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

USERS_DIR = "users"
SESSIONS_DIR = "sessions"
clients = {}
started_phones = set()
active_bots = {}

async def interruptible_sleep(get_target_time, tz_name: str):
    while True:
        target = get_target_time()
        if not target:
            break
        now = _get_now_tz(tz_name)
        if now >= target:
            break
        rem = (target - now).total_seconds()
        if rem <= 0:
            break
        # Sleep at most 1 second to remain highly responsive
        await asyncio.sleep(min(rem, 1.0))

# Global Auto-Night config (shared across accounts)
AUTONIGHT_CFG = _load_autonight()

async def run_user_bot(config):
    phone = config["phone"]
    if phone in started_phones:
        return

    # Track this session to avoid concurrent start attempts
    started_phones.add(phone)

    session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
    api_id = int(config["api_id"])
    api_hash = config["api_hash"]
    delay = config.get("msg_delay_sec", 20)
    cycle = config.get("cycle_delay_min", 7)

    user_state = {
        "delay": delay,   # seconds between forwards
        "cycle": cycle,   # minutes between cycles
        "use_copy": True, # Copy instead of Forward (removes 'forwarded from' tag)
        "success_total": 0,
        "fail_total": 0,
        "current_cycle_success": 0,
        "current_cycle_fail": 0,
        "next_msg_at": None,
        "status": "Idle 😴",
        "logs": [],
        "start_time": _get_now_tz(AUTONIGHT_CFG.get("tz", DEFAULT_AUTONIGHT["tz"]))
    }

    active_bots[phone] = {
        "client": None,
        "state": user_state,
        "config": config
    }

    def log_event(msg):
        tz = AUTONIGHT_CFG.get("tz", DEFAULT_AUTONIGHT["tz"])
        now = _get_now_tz(tz)
        ts = now.strftime("%H:%M:%S")
        
        # Determine color and icon
        color = Fore.WHITE
        icon = "ℹ"
        
        lower_msg = msg.lower()
        if "success" in lower_msg:
            color = Fore.GREEN
            icon = "✔"
        elif "failed" in lower_msg or "error" in lower_msg or "floodwait" in lower_msg:
            color = Fore.RED
            icon = "✖"
        elif "processing" in lower_msg:
            color = Fore.CYAN
            icon = "📡"
        
        clean_msg = msg.replace("**", "") # Remove markdown for console
        print(f"{Fore.MAGENTA}[{ts}] {color}{icon} {Fore.WHITE}{clean_msg}")
        
        user_state["logs"].append(f"[{ts}] {msg}")
        if len(user_state["logs"]) > 10:
            user_state["logs"].pop(0)
        logger.info(f"[{phone}] {msg}")

    client = TelegramClient(session_path, api_id, api_hash)
    active_bots[phone]["client"] = client
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error(f"[{phone}] Session revoked or unauthorized.")
            return

        me = await client.get_me()
        me_id = me.id
        log_event(f"Bot connected: {config.get('name','N/A')} (ID: {me_id})")
    except Exception as e:
        logger.error(f"[{phone}] Connection failure: {e}")
        return


    async def delayed_delete(chat_id, msg_ids, delay=40):
        await asyncio.sleep(delay)
        try:
            await client.delete_messages(chat_id, msg_ids)
        except Exception:
            pass

    @client.on(events.NewMessage(outgoing=True))
    async def command_handler(event):
        text = (event.raw_text or "").strip()
        if not text.startswith("."):
            return

        # Setup auto-delete for command and its responses
        orig_respond = event.respond
        async def auto_delete_respond(*args, **kwargs):
            resp = await orig_respond(*args, **kwargs)
            if resp:
                asyncio.create_task(delayed_delete(event.chat_id, [event.id, resp.id]))
            return resp
        event.respond = auto_delete_respond

        # Ensure command itself is deleted after 40s even if no respond() is called
        asyncio.create_task(delayed_delete(event.chat_id, [event.id]))

        if text.startswith(".time"):
            value = int(''.join(filter(str.isdigit, text)) or "0")
            if value <= 0:
                await event.respond("❗ Usage: `.time 7m` or `.time 1h`")
                return
            if 'h' in text.lower():
                value = value * 60
            
            if value < 5:
                await event.respond("⚠️ Minimum cycle interval is **5 minutes**. Setting to 5m.")
                value = 5
                
            user_state["cycle"] = value
            config["cycle_delay_min"] = value
            atomic_save_json(os.path.join(USERS_DIR, f"{phone}.json"), config)
            
            tz = AUTONIGHT_CFG.get("tz", DEFAULT_AUTONIGHT["tz"])
            sleep_seconds = _get_cycle_seconds_with_jitter(value)
            user_state["next_msg_at"] = _get_now_tz(tz) + timedelta(seconds=sleep_seconds)
            await event.respond(f"✅ Cycle delay set to **{value} minutes**")

        elif text.startswith(".delay"):
            value = int(''.join(filter(str.isdigit, text)) or "0")
            if value <= 0:
                await event.respond("❗ Usage: `.delay 30` (seconds)")
                return
            
            if value < 10:
                await event.respond("⚠️ Minimum message delay is **10 seconds**. Setting to 10s.")
                value = 10
                
            user_state["delay"] = value
            config["msg_delay_sec"] = value
            atomic_save_json(os.path.join(USERS_DIR, f"{phone}.json"), config)
            
            tz = AUTONIGHT_CFG.get("tz", DEFAULT_AUTONIGHT["tz"])
            user_state["next_msg_at"] = _get_now_tz(tz) + timedelta(seconds=value)
            await event.respond(f"✅ Message delay set to **{value} seconds** (Randomized ±15%)")


        elif text.startswith(".status"):
            await event.respond(
                "📊 Status:\n"
                f"• Cycle Delay: **{user_state['cycle']} minutes**\n"
                f"• Message Delay: **{user_state['delay']} seconds**\n"
                f"• Success: **{user_state['success_total']}**\n"
                f"• Failed: **{user_state['fail_total']}**\n\n"
                + autonight_status_text(AUTONIGHT_CFG)
            )

        elif text.startswith(".stats"):
            tz = AUTONIGHT_CFG.get("tz", DEFAULT_AUTONIGHT["tz"])
            now = _get_now_tz(tz)
            uptime = str(now - user_state["start_time"]).split('.')[0]
            
            # Formatting next delivery time
            next_msg_str = "N/A"
            if user_state["next_msg_at"]:
                next_msg_str = user_state["next_msg_at"].strftime("%H:%M:%S")
            
            # Label change based on status
            next_label = "🕒 Next Delivery"
            if "Idle" in user_state["status"] or "Waiting" in user_state["status"]:
                next_label = "🕒 Next Cycle"
            elif "Msg" in user_state["status"]:
                next_label = "🕒 Next Group"

            log_text = "\n".join(user_state["logs"][-5:]) if user_state["logs"] else "No logs yet."
            
            reply = (
                f"📊 **System Statistics**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Account:** {config.get('name')} ({phone})\n"
                f"⏱ **Uptime:** `{uptime}`\n"
                f"🔄 **Status:** {user_state['status']}\n"
                f"📍 **Groups:** {len(config.setdefault('groups', []))}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"✅ **Total Success:** `{user_state['success_total']}`\n"
                f"❌ **Total Failed:** `{user_state['fail_total']}`\n"
                f"{next_label}: `{next_msg_str}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📜 **Recent Logs:**\n`{log_text}`"
            )
            await event.respond(reply)

        elif text.startswith(".info"):
            me = await client.get_me()
            expiry = "Lifetime"
            reply = (
                f"❀ User Info:\n"
                f"❀ Name: {config.get('name')}\n"
                f"❀ Cycle Delay: {user_state['cycle']} min\n"
                f"❀ Message Delay: {user_state['delay']} sec\n"
                f"❀ Groups: {len(config.setdefault('groups', []))}\n"
                f"❀ Plan Access: {expiry}\n\n"
                + autonight_status_text(AUTONIGHT_CFG)
            )

            await event.respond(reply)

        elif text.startswith(".addgroup"):
            links = re.findall(r'https://t\.me/\S+', text)
            if not links:
                await event.respond("⚠️ No valid group links found.")
                return
            added, skipped = [], []
            groups_list = config.setdefault("groups", [])
            for link in links:
                if link not in groups_list:
                    groups_list.append(link)
                    added.append(link)
                else:
                    skipped.append(link)
            atomic_save_json(os.path.join(USERS_DIR, f"{phone}.json"), config)
            msg = []
            if added:
                msg.append(f"✅ Added **{len(added)}** new group(s).")
            if skipped:
                msg.append(f"⚠️ Skipped **{len(skipped)}** duplicate(s).")
            await event.respond("\n".join(msg) or "No changes.")

        elif text.startswith(".delgroup"):
            links = re.findall(r'https://t\.me/\S+', text)
            if not links:
                await event.respond("⚠️ Usage: `.delgroup <link1> <link2> ...`")
                return
            removed, skipped = [], []
            groups_list = config.setdefault("groups", [])
            for link in links:
                normalized_link = link.rstrip('/')
                found = None
                for g in groups_list:
                    if g.rstrip('/') == normalized_link:
                        found = g
                        break
                if found:
                    groups_list.remove(found)
                    removed.append(link)
                else:
                    skipped.append(link)
            atomic_save_json(os.path.join(USERS_DIR, f"{phone}.json"), config)
            msg = []
            if removed:
                msg.append(f"✅ Removed **{len(removed)}** group(s).")
            if skipped:
                msg.append(f"⚠️ Skipped **{len(skipped)}** group(s) (not in list).")
            await event.respond("\n".join(msg) or "No changes.")

        elif text.startswith(".groups"):
            groups_list = config.setdefault("groups", [])
            if not groups_list:
                await event.respond("📋 No groups configured.")
            else:
                lines = [f"❀ Groups ({len(groups_list)}):"]
                for idx, g in enumerate(groups_list, 1):
                    lines.append(f"{idx}. {g}")
                
                # Chunk sending to avoid Telegram MessageTooLongError
                current_chunk = []
                current_len = 0
                for line in lines:
                    if current_len + len(line) + 1 > 4000:
                        await event.respond("\n".join(current_chunk))
                        current_chunk = [line]
                        current_len = len(line)
                    else:
                        current_chunk.append(line)
                        current_len += len(line) + 1
                if current_chunk:
                    await event.respond("\n".join(current_chunk))

        elif text.startswith(".night"):
            # .night, .night on/off, .night 23:00 to 07:00
            arg = text[6:].strip() if len(text) > 6 else ""
            msg, new_cfg = autonight_parse_command(arg, AUTONIGHT_CFG)
            # Update global config in memory
            for k in list(AUTONIGHT_CFG.keys()):
                AUTONIGHT_CFG[k] = new_cfg.get(k, AUTONIGHT_CFG[k])
            await event.respond(msg)

        elif text.startswith(".mode"):
            if "forward" in text.lower():
                user_state["use_copy"] = False
                await event.respond("✅ Mode set to **Forward** (will show 'Forwarded from...')")
            else:
                user_state["use_copy"] = True
                await event.respond("✅ Mode set to **Copy** (looks like a fresh message)")


        elif text.startswith(".join"):
            links = re.findall(r'https://t\.me/\S+', text)
            if not links:
                await event.respond("⚠️ Usage: `.join <link1> <link2> ...`")
                return
            
            await event.respond(f"🔄 Attempting to join {len(links)} groups...")
            success, fail = 0, 0
            for link in links:
                try:
                    clean_link = link.strip().rstrip('/')
                    # Handle both private and public invite links robustly
                    if "t.me/+" in clean_link:
                        hash_val = clean_link.split('+')[-1]
                        from telethon.tl.functions.messages import ImportChatInviteRequest
                        await client(ImportChatInviteRequest(hash_val))
                    elif "t.me/joinchat/" in clean_link:
                        hash_val = clean_link.split('joinchat/')[-1]
                        from telethon.tl.functions.messages import ImportChatInviteRequest
                        await client(ImportChatInviteRequest(hash_val))
                    else:
                        username = clean_link.split('/')[-1]
                        from telethon.tl.functions.channels import JoinChannelRequest
                        await client(JoinChannelRequest(username))
                    success += 1
                    await asyncio.sleep(random.randint(10, 20)) # Wait between joins to prevent flood
                except Exception as e:
                    logger.error(f"Join error {link}: {e}")
                    fail += 1
            await event.respond(f"✅ Done! Joined: **{success}**, Failed: **{fail}**")


        elif text.startswith(".help"):
            await event.respond(
                "🎁 **TELETHON V5 ELITE ADVANCED MODULE**\n\n"
                "🛠 **Timing & Mode:**\n"
                "• `.time <m|h>` — Set cycle interval\n"
                "• `.delay <sec>` — Set message spacing\n"
                "• `.mode <copy|forward>` — Switch sending style\n"
                "\n🛰 **Management:**\n"
                "• `.addgroup <url>` — Add target group\n"
                "• `.delgroup <url>` — Remove group\n"
                "• `.groups` — Show target list\n"
                "• `.join <url>` — Join new groups\n"
                "\n📊 **Stats & Info:**\n"
                "• `.stats` — Detailed performance\n"
                "• `.status` | `.info` | `.night`"
            )

    async def forward_loop():
        while True:
            tz = AUTONIGHT_CFG.get("tz", DEFAULT_AUTONIGHT["tz"])
            try:
                # 🌙 If within quiet hours, check every minute if still quiet
                while autonight_is_quiet(AUTONIGHT_CFG):
                    user_state["status"] = "Quiet Mode 🌙"
                    secs_to_end = _seconds_until_quiet_end(AUTONIGHT_CFG)
                    # Sleep max 60s at a time to allow immediate wake-up if config changes
                    sleep_step = min(secs_to_end, 60)
                    if sleep_step > 0:
                        await asyncio.sleep(sleep_step)
                    else:
                        break # safety break
                
                # 🎯 Check if target groups are configured first
                groups_list = config.setdefault("groups", [])
                if not groups_list:
                    log_event("No target groups configured.")
                    user_state["status"] = "Idle (No Groups) 😴"
                    now = _get_now_tz(tz)
                    user_state["next_msg_at"] = now + timedelta(minutes=user_state["cycle"])
                    await interruptible_sleep(lambda: user_state["next_msg_at"], tz)
                    continue

                # 💎 Fetch all messages from Saved Messages (up to 100)
                user_state["status"] = "Fetching Msgs 🔍"
                messages = await client.get_messages("me", limit=100)
                
                # Filter out messages that cannot be sent (empty text & no media)
                valid_messages = [m for m in messages if m.text or m.media]

                if not valid_messages:
                    log_event("No valid messages in Saved Messages.")
                    user_state["status"] = "Idle (No Msg) 😴"
                    now = _get_now_tz(tz)
                    user_state["next_msg_at"] = now + timedelta(minutes=user_state["cycle"])
                    await interruptible_sleep(lambda: user_state["next_msg_at"], tz)
                    continue

                # Forward messages one by one
                for msg_idx, msg in enumerate(valid_messages, 1):
                    log_event(f"Processing message {msg_idx}/{len(valid_messages)}")
                    interrupted_by_night = False
                    
                    user_state["current_cycle_success"] = 0
                    user_state["current_cycle_fail"] = 0

                    groups_list = config.setdefault("groups", [])
                    for i, group in enumerate(groups_list, 1):
                        # If night starts mid-cycle, break early
                        if autonight_is_quiet(AUTONIGHT_CFG):
                            interrupted_by_night = True
                            break

                        user_state["status"] = f"Msg {msg_idx} -> Grp {i}/{len(groups_list)} 📡"
                        send_start = _get_now_tz(tz)
                        custom_sleep_done = False
                        
                        try:
                            if user_state["use_copy"]:
                                # 🌈 Copy Mode
                                caption = msg.text or ""
                                if msg.media:
                                    await client.send_file(group, msg.media, caption=caption)
                                else:
                                    await client.send_message(group, caption)
                            else:
                                # 🔄 Forward Mode
                                await client.forward_messages(group, msg)

                            user_state["success_total"] += 1
                            user_state["current_cycle_success"] += 1
                            log_event(f"Msg {msg_idx} Success -> {group}")

                        except FloodWaitError as e:
                             log_event(f"FloodWait! Sleeping {e.seconds}s. Increasing delay.")
                             user_state["status"] = f"FloodWait ⏳ ({e.seconds}s)"
                             user_state["delay"] = min(user_state["delay"] + 20, 600)
                             config["msg_delay_sec"] = user_state["delay"]
                             atomic_save_json(os.path.join(USERS_DIR, f"{phone}.json"), config)
                             now = _get_now_tz(tz)
                             user_state["next_msg_at"] = now + timedelta(seconds=e.seconds + 5)
                             await interruptible_sleep(lambda: user_state["next_msg_at"], tz)
                             custom_sleep_done = True
                        except SlowModeWaitError as e:
                             log_event(f"Slowmode in {group}. Waiting {e.seconds}s")
                             user_state["status"] = f"Slowmode ⏳ ({e.seconds}s)"
                             now = _get_now_tz(tz)
                             user_state["next_msg_at"] = now + timedelta(seconds=e.seconds + 2)
                             await interruptible_sleep(lambda: user_state["next_msg_at"], tz)
                             custom_sleep_done = True
                        except ChatWriteForbiddenError:
                            log_event(f"No permission in {group}")
                            user_state["fail_total"] += 1
                            user_state["current_cycle_fail"] += 1
                        except Exception as e:
                            log_event(f"Failed {group}: {type(e).__name__}")
                            user_state["fail_total"] += 1
                            user_state["current_cycle_fail"] += 1

                        # Always sleep the delay between groups (unless custom sleep occurred or it is the last group)
                        if i < len(groups_list) and not custom_sleep_done:
                            wait_time = user_state["delay"] * random.uniform(0.9, 1.1)
                            # Subtract the message-sending duration to avoid latency drift accumulation
                            elapsed = (_get_now_tz(tz) - send_start).total_seconds()
                            remaining_wait = max(0.1, wait_time - elapsed)
                            
                            now = _get_now_tz(tz)
                            user_state["next_msg_at"] = now + timedelta(seconds=remaining_wait)
                            await interruptible_sleep(lambda: user_state["next_msg_at"], tz)
                        elif i == len(groups_list):
                            user_state["next_msg_at"] = None

                    if interrupted_by_night:
                        break # exit message loop and go back to outer while True

                    # Adaptive optimization: If cycle was perfect, slightly reduce delay (but not below 20s)
                    if user_state["current_cycle_fail"] == 0 and user_state["current_cycle_success"] > 0:
                        if user_state["delay"] > 25:
                            user_state["delay"] -= 2
                            config["msg_delay_sec"] = user_state["delay"]
                            atomic_save_json(os.path.join(USERS_DIR, f"{phone}.json"), config)

                    log_event(f"Msg {msg_idx} cycle complete. Success: {user_state['current_cycle_success']}, Fail: {user_state['current_cycle_fail']}")
                    
                    # Interval delay between different messages
                    if msg_idx < len(valid_messages):
                        user_state["status"] = f"Waiting for next msg ⏳"
                        now = _get_now_tz(tz)
                        user_state["next_msg_at"] = now + timedelta(minutes=user_state["cycle"])
                        await interruptible_sleep(lambda: user_state["next_msg_at"], tz)

                # After all messages are processed, wait the cycle delay again before checking for new messages
                user_state["status"] = "Idle 😴"
                now = _get_now_tz(tz)
                user_state["next_msg_at"] = now + timedelta(minutes=user_state["cycle"])
                await interruptible_sleep(lambda: user_state["next_msg_at"], tz)

            except Exception as e:
                log_event(f"Error in forward loop: {e}")
                await asyncio.sleep(60)


    asyncio.create_task(forward_loop())
    try:
        await client.run_until_disconnected()
    except Exception as e:
        logger.error(f"[{phone}] Disconnected with error: {e}")
    finally:
        active_bots.pop(phone, None)
        try:
            await client.disconnect()
        except Exception:
            pass
        if phone in started_phones:
            started_phones.remove(phone)
        log_event(f"Bot for {phone} stopped.")

async def user_loader():
    config_mtimes = {} # path -> last_mtime
    while True:
        if not os.path.exists(USERS_DIR):
            os.makedirs(USERS_DIR, exist_ok=True)
            
        for file in os.listdir(USERS_DIR):
            if file.endswith(".json"):
                path = os.path.join(USERS_DIR, file)
                try:
                    mtime = os.path.getmtime(path)
                    # Only load if new or modified
                    if path not in config_mtimes or mtime > config_mtimes[path]:
                        with open(path, 'r', encoding="utf-8") as f:
                            config = json.load(f)
                            phone = config.get("phone")
                            if phone:
                                if phone not in started_phones:
                                    asyncio.create_task(run_user_bot(config))
                                else:
                                    # Update active bot in place
                                    if phone in active_bots:
                                        bot = active_bots[phone]
                                        bot["config"].update(config)
                                        # Sync state values
                                        state = bot["state"]
                                        state["delay"] = config.get("msg_delay_sec", 20)
                                        state["cycle"] = config.get("cycle_delay_min", 7)
                                        # Recalculate next_msg_at if waiting
                                        tz = AUTONIGHT_CFG.get("tz", DEFAULT_AUTONIGHT["tz"])
                                        sleep_seconds = _get_cycle_seconds_with_jitter(state["cycle"])
                                        state["next_msg_at"] = _get_now_tz(tz) + timedelta(seconds=sleep_seconds)
                                config_mtimes[path] = mtime
                except Exception as e:
                    logger.error(f"Error loading user config {file}: {e}")
        await asyncio.sleep(10) # Check every 10s for faster configuration updates

async def main():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(USERS_DIR, exist_ok=True)
    
    # Write PID file
    pid_file = "runner.pid"
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    print(f"{Fore.CYAN}{Style.BRIGHT}╔════════════════════════════════════════════╗")
    print(f"{Fore.CYAN}║    {Fore.YELLOW}KURUP ADS V5 ELITE - WORKER ENGINE      {Fore.CYAN}║")
    print(f"{Fore.CYAN}║    {Fore.GREEN}Status: Operational                     {Fore.CYAN}║")
    print(f"{Fore.CYAN}╚════════════════════════════════════════════╝{Style.RESET_ALL}")
    print(f"{Fore.WHITE}Logs will appear below in real-time...\n")

    # Ensure Auto-Night config file exists
    if not os.path.exists(AUTONIGHT_PATH):
        _save_autonight(AUTONIGHT_CFG)
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    def stop_all():
        logger.info("Shutdown signal received. Stopping...")
        for task in asyncio.all_tasks():
            task.cancel()

    if os.name != 'nt': # Signals not fully supported on Windows this way
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_all)
    
    try:
        await user_loader()
    except asyncio.CancelledError:
        pass
    finally:
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except Exception:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested. Exiting.")
        try:
            if os.path.exists("runner.pid"):
                os.remove("runner.pid")
        except Exception:
            pass
