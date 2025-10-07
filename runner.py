
#!/usr/bin/env python3
"""Telegram Auto Forwarder: Forwards Saved Messages to configured groups with delay, night mode, and rest mode."""


import re

def normalize_phone(s: str) -> str:
    return re.sub(r"\D", "", s)
import sys
import json
import asyncio
import re
import logging
import signal
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from telethon import TelegramClient, events, functions, types, errors
from logging.handlers import RotatingFileHandler

# === Constants & Paths ===
MAX_GROUPS = 50
USERS_DIR = Path("users")
SESSIONS_DIR = Path("sessions")
LOGS_DIR = Path("logs")
for d in (USERS_DIR, SESSIONS_DIR, LOGS_DIR): d.mkdir(exist_ok=True)

# === Logger Setup ===
log_file = LOGS_DIR / "runner.log"
logger = logging.getLogger("runner")
handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3)
logging.basicConfig(handlers=[handler], level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# === Regex ===
RE_FOLDER = re.compile(r'(?:https?://)?t\.me/(?:addlist/)([\w-]+)', re.I)
RE_INVITE = re.compile(r'https?://t\.me/(?:\+|joinchat/)([\w-]+)', re.I)
RE_USERLN = re.compile(r'https?://t\.me/([A-Za-z0-9_]{5,})', re.I)

# === Runtime State ===
runtime = {
    "forward_delay": 5,
    "groups": [],
    "night_mode": False,
    "rest_until": None
}

# === Helper Functions ===
def user_file(phone): return USERS_DIR / f"{phone}.json"

def load_user(phone):
    p = user_file(phone)
    if p.exists():
        with open(p) as fp:
            return json.load(fp)
    p_alt = USERS_DIR / f"+{phone}.json"
    if p_alt.exists():
        with open(p_alt) as fp:
            return json.load(fp)
    raise FileNotFoundError(f"User config not found for phone '{phone}' (tried {p} and {p_alt})")

def save_user(phone, data):
    with open(user_file(phone), "w") as fp:
        json.dump(data, fp, indent=2)

def save_runtime(phone, cfg):
    cfg.update({
        "groups": runtime["groups"],
        "forward_delay": runtime["forward_delay"],
        "night_mode": runtime["night_mode"]
    })
    save_user(phone, cfg)

async def _fetch_folder(url):
    async with httpx.AsyncClient(timeout=20) as hc:
        r = await hc.get(url if url.startswith("http") else f"https://{url}")
        r.raise_for_status()
        text = r.text
    invites = set(RE_INVITE.findall(text))
    users = set(u for u in RE_USERLN.findall(text) if not u.startswith(("+", "joinchat")))
    return invites, users

# === Main Async Function and Event Handlers to follow ===

async def _join_group(client, phone, kind, val, cfg):
    if len(runtime["groups"]) >= MAX_GROUPS:
        logger.warning("Group cap reached – cannot add more")
        return False

    try:
        ent = None
        if kind == "invite":
            try:
                result = await client(functions.messages.ImportChatInviteRequest(val))
                ent = result.chats[0] if result.chats else None
            except errors.UserAlreadyParticipantError:
                ent = await client.get_entity(f"joinchat/{val}")
        elif kind == "username":
            ent = await client.get_entity(val)
            if isinstance(ent, types.Channel):
                try:
                    await client(functions.channels.JoinChannelRequest(ent))
                except errors.UserAlreadyParticipantError:
                    pass
        elif kind == "entity_id":
            ent = await client.get_entity(int(val))

        added_id = getattr(ent, "id", None)
        if added_id and added_id not in runtime["groups"]:
            runtime["groups"].append(added_id)
            save_runtime(phone, cfg)
            logger.info(f"Joined and saved {added_id}")
            return True
    except Exception as e:
        logger.error(f"Failed to join group ({val}): {e}")
    return False




# === Auto Night Mode Defaults ===
    AUTO_NIGHT_DEFAULTS = {
        "auto_night": True,
        "quiet_start": "23:00",
        "quiet_end": "06:00",
        "timezone": "Asia/Kolkata",
    }

    def _ensure_defaults(cfg: dict) -> dict:
    	for k, v in AUTO_NIGHT_DEFAULTS.items():
    		cfg.setdefault(k, v)
    	return cfg
async def main(phone: str):
    cfg = _ensure_defaults(load_user(phone))
    client = TelegramClient(str(SESSIONS_DIR / f"{phone}.session"), cfg["api_id"], cfg["api_hash"], use_ipv6=False)
    await client.start()
    me = await client.get_me()

    runtime.update({
        "groups": cfg.get("groups", []),
        "forward_delay": max(cfg.get("forward_delay", 5), 5),
        "night_mode": cfg.get("night_mode", False),
        "rest_until": None
    })

    def is_me(ev):
        # treat your own outgoing messages as 'me'
        return bool(getattr(ev, 'out', False)) or ev.sender_id == me.id


    

def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    h, m = hhmm.split(":")
    return int(h), int(m)

def _in_quiet_window(now_local, start_hm: str, end_hm: str) -> bool:
    sh, sm = _parse_hhmm(start_hm)
    eh, em = _parse_hhmm(end_hm)
    start = now_local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end   = now_local.replace(hour=eh, minute=em, second=0, microsecond=0)
    if start <= end:
        return start <= now_local < end
    else:
        return now_local >= start or now_local < end

def is_quiet_now(cfg: dict) -> bool:
    if not cfg.get("auto_night", True):
        return False
    try:
        tz = ZoneInfo(cfg.get("timezone", "Asia/Kolkata"))
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")
    now_local = datetime.datetime.now(tz)
    return _in_quiet_window(now_local, cfg["quiet_start"], cfg["quiet_end"])

# === Commands ===
    @client.on(events.NewMessage(pattern=r"^\.addgroup\s+(.+)$", outgoing=True))
    async def cmd_addgroup(ev):
        if not is_me(ev): return
        if len(runtime["groups"]) >= MAX_GROUPS:
            await ev.reply(f"⚠️ Group limit reached ({MAX_GROUPS}), cannot add more.")
            return
        arg = ev.pattern_match.group(1).strip()
        joined = 0

        if RE_FOLDER.search(arg):
            invites, users = await _fetch_folder(arg)
            for h in invites:
                if await _join_group(client, phone, "invite", h, cfg): joined += 1
            for u in users:
                if await _join_group(client, phone, "username", u, cfg): joined += 1
            await ev.reply(f"📦 Folder added {joined} new groups.")
            return

        m_inv, m_usr = RE_INVITE.search(arg), RE_USERLN.search(arg)
        if m_inv and await _join_group(client, phone, "invite", m_inv.group(1), cfg):
            await ev.reply("✅ Added group")
        elif m_usr and await _join_group(client, phone, "username", m_usr.group(1), cfg):
            await ev.reply("✅ Added group")
        elif arg.isdigit() and await _join_group(client, phone, "entity_id", arg, cfg):
            await ev.reply("✅ Added group")
        elif arg.startswith("@") and await _join_group(client, phone, "username", arg.lstrip("@"), cfg):
            await ev.reply("✅ Added group")
        else:
            await ev.reply("❌ Invalid group link/ID")

    @client.on(events.NewMessage(pattern=r"^\.listgroups$", outgoing=True))
    async def cmd_listgroups(ev):
        if not is_me(ev): return
        gs = runtime["groups"]
        await ev.reply("Groups:\n" + ("\n".join(map(str, gs)) if gs else "(none)"))

    @client.on(events.NewMessage(pattern=r"^\.delgroup\s+(\d+)$", outgoing=True))
    async def cmd_delgroup(ev):
        if not is_me(ev): return
        gid = int(ev.pattern_match.group(1))
        if gid in runtime["groups"]:
            runtime["groups"].remove(gid)
            save_runtime(phone, cfg)
            await ev.reply(f"Removed {gid}")
        else:
            await ev.reply("Not found.")

    @client.on(events.NewMessage(pattern=r"^\.delay\s+(\d+)$", outgoing=True))
    async def cmd_delay(ev):
        if not is_me(ev): return
        new_delay = max(int(ev.pattern_match.group(1)), 5)
        runtime["forward_delay"] = new_delay
        save_runtime(phone, cfg)
        await ev.reply(f"⏱️ Delay set to {new_delay}s")

    @client.on(events.NewMessage(pattern=r"^\.night\s+(on|off|status)$", outgoing=True))
    async def cmd_night(ev):
        if not is_me(ev): return
        arg = ev.pattern_match.group(1).lower()
        if arg == "on":
            runtime["night_mode"] = True
            await ev.reply("🌙 Night mode enabled (00:00–05:00)")
        elif arg == "off":
            runtime["night_mode"] = False
            await ev.reply("☀️ Night mode disabled")
        else:
            await ev.reply("🌙 Night mode is " + ("ON" if runtime["night_mode"] else "OFF"))
        save_runtime(phone, cfg)

    @client.on(events.NewMessage(pattern=r"^\.rest\s+(10m|1h|5h)$", outgoing=True))
    async def cmd_rest(ev):
        if not is_me(ev): return
        now = datetime.datetime.now()

        # Auto night mode guard
        if is_quiet_now(cfg):
            logger.info("Auto night mode: quiet window active – skipping")
            return
        arg = ev.pattern_match.group(1)
        if arg == "10m": runtime["rest_until"] = now + datetime.timedelta(minutes=10)
        elif arg == "1h": runtime["rest_until"] = now + datetime.timedelta(hours=1)
        elif arg == "5h": runtime["rest_until"] = now + datetime.timedelta(hours=5)
        await ev.reply(f"⏸️ Forwarding paused until {runtime['rest_until'].strftime('%H:%M')}")

    @client.on(events.NewMessage(pattern=r"^\.start$", outgoing=True))
    async def cmd_start(ev):
        if not is_me(ev): return
        runtime["rest_until"] = None
        await ev.reply("▶️ Forwarding resumed")

    @client.on(events.NewMessage(pattern=r"^\.status$", outgoing=True))
    async def cmd_status(ev):
        if not is_me(ev): return
        rest = "ACTIVE" if runtime["rest_until"] and datetime.datetime.now() < runtime["rest_until"] else "OFF"
        await ev.reply(f"Groups: {len(runtime['groups'])}\nDelay: {runtime['forward_delay']}s\nNight: {'ON' if runtime['night_mode'] else 'OFF'}\nRest: {rest}")

    

@client.on(events.NewMessage(pattern=r"^\.auto_night\s+(on|off)$", outgoing=True))
async def cmd_auto_night(ev):
    if not is_me(ev): return
    val = ev.pattern_match.group(1).lower() == "on"
    cfg["auto_night"] = val
    save_runtime(phone, cfg)
    await ev.reply(f"Auto night mode: {'ON' if val else 'OFF'}")

@client.on(events.NewMessage(pattern=r"^\.quiet\s+(\d{2}:\d{2})-(\d{2}:\d{2})$", outgoing=True))
async def cmd_quiet(ev):
    if not is_me(ev): return
    start, end = ev.pattern_match.group(1), ev.pattern_match.group(2)
    cfg["quiet_start"], cfg["quiet_end"] = start, end
    save_runtime(phone, cfg)
    await ev.reply(f"Quiet window set to {start}-{end}")

@client.on(events.NewMessage(pattern=r"^\.tz\s+([A-Za-z_/\-]+)$", outgoing=True))
async def cmd_tz(ev):
    if not is_me(ev): return
    tzname = ev.pattern_match.group(1)
    try:
        _ = ZoneInfo(tzname)
        cfg["timezone"] = tzname
        save_runtime(phone, cfg)
        await ev.reply(f"Timezone set to {tzname}")
    except Exception:
        await ev.reply("Invalid timezone. Example: Asia/Kolkata, UTC, Europe/Berlin")

@client.on(events.NewMessage(pattern=r"^\.help$", outgoing=True))
    async def cmd_help(ev):
        if not is_me(ev): return
        await ev.reply("Commands:\n.help\n.status\n.info\n.delay <s>\n.addgroup <link|@user|id>\n.listgroups\n.delgroup <id>\n.night on/off/status\n.rest 10m|1h|5h\n.start")

    @client.on(events.NewMessage(pattern=r"^\.info$", outgoing=True))
    async def cmd_info(ev):
        if not is_me(ev): return
        me_ = await client.get_me()
        await ev.reply(f"User: {me_.first_name}\nUsername: @{me_.username}\nID: {me_.id}")

    @client.on(events.NewMessage(chats="me", outgoing=True))
    async def forward_from_saved(ev):
        if not is_me(ev): return
        now = datetime.datetime.now()

        if runtime["night_mode"] and datetime.time(0, 0) <= now.time() <= datetime.time(5, 0):
            logger.info("Night mode active – skipping forward")
            return

        if runtime["rest_until"] and now < runtime["rest_until"]:
            logger.info("Rest active – skipping forward")
            return

        if not runtime["groups"]:
            await ev.reply("⚠️ No groups configured. Use .addgroup first.")
            return

        for gid in runtime["groups"]:
            try:
                await ev.message.forward_to(gid)
                await asyncio.sleep(runtime["forward_delay"])
            except Exception as e:
                logger.error(f"Forward failed to {gid}: {e}")

        await ev.reply(f"✅ Forwarded to {len(runtime['groups'])} groups.")

    # Graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, lambda: asyncio.create_task(client.disconnect()))

    logger.info("Bot is running...")
    await client.run_until_disconnected()


# === Entry Point ===
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        phone = normalize_phone(sys.argv[1])
    else:
        phone = normalize_phone(input("Enter phone (+countrycode): ").strip())
    asyncio.run(main(phone))
