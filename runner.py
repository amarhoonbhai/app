#!/usr/bin/env python3
import os
import asyncio
import logging
import re
from datetime import datetime, time, timedelta
from typing import Tuple

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

from dotenv import load_dotenv
from pymongo import MongoClient
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import RPCError

# =========================
# Env / DB bootstrap
# =========================
load_dotenv()

API_ID = int(os.getenv("TG_API_ID") or os.getenv("API_ID") or 0)
API_HASH = os.getenv("TG_API_HASH") or os.getenv("API_HASH") or ""
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("SPINIFY_DB_NAME", "spinify")

if not API_ID or not API_HASH:
    raise SystemExit("[!] Set TG_API_ID/API_ID and TG_API_HASH/API_HASH in .env")

mongo = MongoClient(MONGO_URI)
db = mongo[DB_NAME]
users_col = db.users
codes_col = db.plan_codes

# =========================
# General config
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_AUTONIGHT = {
    "enabled": True,
    "start": "23:00",        # HH:MM 24h
    "end": "07:00",
    "tz": "Asia/Kolkata",
}
DEFAULT_DELAY_SEC = 5
DEFAULT_CYCLE_MIN = 15

started_users = set()  # track started Mongo _id (string)


# =========================
# Auto-Night helpers
# =========================
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
    return datetime.now()


def _in_window(now_t: time, start_t: time, end_t: time) -> bool:
    """True if now is within [start, end) with midnight wrap support."""
    if start_t <= end_t:
        return start_t <= now_t < end_t
    # crosses midnight, e.g., 23:00 -> 07:00
    return (now_t >= start_t) or (now_t < end_t)


def autonight_is_quiet(cfg: dict) -> bool:
    if not cfg.get("enabled", True):
        return False
    try:
        now = _get_now_tz(cfg.get("tz", DEFAULT_AUTONIGHT["tz"]))
        start_t = _parse_hhmm(cfg.get("start", DEFAULT_AUTONIGHT["start"]))
        end_t = _parse_hhmm(cfg.get("end", DEFAULT_AUTONIGHT["end"]))
        return _in_window(now.time(), start_t, end_t)
    except Exception:
        # Fail open if config broken
        return False


def seconds_until_quiet_end(cfg: dict) -> int:
    """Return seconds until the end of quiet window (>= 1), assuming we are currently in quiet."""
    tz = cfg.get("tz") or DEFAULT_AUTONIGHT["tz"]
    now = _get_now_tz(tz)
    start_t = _parse_hhmm(cfg.get("start", DEFAULT_AUTONIGHT["start"]))
    end_t = _parse_hhmm(cfg.get("end", DEFAULT_AUTONIGHT["end"]))
    today = now.date()

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


def autonight_status_text(cfg: dict) -> str:
    state = "ON ✅" if cfg.get("enabled", True) else "OFF ❌"
    return (
        f"🌙 Auto-Night: **{state}**\n"
        f"Window: **{cfg.get('start','23:00')} → {cfg.get('end','07:00')}**\n"
        f"TZ: **{cfg.get('tz','Asia/Kolkata')}**"
    )


def autonight_parse_command(arg: str, cfg: dict) -> Tuple[str, dict]:
    """
    Returns (message_text, updated_cfg).
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
        new_cfg = cfg.copy()
        new_cfg["enabled"] = True
        return ("✅ Auto-Night **enabled**.\n" + autonight_status_text(new_cfg), new_cfg)

    if low in {"off", "disable", "disabled"}:
        new_cfg = cfg.copy()
        new_cfg["enabled"] = False
        return ("🚫 Auto-Night **disabled**.\n" + autonight_status_text(new_cfg), new_cfg)

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
        end_t = _parse_hhmm(end_raw)
    except ValueError as e:
        return (f"❗ {e}", cfg)

    new_cfg = cfg.copy()
    new_cfg["start"] = f"{start_t.hour:02d}:{start_t.minute:02d}"
    new_cfg["end"] = f"{end_t.hour:02d}:{end_t.minute:02d}"
    return (
        f"🕒 Auto-Night window updated:\n**{new_cfg['start']} → {new_cfg['end']}** ({new_cfg.get('tz','Asia/Kolkata')})\n"
        + autonight_status_text(new_cfg),
        new_cfg
    )


def load_autonight_for_user(user_doc: dict) -> dict:
    cfg = DEFAULT_AUTONIGHT.copy()
    db_cfg = user_doc.get("auto_night") or {}
    for k in cfg:
        if k in db_cfg:
            cfg[k] = db_cfg[k]
    return cfg


def save_autonight_for_user(user_id, cfg: dict) -> None:
    users_col.update_one(
        {"_id": user_id},
        {"$set": {"auto_night": cfg, "updated_at": datetime.utcnow()}}
    )


# =========================
# Plan helpers
# =========================
def is_plan_active(user_doc: dict) -> bool:
    """
    True if plan is active (no expiry or expiry in the future).
    """
    exp = user_doc.get("plan_expiry")
    if not exp:
        return True

    if isinstance(exp, str):
        try:
            exp = datetime.fromisoformat(exp)
        except ValueError:
            # broken expiry: treat as expired (safer)
            return False

    now = datetime.utcnow()
    if exp.tzinfo is not None:
        now = now.astimezone(exp.tzinfo)
    return now < exp


def plan_text(user_doc: dict) -> str:
    exp = user_doc.get("plan_expiry")
    name = user_doc.get("plan_name") or "free"
    if not exp:
        return f"{name} (∞)"
    if isinstance(exp, str):
        try:
            exp_dt = datetime.fromisoformat(exp)
        except ValueError:
            return f"{name} (invalid expiry)"
    else:
        exp_dt = exp
    return f"{name} (till {exp_dt.strftime('%Y-%m-%d')})"


# =========================
# Core per-user bot
# =========================
async def run_user_bot(user_doc: dict):
    user_id = user_doc["_id"]
    uid_str = str(user_id)
    phone = user_doc.get("phone") or "unknown"
    name = user_doc.get("name") or phone

    if uid_str in started_users:
        return

    session_str = user_doc.get("string_session")
    if not session_str:
        logger.error(f"[{phone}] No string_session in DB, skipping.")
        return

    # Per-user settings
    settings = user_doc.get("settings") or {}
    delay_sec = int(settings.get("msg_delay_sec", DEFAULT_DELAY_SEC))
    cycle_min = int(settings.get("cycle_delay_min", DEFAULT_CYCLE_MIN))

    auto_cfg = load_autonight_for_user(user_doc)

    user_state = {
        "delay": delay_sec,   # seconds between forwards
        "cycle": cycle_min,   # minutes between cycles
        "auto_cfg": auto_cfg,
    }

    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error(f"[{phone}] Session not authorized. Re-login required.")
            return
    except RPCError as e:
        logger.error(f"[{phone}] RPC Error: {e}")
        return
    except Exception as e:
        logger.exception(f"[{phone}] Failed to start client: {e}")
        return

    started_users.add(uid_str)
    me = await client.get_me()
    owner_id = me.id

    logger.info(f"[✔] Started bot for {name} ({phone}) | user_id={owner_id}")

    # ---------- commands ----------
    @client.on(events.NewMessage)
    async def command_handler(event):
        # Only accept commands from this account owner (self)
        if event.sender_id != owner_id:
            return

        text = (event.raw_text or "").strip()
        if not text.startswith("."):
            return

        # Reload latest user_doc from DB for up-to-date plan/groups/autonight/etc.
        current_doc = users_col.find_one({"_id": user_id}) or user_doc
        auto_cfg_local = load_autonight_for_user(current_doc)
        user_state["auto_cfg"] = auto_cfg_local

        groups = current_doc.get("groups") or []

        # ---------- timing ----------
        if text.startswith(".time"):
            value = int("".join(filter(str.isdigit, text)) or "0")
            if value <= 0:
                await event.respond("❗ Usage: `.time 10m` or `.time 1h`")
                return
            if "h" in text.lower():
                user_state["cycle"] = value * 60  # hours → minutes
            else:
                user_state["cycle"] = value       # minutes
            users_col.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "settings.cycle_delay_min": user_state["cycle"],
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            await event.respond(f"✅ Cycle delay set to **{user_state['cycle']} minutes**")

        elif text.startswith(".delay"):
            value = int("".join(filter(str.isdigit, text)) or "0")
            if value <= 0:
                await event.respond("❗ Usage: `.delay 5` (seconds)")
                return
            user_state["delay"] = value
            users_col.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "settings.msg_delay_sec": user_state["delay"],
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            await event.respond(f"✅ Message delay set to **{value} seconds**")

        # ---------- basic info ----------
        elif text.startswith(".status"):
            current_doc = users_col.find_one({"_id": user_id}) or current_doc
            auto_cfg_local = load_autonight_for_user(current_doc)
            user_state["auto_cfg"] = auto_cfg_local
            await event.respond(
                "📊 Status:\n"
                f"• Cycle Delay: **{user_state['cycle']} minutes**\n"
                f"• Message Delay: **{user_state['delay']} seconds**\n"
                f"• Plan: **{plan_text(current_doc)}**\n\n"
                + autonight_status_text(auto_cfg_local)
            )

        elif text.startswith(".info"):
            current_doc = users_col.find_one({"_id": user_id}) or current_doc
            auto_cfg_local = load_autonight_for_user(current_doc)
            user_state["auto_cfg"] = auto_cfg_local
            expiry_str = "Developer" if me.id == 7876302875 else plan_text(current_doc)
            reply = (
                f"❀ User Info:\n"
                f"❀ Name: {current_doc.get('name')}\n"
                f"❀ Phone: {current_doc.get('phone')}\n"
                f"❀ Cycle Delay: {user_state['cycle']} min\n"
                f"❀ Message Delay: {user_state['delay']} sec\n"
                f"❀ Groups: {len(groups)}\n"
                f"❀ Plan: {expiry_str}\n\n"
                + autonight_status_text(auto_cfg_local)
            )
            await event.respond(reply)

        # ---------- groups ----------
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

            users_col.update_one(
                {"_id": user_id},
                {
                    "$set": {"groups": groups, "updated_at": datetime.utcnow()},
                },
            )

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
                users_col.update_one(
                    {"_id": user_id},
                    {
                        "$set": {"groups": groups, "updated_at": datetime.utcnow()},
                    },
                )
                await event.respond("❀ Group removed.")
            else:
                await event.respond("❗ Usage: `.delgroup <https://t.me/...>` (must match an existing group)")

        elif text.startswith(".groups"):
            if groups:
                await event.respond("❀ Groups:\n" + "\n".join([g for g in groups if "t.me" in g]))
            else:
                await event.respond("📋 No groups configured.")

        # ---------- Auto-Night per-user ----------
        elif text.startswith(".night"):
            arg = text[6:].strip() if len(text) > 6 else ""
            msg, new_cfg = autonight_parse_command(arg, auto_cfg_local)
            save_autonight_for_user(user_id, new_cfg)
            user_state["auto_cfg"] = new_cfg
            await event.respond(msg)

        # ---------- Plan code redeem ----------
        elif text.startswith(".redeem"):
            parts = text.split(maxsplit=1)
            if len(parts) != 2:
                await event.respond("❗ Usage: `.redeem ABCD1234`")
                return
            code_str = parts[1].strip().upper()
            if not code_str:
                await event.respond("❗ Usage: `.redeem ABCD1234`")
                return

            code_doc = codes_col.find_one({"code": code_str})
            if not code_doc:
                await event.respond("❌ Invalid code.")
                return
            if code_doc.get("used"):
                await event.respond("⚠️ This code has already been used.")
                return

            plan_expiry = code_doc.get("plan_expiry")
            plan_name = code_doc.get("plan_name", "custom")

            if not isinstance(plan_expiry, datetime):
                await event.respond("❌ Code misconfigured (no expiry in DB). Contact support.")
                return

            # Update user
            users_col.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "plan_name": plan_name,
                        "plan_expiry": plan_expiry,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )

            # Mark code used
            codes_col.update_one(
                {"_id": code_doc["_id"]},
                {
                    "$set": {
                        "used": True,
                        "used_by": user_id,
                        "used_at": datetime.utcnow(),
                    }
                },
            )

            await event.respond(
                f"✅ Code applied.\n"
                f"Plan: **{plan_name}**\n"
                f"Valid till: **{plan_expiry.strftime('%Y-%m-%d')}**"
            )

        # ---------- help ----------
        elif text.startswith(".help"):
            await event.respond(
                "🛠 Available Commands:\n"
                "• `.time <10m|1h>` — Set cycle delay (minutes)\n"
                "• `.delay <sec>` — Set delay between messages\n"
                "• `.status` — Show timing + plan + Auto-Night\n"
                "• `.info` — Show full user info\n"
                "• `.addgroup <url ...>` — Add group(s)\n"
                "• `.delgroup <url>` — Remove group\n"
                "• `.groups` — List groups\n"
                "• `.night` — Show Auto-Night status\n"
                "• `.night on|off` — Enable/disable Auto-Night\n"
                "• `.night 23:00 to 07:00` — Change quiet window (24h)\n"
                "• `.redeem <CODE>` — Redeem plan upgrade code"
            )

    # ---------- forward loop ----------
    async def forward_loop():
        while True:
            try:
                doc = users_col.find_one({"_id": user_id})
                if not doc:
                    logger.info(f"[{phone}] User removed from DB. Stopping loop.")
                    return

                if not doc.get("active", True):
                    logger.info(f"[{phone}] User inactive. Sleeping 5 minutes.")
                    await asyncio.sleep(300)
                    continue

                if not is_plan_active(doc):
                    logger.info(f"[{phone}] Plan expired. Sleeping 5 minutes.")
                    await asyncio.sleep(300)
                    continue

                groups = doc.get("groups") or []
                if not groups:
                    logger.info(f"[{phone}] No groups configured. Sleeping 5 minutes.")
                    await asyncio.sleep(300)
                    continue

                auto_cfg_local = load_autonight_for_user(doc)
                user_state["auto_cfg"] = auto_cfg_local

                # 🌙 Auto-Night window
                if autonight_is_quiet(auto_cfg_local):
                    secs = seconds_until_quiet_end(auto_cfg_local)
                    mins = max(1, secs // 60)
                    logger.info(f"[{phone}] 🌙 Auto-Night active. Sleeping ~{mins} min.")
                    await asyncio.sleep(secs)
                    continue

                # Fetch messages from Saved Messages
                messages = await client.get_messages("me", limit=100)
                messages = list(reversed(messages))

                interrupted_by_night = False

                for msg in messages:
                    if msg.message is None and not msg.media:
                        continue

                    # If night starts mid-cycle, break early
                    if autonight_is_quiet(user_state["auto_cfg"]):
                        interrupted_by_night = True
                        logger.info(f"[{phone}] Entered Auto-Night mid-cycle. Pausing forwards.")
                        break

                    for group in groups:
                        try:
                            await client.forward_messages(group, msg)
                            logger.info(f"[{phone}] Forwarded to {group}")
                        except Exception as e:
                            logger.warning(f"[{phone}] Error forwarding to {group}: {e}")

                    await asyncio.sleep(user_state["delay"])

                if interrupted_by_night:
                    secs = seconds_until_quiet_end(user_state["auto_cfg"])
                    mins = max(1, secs // 60)
                    logger.info(f"[{phone}] 🌙 Auto-Night active. Sleeping ~{mins} min.")
                    await asyncio.sleep(secs)
                    continue

                logger.info(f"[{phone}] Cycle complete. Sleeping for {user_state['cycle']} minutes...")
                await asyncio.sleep(user_state["cycle"] * 60)

            except Exception as e:
                logger.exception(f"[{phone}] Error in forward loop: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(forward_loop())
    await client.run_until_disconnected()


# =========================
# User loader (multi-user)
# =========================
async def user_loader():
    while True:
        try:
            for user_doc in users_col.find({"active": {"$ne": False}}):
                user_id = user_doc["_id"]
                uid_str = str(user_id)
                phone = user_doc.get("phone")

                if uid_str in started_users:
                    continue

                if not is_plan_active(user_doc):
                    logger.info(f"[⏳] Plan expired for {phone}. Not starting client.")
                    continue

                asyncio.create_task(run_user_bot(user_doc))
        except Exception as e:
            logger.exception(f"Error in user_loader: {e}")
        await asyncio.sleep(60)


async def main():
    await user_loader()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested. Exiting.")
