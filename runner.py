#!/usr/bin/env python3
# Runner for one user, started automatically after login.
# Features:
# - Joins groups via .addgroup (supports all link types)
# - Saves groups & settings in users/<phone>.json
# - Forwards Saved Messages to groups (cycle forwarder)
# - .night mode (auto-off between 12AM–5AM if enabled)
# - .clear to reset saved messages
# - Logging with rotation

import sys, json, asyncio, re, logging, signal, datetime
from pathlib import Path
import httpx
from telethon import TelegramClient, events, functions, types, errors
from logging.handlers import RotatingFileHandler

USERS_DIR = Path("users")
SESSIONS_DIR = Path("sessions")
LOGS_DIR = Path("logs")

for d in (USERS_DIR, SESSIONS_DIR, LOGS_DIR):
    d.mkdir(exist_ok=True)

# Setup logging
log_file = LOGS_DIR / "runner.log"
logger = logging.getLogger("runner")
handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3)
logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

runtime = {
    "forward_delay": 5,
    "cycle_minutes": 30,
    "groups": [],
    "saved_msgs": [],
    "night_mode": False
}

# regex
RE_FOLDER = re.compile(r'(?:https?://)?t\.me/(?:addlist/)([A-Za-z0-9_-]+)', re.I)
RE_INVITE = re.compile(r'https?://t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)', re.I)
RE_USERLN = re.compile(r'https?://t\.me/([A-Za-z0-9_]{5,})', re.I)

def user_file(phone: str) -> Path:
    return USERS_DIR / f"{phone}.json"

def load_user(phone: str):
    f = user_file(phone)
    with open(f) as fp:
        return json.load(fp)

def save_user(phone: str, data: dict):
    f = user_file(phone)
    with open(f, "w") as fp:
        json.dump(data, fp, indent=2)

def save_runtime(phone: str, cfg: dict):
    # merge instead of overwrite
    old = cfg.copy()
    old.update({
        "groups": runtime["groups"],
        "forward_delay": runtime["forward_delay"],
        "cycle_minutes": runtime["cycle_minutes"],
        "night_mode": runtime["night_mode"]
    })
    save_user(phone, old)

async def _fetch_folder(url: str):
    async with httpx.AsyncClient(timeout=20) as hc:
        r = await hc.get(url if url.startswith('http') else f'https://{url}')
        r.raise_for_status()
        text = r.text
    invites = set(RE_INVITE.findall(text))
    users = set(u for u in RE_USERLN.findall(text) if not u.startswith(('+','joinchat')))
    return invites, users

async def _join_group(client, phone: str, kind: str, val: str, cfg: dict):
    added_id = None
    try:
        if kind == "invite":
            try:
                result = await client(functions.messages.ImportChatInviteRequest(val))
                ent = result.chats[0] if result.chats else None
            except errors.UserAlreadyParticipantError:
                ent = await client.get_entity(f"joinchat/{val}")
            if ent:
                added_id = getattr(ent, "id", None)
        elif kind == "username":
            ent = await client.get_entity(val)
            if isinstance(ent, types.Channel):
                try:
                    await client(functions.channels.JoinChannelRequest(ent))
                except errors.UserAlreadyParticipantError:
                    pass
            added_id = getattr(ent, "id", None)
        elif kind == "entity_id":
            ent = await client.get_entity(int(val))
            added_id = getattr(ent, "id", None)

        if added_id and added_id not in runtime["groups"]:
            runtime["groups"].append(added_id)
            save_runtime(phone, cfg)
            logger.info(f"Joined and saved {added_id}")
            return True
    except Exception as e:
        logger.error(f"Failed to join {val}: {e}")
    return False

async def forward_cycle(client, phone: str, cfg: dict):
    while True:
        now = datetime.datetime.now().time()
        # Night mode check
        if runtime.get("night_mode", False) and datetime.time(0, 0) <= now <= datetime.time(5, 0):
            logger.info("🌙 Night mode active – skipping cycle")
            await asyncio.sleep(300)  # 5 min sleep
            continue

        if runtime["groups"] and runtime["saved_msgs"]:
            logger.info(f"⏳ Cycle triggered, forwarding {len(runtime['saved_msgs'])} saved messages...")
            for msg in list(runtime["saved_msgs"]):
                for gid in runtime["groups"]:
                    try:
                        await msg.forward_to(gid)
                        await asyncio.sleep(runtime["forward_delay"])
                    except Exception as e:
                        logger.error(f"Forward failed to {gid}: {e}")
        await asyncio.sleep(runtime["cycle_minutes"] * 60)

async def main(phone: str):
    cfg = load_user(phone)
    sess = SESSIONS_DIR / f"{phone}.session"
    client = TelegramClient(str(sess), cfg["api_id"], cfg["api_hash"])
    await client.start()
    me = await client.get_me()

    # restore config
    runtime.update({
        "groups": cfg.get("groups", []),
        "forward_delay": cfg.get("forward_delay", 5),
        "cycle_minutes": cfg.get("cycle_minutes", 30),
        "night_mode": cfg.get("night_mode", False)
    })

    logger.info(f"Runner started for {me.first_name} (@{me.username})")

    @client.on(events.NewMessage(pattern=r"^\.night\s+(on|off|status)$"))
    async def cmd_night(ev):
        arg = ev.pattern_match.group(1).lower()
        if arg == "on":
            runtime["night_mode"] = True
            save_runtime(phone, cfg)
            await ev.reply("🌙 Night mode enabled (12AM–5AM off)")
        elif arg == "off":
            runtime["night_mode"] = False
            save_runtime(phone, cfg)
            await ev.reply("☀️ Night mode disabled")
        else:
            await ev.reply("🌙 Night mode is " + ("ON" if runtime["night_mode"] else "OFF"))

    @client.on(events.NewMessage(pattern=r"^\.help$"))
    async def cmd_help(ev):
        await ev.reply(
            "➻ .help\n"
            "➻ .status\n"
            "➻ .info\n"
            "➻ .delay <s>\n"
            "➻ .time <m>\n"
            "➻ .addgroup <link|@user|id>\n"
            "➻ .listgroups\n"
            "➻ .delgroup <id>\n"
            "➻ .clear\n"
            "➻ .night on/off/status\n\n"
            "📌 Send messages to Saved Messages → included in cycle forward"
        )

    @client.on(events.NewMessage(pattern=r"^\.status$"))
    async def cmd_status(ev):
        await ev.reply(
            f"Groups: {len(runtime['groups'])}\n"
            f"Delay: {runtime['forward_delay']} sec\n"
            f"Cycle: {runtime['cycle_minutes']} min\n"
            f"Saved messages: {len(runtime['saved_msgs'])}\n"
            f"Night mode: {'ON' if runtime['night_mode'] else 'OFF'}"
        )

    @client.on(events.NewMessage(pattern=r"^\.info$"))
    async def cmd_info(ev):
        me = await client.get_me()
        await ev.reply(f"User: {me.first_name}\nUsername: @{me.username}\nID: {me.id}")

    @client.on(events.NewMessage(pattern=r"^\.delay\s+(\d+)$"))
    async def cmd_delay(ev):
        runtime["forward_delay"] = int(ev.pattern_match.group(1))
        save_runtime(phone, cfg)
        await ev.reply(f"Delay set to {runtime['forward_delay']} s")

    @client.on(events.NewMessage(pattern=r"^\.time\s+(\d+)$"))
    async def cmd_time(ev):
        runtime["cycle_minutes"] = int(ev.pattern_match.group(1))
        save_runtime(phone, cfg)
        await ev.reply(f"Cycle interval set to {runtime['cycle_minutes']} min")

    @client.on(events.NewMessage(pattern=r"^\.listgroups$"))
    async def cmd_list(ev):
        gs = runtime["groups"]
        await ev.reply("Groups:\n" + ("\n".join(map(str, gs)) if gs else "(none)"))

    @client.on(events.NewMessage(pattern=r"^\.delgroup\s+(\d+)$"))
    async def cmd_del(ev):
        gid = int(ev.pattern_match.group(1))
        if gid in runtime["groups"]:
            runtime["groups"].remove(gid)
            save_runtime(phone, cfg)
            await ev.reply(f"Removed {gid}")
        else:
            await ev.reply("Not found.")

    # 🔗 Enhanced addgroup
    @client.on(events.NewMessage(pattern=r"^\.addgroup\s+(.+)$"))
    async def cmd_add(ev):
        arg = ev.pattern_match.group(1).strip()
        joined = 0

        # Folder link
        if RE_FOLDER.search(arg):
            invites, users = await _fetch_folder(arg)
            for h in invites:
                if await _join_group(client, phone, "invite", h, cfg):
                    joined += 1
            for u in users:
                if await _join_group(client, phone, "username", u, cfg):
                    joined += 1
            await ev.reply(f"📦 Folder added {joined} new groups.")
            return

        # Invite link
        m_inv = RE_INVITE.search(arg)
        if m_inv:
            ok = await _join_group(client, phone, "invite", m_inv.group(1), cfg)
            await ev.reply("✅ Added group" if ok else "❌ Could not join")
            return

        # Public group/channel link
        m_usr = RE_USERLN.search(arg)
        if m_usr:
            ok = await _join_group(client, phone, "username", m_usr.group(1), cfg)
            await ev.reply("✅ Added group" if ok else "❌ Could not join")
            return

        # Numeric ID
        if arg.isdigit():
            ok = await _join_group(client, phone, "entity_id", arg, cfg)
            await ev.reply("✅ Added group" if ok else "❌ Could not join")
            return

        # Plain @username
        if arg.startswith("@"):
            ok = await _join_group(client, phone, "username", arg.lstrip("@"), cfg)
            await ev.reply("✅ Added group" if ok else "❌ Could not join")
            return

        await ev.reply("❌ Invalid group link/ID")

    @client.on(events.NewMessage(pattern=r"^\.clear$"))
    async def cmd_clear(ev):
        runtime["saved_msgs"] = []
        await ev.reply("🗑️ Cleared all saved messages.")

    @client.on(events.NewMessage(chats="me"))
    async def save_from_me(ev):
        runtime["saved_msgs"].append(ev.message)
        # keep only last 1000
        if len(runtime["saved_msgs"]) > 1000:
            runtime["saved_msgs"] = runtime["saved_msgs"][-1000:]
        await ev.reply(f"💾 Saved for cycle forward. Total: {len(runtime['saved_msgs'])}")

    asyncio.create_task(forward_cycle(client, phone, cfg))

    # handle signals
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, lambda: asyncio.create_task(client.disconnect()))

    await client.run_until_disconnected()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python runner.py <phone>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
    
