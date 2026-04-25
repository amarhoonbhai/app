import os
import json
import asyncio
import logging
import sqlite3
import re
from datetime import datetime, date, time, timedelta
from typing import Tuple, List, Optional
import random


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
    try:
        with open(AUTONIGHT_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

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
    groups = config.get("groups", [])
    delay = config.get("msg_delay_sec", 30)
    cycle = config.get("cycle_delay_min", 15)


    user_state = {
        "delay": delay,   # seconds between forwards
        "cycle": cycle,   # minutes between cycles
        "use_copy": True, # Copy instead of Forward (removes 'forwarded from' tag)
    }

    client = TelegramClient(session_path, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error(f"[{phone}] Session revoked or unauthorized.")
            started_phones.remove(phone)
            return
    except Exception as e:
        logger.error(f"[{phone}] Connection failure: {e}")
        started_phones.remove(phone)
        return

    logger.info(f"[✔] Started bot for {config.get('name','N/A')} ({phone})")


    @client.on(events.NewMessage)
    async def command_handler(event):
        """Accept commands only from the account owner (self)."""
        me = await client.get_me()
        if event.sender_id != me.id:
            return

        text = (event.raw_text or "").strip()

        if text.startswith(".time"):
            value = int(''.join(filter(str.isdigit, text)) or "0")
            if value <= 0:
                await event.respond("❗ Usage: `.time 10m` or `.time 1h`")
                return
            if 'h' in text.lower():
                user_state["cycle"] = value * 60
            else:
                user_state["cycle"] = value
            await event.respond(f"✅ Cycle delay set to **{user_state['cycle']} minutes**")

        elif text.startswith(".delay"):
            value = int(''.join(filter(str.isdigit, text)) or "0")
            if value <= 0:
                await event.respond("❗ Usage: `.delay 5` (seconds)")
                return
            user_state["delay"] = value
            await event.respond(f"✅ Message delay set to **{value} seconds** (Randomized ±20%)")


        elif text.startswith(".status"):
            await event.respond(
                "📊 Status:\n"
                f"• Cycle Delay: **{user_state['cycle']} minutes**\n"
                f"• Message Delay: **{user_state['delay']} seconds**\n\n"
                + autonight_status_text(AUTONIGHT_CFG)
            )

        elif text.startswith(".info"):
            me = await client.get_me()
            expiry = "Lifetime"
            reply = (
                f"❀ User Info:\n"
                f"❀ Name: {config.get('name')}\n"
                f"❀ Cycle Delay: {user_state['cycle']} min\n"
                f"❀ Message Delay: {user_state['delay']} sec\n"
                f"❀ Groups: {len(groups)}\n"
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
            for link in links:
                if link not in groups:
                    groups.append(link)
                    added.append(link)
                else:
                    skipped.append(link)
            config["groups"] = groups
            with open(os.path.join(USERS_DIR, f"{phone}.json"), "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            msg = []
            if added:
                msg.append(f"✅ Added **{len(added)}** new group(s).")
            if skipped:
                msg.append(f"⚠️ Skipped **{len(skipped)}** duplicate(s).")
            await event.respond("\n".join(msg) or "No changes.")

        elif text.startswith(".delgroup"):
            parts = text.split()
            if len(parts) == 2 and parts[1] in groups:
                groups.remove(parts[1])
                config["groups"] = groups
                with open(os.path.join(USERS_DIR, f"{phone}.json"), "w", encoding="utf-8") as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                await event.respond("❀ Group removed.")
            else:
                await event.respond("❗ Usage: `.delgroup <https://t.me/...>` (must match an existing group)")

        elif text.startswith(".groups"):
            if groups:
                await event.respond("❀ Groups:\n" + "\n".join([g for g in groups if "t.me" in g]))
            else:
                await event.respond("📋 No groups configured.")

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
                    # Handle both private and public links
                    if "t.me/+" in link or "t.me/joinchat/" in link:
                        from telethon.tl.functions.messages import ImportChatInviteRequest
                        hash = link.split('/')[-1]
                        await client(ImportChatInviteRequest(hash))
                    else:
                        from telethon.tl.functions.channels import JoinChannelRequest
                        username = link.split('/')[-1]
                        await client(JoinChannelRequest(username))
                    success += 1
                    await asyncio.sleep(random.randint(10, 20)) # Wait between joins
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
                "\n🌙 **System:**\n"
                "• `.status` | `.info` | `.night`"
            )



    async def forward_loop():
        while True:
            try:
                # 🌙 If within quiet hours, check every minute if still quiet
                while autonight_is_quiet(AUTONIGHT_CFG):
                    secs_to_end = _seconds_until_quiet_end(AUTONIGHT_CFG)
                    # Sleep max 60s at a time to allow immediate wake-up if config changes
                    sleep_step = min(secs_to_end, 60)
                    if sleep_step > 0:
                        await asyncio.sleep(sleep_step)
                    else:
                        break # safety break
                
                # 💎 Only get the LATEST message from Saved Messages 
                messages = await client.get_messages("me", limit=1)
                
                if not messages:
                    logger.info(f"[{phone}] No messages in Saved Messages. Waiting for next cycle.")
                    await asyncio.sleep(user_state["cycle"] * 60)
                    continue

                msg = messages[0]
                interrupted_by_night = False

                for group in groups:
                    # If night starts mid-cycle, break early
                    if autonight_is_quiet(AUTONIGHT_CFG):
                        interrupted_by_night = True
                        break

                    try:
                        if user_state["use_copy"]:
                            # 🌈 Copy Mode: Sends as a fresh message (No 'Forwarded' Tag)
                            caption = msg.text or ""
                            if msg.media:
                                await client.send_file(group, msg.media, caption=caption)
                            else:
                                await client.send_message(group, caption)
                        else:
                            # 🔄 Forward Mode
                            await client.forward_messages(group, msg)

                        
                        logger.info(f"[{phone}] Success -> {group}")
                        
                        # ⚡ 30-Second Gap (Randomized ±10% for high stealth)
                        wait_time = user_state["delay"] * random.uniform(0.9, 1.1)
                        await asyncio.sleep(wait_time)


                    except FloodWaitError as e:
                        logger.warning(f"[{phone}] FloodWait! Sleeping {e.seconds}s")
                        await asyncio.sleep(e.seconds + 5)
                    except SlowModeWaitError as e:
                        logger.warning(f"[{phone}] Slowmode in {group}. Waiting {e.seconds}s (skipped)")
                    except ChatWriteForbiddenError:
                        logger.error(f"[{phone}] Banned or No permission in {group}")
                    except Exception as e:
                        logger.error(f"[{phone}] Failed {group}: {type(e).__name__}")

                if interrupted_by_night:
                    continue

                logger.info(f"[{phone}] Cycle complete ({len(groups)} groups). Next in {user_state['cycle']}m.")
                await asyncio.sleep(user_state["cycle"] * 60)

            except Exception as e:
                logger.exception(f"[{phone}] Error in forward loop: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(forward_loop())
    await client.run_until_disconnected()

async def user_loader():
    while True:
        for file in os.listdir(USERS_DIR):
            if file.endswith(".json"):
                path = os.path.join(USERS_DIR, file)
                try:
                    with open(path, 'r', encoding="utf-8") as f:
                        config = json.load(f)
                        # Plan check completely removed for Lifetime access
                        asyncio.create_task(run_user_bot(config))

                except Exception as e:
                    logger.error(f"Error loading user config {file}: {e}")
        await asyncio.sleep(60)

async def main():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(USERS_DIR, exist_ok=True)
    # Ensure Auto-Night config file exists
    if not os.path.exists(AUTONIGHT_PATH):
        _save_autonight(AUTONIGHT_CFG)
    await user_loader()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested. Exiting.")
    
