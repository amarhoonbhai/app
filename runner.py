
#!/usr/bin/env python3
"""
Telegram Auto Forwarder (clean build):
- Forwards messages you send to Saved Messages to configured groups/channels
- Command handlers with outgoing=True
- Auto night mode (quiet window)
- Rest mode and delay
- Phone normalization and per-user JSON state
"""

import asyncio
import datetime
import logging
import re
import signal
from pathlib import Path
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import httpx
from telethon import TelegramClient, events, functions, types, errors

# === Storage Directories ===
USERS_DIR = Path("users")
SESSIONS_DIR = Path("sessions")
LOGS_DIR = Path("logs")
for d in (USERS_DIR, SESSIONS_DIR, LOGS_DIR):
    d.mkdir(exist_ok=True)

# === Constants ===
MAX_GROUPS = 50

# === Regex Helpers ===
RE_INVITE = re.compile(r"(?:https?://)?t(?:elegram)?\.me/joinchat/([a-zA-Z0-9_-]+)|(?:https?://)?t(?:elegram)?\.me/\+([a-zA-Z0-9_-]+)")
RE_USERLN = re.compile(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)|@([A-Za-z0-9_]+)")

# === Runtime State (in-memory) ===
runtime = {
    "forward_delay": 5,
    "groups": [],
    "night_mode": False,        # manual legacy toggle
    "rest_until": None
}

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
    cfg.setdefault("groups", [])
    cfg.setdefault("forward_delay", 5)
    cfg.setdefault("night_mode", False)
    return cfg

def normalize_phone(s: str) -> str:
    return re.sub(r"\D", "", s)

# === File Helpers ===
def user_file(phone): return USERS_DIR / f"{phone}.json"

def load_user(phone):
    p = user_file(phone)
    if p.exists():
        import json
        with open(p, "r", encoding="utf-8") as fp:
            return json.load(fp)
    p_alt = USERS_DIR / f"+{phone}.json"
    if p_alt.exists():
        import json
        with open(p_alt, "r", encoding="utf-8") as fp:
            return json.load(fp)
    raise FileNotFoundError(f"User config not found for phone '{phone}' (tried {p} and {p_alt})")

def save_user(phone, data):
    import json
    with open(user_file(phone), "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=False)

def save_runtime(phone, cfg):
    # persist groups/forward_delay/night_mode/auto-night fields into user file
    cfg = dict(cfg)
    cfg["groups"] = runtime.get("groups", cfg.get("groups", []))
    cfg["forward_delay"] = runtime.get("forward_delay", cfg.get("forward_delay", 5))
    cfg["night_mode"] = runtime.get("night_mode", cfg.get("night_mode", False))
    save_user(phone, cfg)

# === Logging ===
def setup_logging(phone: str):
    log_path = LOGS_DIR / f"runner_{phone}.log"
    logger = logging.getLogger("runner")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger

# === Quiet Window Helpers ===
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

# === Web Fetch for Folder URLs ===
async def _fetch_folder(url):
    async with httpx.AsyncClient(timeout=20) as hc:
        r = await hc.get(url if url.startswith("http") else f"https://{url}")
        r.raise_for_status()
        text = r.text
    invites = set([x for t in RE_INVITE.findall(text) for x in t if x])
    users = set(u for u in [x for t in RE_USERLN.findall(text) for x in t if x] if not u.startswith(("+", "joinchat")))
    return invites, users

# === Join Helpers ===
async def _join_group(client: TelegramClient, phone: str, kind: str, val: str, cfg: dict) -> bool:
    logger = logging.getLogger("runner")
    try:
        if kind == "invite":
            link_hash = val
            res = await client(functions.messages.ImportChatInviteRequest(link_hash))
            gid = getattr(res.chats[0], "id", None)
        elif kind == "username":
            entity = await client.get_entity(val)
            gid = getattr(entity, "id", None)
        elif kind == "entity_id":
            gid = int(val)
            # ensure it exists / accessible
            _ = await client.get_entity(gid)
        else:
            return False

        if gid and gid not in runtime["groups"]:
            runtime["groups"].append(gid)
            save_runtime(phone, cfg)
            logger.info(f"Added group {gid}")
            return True
    except Exception as e:
        logger.error(f"Join failed ({kind}={val}): {e}")
    return False

# === Main ===
async def main(phone: str):
    phone = normalize_phone(phone)
    cfg = _ensure_defaults(load_user(phone))
    logger = setup_logging(phone)

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
        return bool(getattr(ev, "out", False)) or ev.sender_id == me.id

    # === Commands ===
    @client.on(events.NewMessage(pattern=r"^\.addgroup\s+(.+)$", outgoing=True))
    async def cmd_addgroup(ev):
        if not is_me(ev): return
        if len(runtime["groups"]) >= MAX_GROUPS:
            await ev.reply(f"⚠️ Group limit reached ({MAX_GROUPS}), cannot add more.")
            return
        arg = ev.pattern_match.group(1).strip()
        joined = 0

        if RE_INVITE.search(arg) or "joinchat" in arg or "/+" in arg:
            # try as invite or folder page
            if "http" in arg:
                invs, users = await _fetch_folder(arg)
                for h in invs:
                    if await _join_group(client, phone, "invite", h, cfg): joined += 1
                for u in users:
                    if await _join_group(client, phone, "username", u, cfg): joined += 1
                await ev.reply(f"📦 Folder added {joined} new groups.")
                return
            else:
                m = RE_INVITE.search(arg)
                h = next((x for x in m.groups() if x), None) if m else None
                if h and await _join_group(client, phone, "invite", h, cfg):
                    await ev.reply("✅ Added group")
                    return

        m_usr = RE_USERLN.search(arg)
        if m_usr:
            u = next((x for x in m_usr.groups() if x), None)
            if u and await _join_group(client, phone, "username", u, cfg):
                await ev.reply("✅ Added group")
                return

        if arg.isdigit() and await _join_group(client, phone, "entity_id", arg, cfg):
            await ev.reply("✅ Added group")
            return
        if arg.startswith("@") and await _join_group(client, phone, "username", arg.lstrip("@"), cfg):
            await ev.reply("✅ Added group")
            return

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
            save_runtime(phone, cfg)
            await ev.reply("🌙 Night mode enabled (manual)")
        elif arg == "off":
            runtime["night_mode"] = False
            save_runtime(phone, cfg)
            await ev.reply("☀️ Night mode disabled (manual)")
        else:
            await ev.reply("🌙 Night mode is " + ("ON" if runtime["night_mode"] else "OFF"))

    @client.on(events.NewMessage(pattern=r"^\.rest\s+(10m|1h|5h)$", outgoing=True))
    async def cmd_rest(ev):
        if not is_me(ev): return
        val = ev.pattern_match.group(1)
        now = datetime.datetime.now()
        if val == "10m":
            runtime["rest_until"] = now + datetime.timedelta(minutes=10)
        elif val == "1h":
            runtime["rest_until"] = now + datetime.timedelta(hours=1)
        else:
            runtime["rest_until"] = now + datetime.timedelta(hours=5)
        save_runtime(phone, cfg)
        await ev.reply(f"⏸️ Forwarding paused until {runtime['rest_until'].strftime('%H:%M')}")

    @client.on(events.NewMessage(pattern=r"^\.start$", outgoing=True))
    async def cmd_start(ev):
        if not is_me(ev): return
        runtime["rest_until"] = None
        save_runtime(phone, cfg)
        await ev.reply("▶️ Forwarding resumed")

    @client.on(events.NewMessage(pattern=r"^\.status$", outgoing=True))
    async def cmd_status(ev):
        if not is_me(ev): return
        now = datetime.datetime.now()
        rest = "ACTIVE" if runtime["rest_until"] and now < runtime["rest_until"] else "OFF"
        await ev.reply(f"Groups: {len(runtime['groups'])}\nDelay: {runtime['forward_delay']}s\nNight: {'ON' if runtime['night_mode'] else 'OFF'}\nRest: {rest}")

    @client.on(events.NewMessage(pattern=r"^\.help$", outgoing=True))
    async def cmd_help(ev):
        if not is_me(ev): return
        await ev.reply(
            "Commands:\n"
            ".help\n"
            ".status\n"
            ".info\n"
            ".addgroup <link|@username|id|folder_url>\n"
            ".listgroups\n"
            ".delgroup <id>\n"
            ".delay <seconds>\n"
            ".night on|off|status\n"
            ".rest 10m|1h|5h\n"
            ".start\n"
            ".auto_night on|off\n"
            ".quiet HH:MM-HH:MM\n"
            ".tz <IANA tz>\n"
        )

    @client.on(events.NewMessage(pattern=r"^\.info$", outgoing=True))
    async def cmd_info(ev):
        if not is_me(ev): return
        me_ = await client.get_me()
        await ev.reply(f"User: {me_.first_name}\nUsername: @{me_.username}\nID: {me_.id}")

    # New: Auto night mode controls
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

    @client.on(events.NewMessage(pattern=r"^\.tz\s+([A-Za-z_/\\-]+)$", outgoing=True))
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

    # Forward from Saved Messages to configured groups (respect quiet/rest)
    @client.on(events.NewMessage(chats="me"))
    async def forward_from_saved(ev):
        if not is_me(ev): return
        now = datetime.datetime.now()

        # manual night window (legacy fixed)
        if runtime["night_mode"] and datetime.time(0, 0) <= now.time() <= datetime.time(5, 0):
            logging.getLogger("runner").info("Night mode active – skipping forward")
            return

        # auto night quiet window
        if is_quiet_now(cfg):
            logging.getLogger("runner").info("Auto night mode: quiet window active – skipping")
            return

        if runtime["rest_until"] and now < runtime["rest_until"]:
            logging.getLogger("runner").info("Rest active – skipping forward")
            return

        if not runtime["groups"]:
            await ev.reply("⚠️ No groups configured. Use .addgroup first.")
            return

        for gid in runtime["groups"]:
            try:
                await ev.message.forward_to(gid)
                await asyncio.sleep(runtime["forward_delay"])
            except Exception as e:
                logging.getLogger("runner").error(f"Forward failed to {gid}: {e}")

        await ev.reply(f"✅ Forwarded to {len(runtime['groups'])} groups.")

    # Graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, lambda: asyncio.create_task(client.disconnect()))

    logging.getLogger("runner").info("Bot is running...")
    await client.run_until_disconnected()


# === Entry Point ===
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        phone = normalize_phone(sys.argv[1])
    else:
        phone = normalize_phone(input("Enter phone (+countrycode): ").strip())
    asyncio.run(main(phone))
