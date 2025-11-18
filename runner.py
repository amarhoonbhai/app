#!/usr/bin/env python3
import os
import json
import asyncio
import logging
import re
from datetime import datetime, time, timedelta
from typing import Tuple

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import RPCError

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
USERS_DIR = os.path.join(BASE_DIR, "users")
CODES_PATH = os.path.join(BASE_DIR, "plan_codes.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_TZ = "Asia/Kolkata"
DEFAULT_AUTONIGHT = {
    "enabled": True,
    "start": "23:00",        # HH:MM 24h
    "end": "07:00",
    "tz": DEFAULT_TZ,
}
DEFAULT_DELAY_SEC = 5
DEFAULT_CYCLE_MIN = 15

started_users: set[str] = set()  # track started user files (paths)


# =========================
# Config & storage helpers
# =========================
def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit("[!] config.json not found. Run login.py first to set API_ID/API_HASH.")
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        raise SystemExit(f"[!] Failed to read config.json: {e}")
    if "API_ID" not in cfg or "API_HASH" not in cfg:
        raise SystemExit("[!] config.json missing API_ID or API_HASH. Run login.py again.")
    return cfg


CFG = load_config()
API_ID = int(CFG["API_ID"])
API_HASH = str(CFG["API_HASH"])

os.makedirs(USERS_DIR, exist_ok=True)


def load_user_from_file(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load user file {path}: {e}")
        return None


def save_user_to_file(user: dict) -> None:
    phone = user.get("phone")
    if not phone:
        return
    safe = phone.replace(" ", "")
    path = os.path.join(USERS_DIR, f"{safe}.json")
    user["updated_at"] = datetime.utcnow().isoformat()
    if "created_at" not in user:
        user["created_at"] = user["updated_at"]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(user, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save user {phone}: {e}")


def load_codes() -> list[dict]:
    if not os.path.exists(CODES_PATH):
        return []
    try:
        with open(CODES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        logger.error(f"Failed to load plan codes: {e}")
    return []


def save_codes(codes: list[dict]) -> None:
    try:
        with open(CODES_PATH, "w", encoding="utf-8") as f:
            json.dump(codes, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save plan codes: {e}")


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


def load_autonight_for_user(user_cfg: dict) -> dict:
    cfg = DEFAULT_AUTONIGHT.copy()
    db_cfg = user_cfg.get("auto_night") or {}
    for k in cfg:
        if k in db_cfg:
            cfg[k] = db_cfg[k]
    return cfg


def save_autonight_for_user(user_cfg: dict, new_cfg: dict) -> None:
    user_cfg["auto_night"] = new_cfg
    save_user_to_file(user_cfg)


# =========================
# Plan helpers
# =========================
def parse_expiry(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    return None


def is_plan_active(user_cfg: dict) -> bool:
    """
    True if plan is active (no expiry or expiry in the future).
    """
    exp = parse_expiry(user_cfg.get("plan_expiry"))
    if not exp:
        return True
    now = datetime.utcnow()
    return now < exp


def plan_text(user_cfg: dict) -> str:
    name = user_cfg.get("plan_name") or "free"
    exp = parse_expiry(user_cfg.get("plan_expiry"))
    if not exp:
        return f"{name} (∞)"
    return f"{name} (till {exp.strftime('%Y-%m-%d')})"


# =========================
# Core per-user bot
# =========================
async def run_user_bot(config_path: str, user_cfg: dict):
    # Avoid duplicate start
    if config_path in started_users:
        return

    phone = user_cfg.get("phone") or "unknown"
    name = user_cfg.get("name") or phone
    session_str = user_cfg.get("string_session")
    if not session_str:
        logger.error(f"[{phone}] No string_session in user config, skipping.")
        return

    # Per-user settings
    settings = user_cfg.get("settings") or {}
    delay_sec = int(settings.get("msg_delay_sec", DEFAULT_DELAY_SEC))
    cycle_min = int(settings.get("cycle_delay_min", DEFAULT_CYCLE_MIN))

    auto_cfg = load_autonight_for_user(user_cfg)
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

    started_users.add(config_path)
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

        # Reload latest user_cfg from disk for up-to-date data
        current = load_user_from_file(config_path) or user_cfg
        auto_cfg_local = load_autonight_for_user(current)
        user_state["auto_cfg"] = auto_cfg_local
        groups = current.get("groups") or []

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

            settings = current.get("settings") or {}
            settings["cycle_delay_min"] = user_state["cycle"]
            current["settings"] = settings
            save_user_to_file(current)

            await event.respond(f"✅ Cycle delay set to **{user_state['cycle']} minutes**")

        elif text.startswith(".delay"):
            value = int("".join(filter(str.isdigit, text)) or "0")
            if value <= 0:
                await event.respond("❗ Usage: `.delay 5` (seconds)")
                return
            user_state["delay"] = value

            settings = current.get("settings") or {}
            settings["msg_delay_sec"] = user_state["delay"]
            current["settings"] = settings
            save_user_to_file(current)

            await event.respond(f"✅ Message delay set to **{value} seconds**")

        # ---------- basic info ----------
        elif text.startswith(".status"):
            current = load_user_from_file(config_path) or current
            auto_cfg_local = load_autonight_for_user(current)
            user_state["auto_cfg"] = auto_cfg_local
            await event.respond(
                "📊 Status:\n"
                f"• Cycle Delay: **{user_state['cycle']} minutes**\n"
                f"• Message Delay: **{user_state['delay']} seconds**\n"
                f"• Plan: **{plan_text(current)}**\n\n"
                + autonight_status_text(auto_cfg_local)
            )

        elif text.startswith(".info"):
            current = load_user_from_file(config_path) or current
            auto_cfg_local = load_autonight_for_user(current)
            user_state["auto_cfg"] = auto_cfg_local
            expiry_str = "Developer" if me.id == 7876302875 else plan_text(current)
            reply = (
                f"❀ User Info:\n"
                f"❀ Name: {current.get('name')}\n"
                f"❀ Phone: {current.get('phone')}\n"
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

            current["groups"] = groups
            save_user_to_file(current)

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
                current["groups"] = groups
                save_user_to_file(current)
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
            save_autonight_for_user(current, new_cfg)
            user_state["auto_cfg"] = new_cfg
            await event.respond(msg)

        # ---------- Plan code redeem (from Telegram) ----------
        elif text.startswith(".redeem"):
            parts = text.split(maxsplit=1)
            if len(parts) != 2:
                await event.respond("❗ Usage: `.redeem ABCD1234`")
                return
            code_str = parts[1].strip().upper()
            if not code_str:
                await event.respond("❗ Usage: `.redeem ABCD1234`")
                return

            codes = load_codes()
            idx = None
            code_doc = None
            for i, c in enumerate(codes):
                if c.get("code") == code_str:
                    idx = i
                    code_doc = c
                    break

            if not code_doc:
                await event.respond("❌ Invalid code.")
                return
            if code_doc.get("used"):
                await event.respond("⚠️ This code has already been used.")
                return

            plan_expiry_raw = code_doc.get("plan_expiry")
            plan_name = code_doc.get("plan_name", "custom")

            exp_dt = parse_expiry(plan_expiry_raw)
            if not exp_dt:
                await event.respond("❌ Code misconfigured (no valid expiry). Contact support.")
                return

            # Update user
            current["plan_name"] = plan_name
            current["plan_expiry"] = exp_dt.isoformat()
            save_user_to_file(current)

            # Mark code used
            codes[idx]["used"] = True
            codes[idx]["used_by"] = current.get("phone")
            codes[idx]["used_at"] = datetime.utcnow().isoformat()
            save_codes(codes)

            await event.respond(
                f"✅ Code applied.\n"
                f"Plan: **{plan_name}**\n"
                f"Valid till: **{exp_dt.strftime('%Y-%m-%d')}**"
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
                # reload config from disk
                cfg = load_user_from_file(config_path)
                if not cfg:
                    logger.info(f"[{phone}] User file deleted. Stopping loop.")
                    return

                if not cfg.get("active", True):
                    logger.info(f"[{phone}] User inactive. Sleeping 5 minutes.")
                    await asyncio.sleep(300)
                    continue

                if not is_plan_active(cfg):
                    logger.info(f"[{phone}] Plan expired. Sleeping 5 minutes.")
                    await asyncio.sleep(300)
                    continue

                groups = cfg.get("groups") or []
                if not groups:
                    logger.info(f"[{phone}] No groups configured. Sleeping 5 minutes.")
                    await asyncio.sleep(300)
                    continue

                auto_cfg_local = load_autonight_for_user(cfg)
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
            for name in os.listdir(USERS_DIR):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(USERS_DIR, name)
                if path in started_users:
                    continue

                cfg = load_user_from_file(path)
                if not cfg:
                    continue

                phone = cfg.get("phone")
                if not is_plan_active(cfg):
                    logger.info(f"[⏳] Plan expired for {phone}. Not starting client.")
                    continue

                if cfg.get("active", True) is False:
                    logger.info(f"[{phone}] User marked inactive. Skipping start.")
                    continue

                asyncio.create_task(run_user_bot(path, cfg))
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
