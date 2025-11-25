import os
import json
import asyncio
import logging
import re
import sqlite3
from datetime import datetime, time, timedelta
from typing import Tuple

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, RPCError

# =========================================================
# AUTO-NIGHT CONFIG
# =========================================================
AUTONIGHT_PATH = os.path.join(os.path.dirname(__file__), "autonight.json")
DEFAULT_AUTONIGHT = {
    "enabled": True,
    "start": "23:00",
    "end": "07:00",
    "tz": "Asia/Kolkata"
}

def _load_autonight() -> dict:
    cfg = DEFAULT_AUTONIGHT.copy()
    try:
        if os.path.exists(AUTONIGHT_PATH):
            with open(AUTONIGHT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg.update({k: data.get(k, cfg[k]) for k in cfg})
    except:
        pass
    return cfg

def _save_autonight(cfg: dict):
    try:
        with open(AUTONIGHT_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except:
        pass

def _parse_hhmm(s: str) -> time:
    s = s.strip()
    if re.fullmatch(r"\d{1,2}", s):
        return time(int(s), 0)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        raise ValueError("Invalid time")
    return time(int(m.group(1)), int(m.group(2)))

def _get_now_tz(tz_name: str):
    if ZoneInfo:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except:
            pass
    return datetime.now()

def _in_window(now_t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now_t < end
    return now_t >= start or now_t < end

def _seconds_until_quiet_end(cfg: dict) -> int:
    tz = cfg.get("tz")
    now = _get_now_tz(tz)
    start_t = _parse_hhmm(cfg["start"])
    end_t = _parse_hhmm(cfg["end"])
    today = now.date()

    if start_t <= end_t:
        end_dt = datetime.combine(today, end_t, tzinfo=now.tzinfo)
        if now.time() >= end_t:
            end_dt += timedelta(days=1)
    else:
        if now.time() < end_t:
            end_dt = datetime.combine(today, end_t, tzinfo=now.tzinfo)
        else:
            end_dt = datetime.combine(today + timedelta(days=1), end_t, tzinfo=now.tzinfo)

    secs = int((end_dt - now).total_seconds())
    return max(secs, 1)

def autonight_is_quiet(cfg) -> bool:
    if not cfg.get("enabled"):
        return False
    now = _get_now_tz(cfg["tz"])
    start = _parse_hhmm(cfg["start"])
    end = _parse_hhmm(cfg["end"])
    return _in_window(now.time(), start, end)

def autonight_status_text(cfg):
    state = "ON ✅" if cfg["enabled"] else "OFF ❌"
    return (
        f"🌙 Auto-Night: **{state}**\n"
        f"Window: **{cfg['start']} → {cfg['end']}**\n"
        f"TZ: **{cfg['tz']}**"
    )

# =========================================================
# MAIN FORWARDER LOGIC
# =========================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("runner")

USERS_DIR = "users"
AUTONIGHT_CFG = _load_autonight()
started = set()   # tracks which accounts are already started


async def run_user_bot(config):
    phone = config["phone"]

    # avoid duplicate starts
    if phone in started:
        return

    api_id = int(config["api_id"])
    api_hash = config["api_hash"]
    session_str = config.get("session")  # IMPORTANT FIX

    if not session_str:
        logger.error(f"[{phone}] No string session found.")
        return

    groups = config.get("groups", [])
    delay = config.get("msg_delay_sec", 5)
    cycle = config.get("cycle_delay_min", 15)

    user_state = {"delay": delay, "cycle": cycle}

    # FIX: Use StringSession instead of SQLite `.session`
    client = TelegramClient(
        StringSession(session_str),
        api_id,
        api_hash,
        connection_retries=None,
        request_retries=5,
        retry_delay=1,
        timeout=10
    )

    try:
        await client.start()
    except SessionPasswordNeededError:
        logger.error(f"[{phone}] 2FA required; cannot start.")
        return
    except RPCError as e:
        logger.error(f"[{phone}] RPC Error: {e}")
        return
    except Exception as e:
        logger.error(f"[{phone}] Failed to start client: {e}")
        return

    started.add(phone)
    logger.info(f"[✔] Logged in successfully: {phone}")

    # =====================================================
    # COMMAND HANDLER (self-only)
    # =====================================================
    @client.on(events.NewMessage)
    async def commands(event):
        me = await client.get_me()
        if event.sender_id != me.id:
            return

        txt = (event.raw_text or "").strip().lower()

        if txt.startswith(".time"):
            v = int(''.join(filter(str.isdigit, txt)) or "0")
            if v <= 0:
                await event.respond("❗ Use `.time 10m` or `.time 1h`")
                return
            if 'h' in txt:
                user_state["cycle"] = v * 60
            else:
                user_state["cycle"] = v
            await event.respond(f"⏳ Cycle set: **{user_state['cycle']} min**")

        elif txt.startswith(".delay"):
            v = int(''.join(filter(str.isdigit, txt)) or "0")
            if v <= 0:
                await event.respond("❗ Use `.delay 5`")
                return
            user_state["delay"] = v
            await event.respond(f"⏱ Delay set: **{v} sec**")

        elif txt.startswith(".status"):
            await event.respond(
                f"📊 **Status**\n"
                f"• Cycle: {user_state['cycle']} min\n"
                f"• Delay: {user_state['delay']} sec\n\n"
                f"{autonight_status_text(AUTONIGHT_CFG)}"
            )

        elif txt.startswith(".groups"):
            if groups:
                await event.respond("📋 Groups:\n" + "\n".join(groups))
            else:
                await event.respond("❗ No groups added.")

        elif txt.startswith(".addgroup"):
            urls = re.findall(r"https://t\.me/\S+", txt)
            new = 0
            for u in urls:
                if u not in groups:
                    groups.append(u)
                    new += 1
            config["groups"] = groups
            with open(f"{USERS_DIR}/{phone}.json", "w") as f:
                json.dump(config, f, indent=2)
            await event.respond(f"✨ Added {new} group(s).")

        elif txt.startswith(".night"):
            arg = txt[6:].strip()
            # simple on/off
            if arg in ["on", "enable"]:
                AUTONIGHT_CFG["enabled"] = True
                _save_autonight(AUTONIGHT_CFG)
                await event.respond("🌙 Auto-night **enabled**")
                return
            if arg in ["off", "disable"]:
                AUTONIGHT_CFG["enabled"] = False
                _save_autonight(AUTONIGHT_CFG)
                await event.respond("🌙 Auto-night **disabled**")
                return

            m = re.fullmatch(r"(\d{1,2}(:\d{2})?)\s*[-to]+\s*(\d{1,2}(:\d{2})?)", arg)
            if m:
                AUTONIGHT_CFG["start"] = m.group(1)
                AUTONIGHT_CFG["end"] = m.group(3)
                _save_autonight(AUTONIGHT_CFG)
                await event.respond("⏳ Auto-night window updated.\n" + autonight_status_text(AUTONIGHT_CFG))
                return

            await event.respond(autonight_status_text(AUTONIGHT_CFG))

    # =====================================================
    # FORWARD LOOP
    # =====================================================
    async def forward_loop():
        while True:
            try:
                if autonight_is_quiet(AUTONIGHT_CFG):
                    secs = _seconds_until_quiet_end(AUTONIGHT_CFG)
                    logger.info(f"[{phone}] Auto-night active, sleeping {secs//60}m")
                    await asyncio.sleep(secs)
                    continue

                msgs = await client.get_messages("me", limit=100)
                msgs = list(reversed(msgs))

                for msg in msgs:
                    if autonight_is_quiet(AUTONIGHT_CFG):
                        break
                    if not msg.message and not msg.media:
                        continue

                    for g in groups:
                        try:
                            await client.forward_messages(g, msg)
                            logger.info(f"[{phone}] Forwarded → {g}")
                        except Exception as e:
                            logger.warning(f"[{phone}] Error forwarding: {e}")

                    await asyncio.sleep(user_state["delay"])

                await asyncio.sleep(user_state["cycle"] * 60)

            except Exception as e:
                logger.error(f"[{phone}] Loop error: {e}")
                await asyncio.sleep(10)

    asyncio.create_task(forward_loop())
    await client.run_until_disconnected()


# =========================================================
# USER LOADER
# =========================================================
async def user_loader():
    while True:
        for file in os.listdir(USERS_DIR):
            if file.endswith(".json"):
                try:
                    cfg = json.load(open(f"{USERS_DIR}/{file}", "r"))
                    expiry = cfg.get("plan_expiry")
                    if expiry and datetime.now() > datetime.fromisoformat(expiry):
                        logger.info(f"[⏳] Plan expired for {cfg['phone']}")
                        continue
                    asyncio.create_task(run_user_bot(cfg))
                except Exception as e:
                    logger.error(f"Error reading user file {file}: {e}")
        await asyncio.sleep(60)


# =========================================================
# MAIN
# =========================================================
async def main():
    os.makedirs(USERS_DIR, exist_ok=True)
    if not os.path.exists(AUTONIGHT_PATH):
        _save_autonight(AUTONIGHT_CFG)
    await user_loader()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("💤 Exiting runner.")
        
