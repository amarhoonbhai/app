#!/usr/bin/env python3
# Runner with direct Saved Messages forwarding & safety features
# - Auto forwards all Saved Messages to groups
# - Rest mode (.rest 10m/1h/5h, .start)
# - Safe delays (min 5s)
# - Night mode (00:00–05:00 off if enabled)
# - Max groups cap (50)
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
    "groups": [],
    "night_mode": False,
    "rest_until": None  # ⏸ pause timestamp
}

MAX_GROUPS = 50  # 🔒 group cap for safety

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
    old = cfg.copy()
    old.update({
        "groups": runtime["groups"],
        "forward_delay": runtime["forward_delay"],
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
    if len(runtime["groups"]) >= MAX_GROUPS:
        logger.warning("Group cap reached – cannot add more")
        return False

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

async def main(phone: str):
    cfg = load_user(phone)
    sess = SESSIONS_DIR / f"{phone}.session"
    client = TelegramClient(str(sess), cfg["api_id"], cfg["api_hash"], use_ipv6=False)
    await client.start()
    me = await client.get_me()

    runtime.update({
        "groups": cfg.get("groups", []),
        "forward_delay": max(cfg.get("forward_delay", 5), 5),
        "night_mode": cfg.get("night_mode", False),
        "rest_until": None
    })

    logger.info(f"Runner started for {me.first_name} (@{me.username})")

    def is_me(ev):
        return ev.sender_id == me.id

    # --- Command handlers ---
    @client.on(events.NewMessage(pattern=r"^\.delay\s+(\d+)$"))
    async def cmd_delay(ev):
        if not is_me(ev): return
        new_delay = int(ev.pattern_match.group(1))
        if new_delay < 5:
            new_delay = 5
        runtime["forward_delay"] = new_delay
        save_runtime(phone, cfg)
        await ev.reply(f"⏱️ Delay set to {runtime['forward_delay']} s (min 5s for safety)")

    @client.on(events.NewMessage(pattern=r"^\.night\s+(on|off|status)$"))
    async def cmd_night(ev):
        if not is_me(ev): return
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

    @client.on(events.NewMessage(pattern=r"^\.rest\s+(10m|1h|5h)$"))
    async def cmd_rest(ev):
        if not is_me(ev): return
        arg = ev.pattern_match.group(1)
        now = datetime.datetime.now()
        if arg == "10m":
            runtime["rest_until"] = now + datetime.timedelta(minutes=10)
        elif arg == "1h":
            runtime["rest_until"] = now + datetime.timedelta(hours=1)
        elif arg == "5h":
            runtime["rest_until"] = now + datetime.timedelta(hours=5)
        until = runtime["rest_until"].strftime("%H:%M")
        await ev.reply(f"⏸️ Forwarding paused until {until}")

    @client.on(events.NewMessage(pattern=r"^\.start$"))
    async def cmd_start(ev):
        if not is_me(ev): return
        runtime["rest_until"] = None
        await ev.reply("▶️ Forwarding resumed")

    @client.on(events.NewMessage(pattern=r"^\.status$"))
    async def cmd_status(ev):
        if not is_me(ev): return
        rest_info = "ACTIVE" if runtime["rest_until"] and datetime.datetime.now() < runtime["rest_until"] else "OFF"
        await ev.reply(
            f"Groups: {len(runtime['groups'])}\n"
            f"Delay: {runtime['forward_delay']} sec\n"
            f"Night mode: {'ON' if runtime['night_mode'] else 'OFF'}\n"
            f"Rest mode: {rest_info}"
        )

    @client.on(events.NewMessage(pattern=r"^\.info$"))
    async def cmd_info(ev):
        if not is_me(ev): return
        me_ = await client.get_me()
        await ev.reply(f"User: {me_.first_name}\nUsername: @{me_.username}\nID: {me_.id}")

    @client.on(events.NewMessage(pattern=r"^\.help$"))
    async def cmd_help(ev):
        if not is_me(ev): return
        await ev.reply(
            "➻ .help\n"
            "➻ .status\n"
            "➻ .info\n"
            "➻ .delay <s> (min 5)\n"
            "➻ .addgroup <link|@user|id>\n"
            "➻ .listgroups\n"
            "➻ .delgroup <id>\n"
            "➻ .night on/off/status\n"
            "➻ .rest 10m|1h|5h\n"
            "➻ .start (resume)\n\n"
            "📌 Any message you put in Saved Messages is forwarded automatically."
        )

    @client.on(events.NewMessage(pattern=r"^\.listgroups$"))
    async def cmd_listgroups(ev):
        if not is_me(ev): return
        gs = runtime["groups"]
        await ev.reply("Groups:\n" + ("\n".join(map(str, gs)) if gs else "(none)"))

    @client.on(events.NewMessage(pattern=r"^\.delgroup\s+(\d+)$"))
    async def cmd_delgroup(ev):
        if not is_me(ev): return
        gid = int(ev.pattern_match.group(1))
        if gid in runtime["groups"]:
            runtime["groups"].remove(gid)
            save_runtime(phone, cfg)
            await ev.reply(f"Removed {gid}")
        else:
            await ev.reply("Not found.")

    @client.on(events.NewMessage(pattern=r"^\.addgroup\s+(.+)$"))
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
                if await _join_group(client, phone, "invite", h, cfg):
                    joined += 1
            for u in users:
                if await _join_group(client, phone, "username", u, cfg):
                    joined += 1
            await ev.reply(f"📦 Folder added {joined} new groups.")
            return

        m_inv = RE_INVITE.search(arg)
        m_usr = RE_USERLN.search(arg)

        if m_inv:
            ok = await _join_group(client, phone, "invite", m_inv.group(1), cfg)
            await ev.reply("✅ Added group" if ok else "❌ Could not join")
            return
        if m_usr:
            ok = await _join_group(client, phone, "username", m_usr.group(1), cfg)
            await ev.reply("✅ Added group" if ok else "❌ Could not join")
            return
        if arg.isdigit():
            ok = await _join_group(client, phone, "entity_id", arg, cfg)
            await ev.reply("✅ Added group" if ok else "❌ Could not join")
            return
        if arg.startswith("@"):
            ok = await _join_group(client, phone, "username", arg.lstrip("@"), cfg)
            await ev.reply("✅ Added group" if ok else "❌ Could not join")
            return

        await ev.reply("❌ Invalid group link/ID")

    # --- Direct forwarding from Saved Messages ---
    @client.on(events.NewMessage(chats="me"))
    async def forward_from_saved(ev):
        if not is_me(ev): return

        now = datetime.datetime.now()

        # Night mode
        if runtime["night_mode"] and datetime.time(0, 0) <= now.time() <= datetime.time(5, 0):
            logger.info("🌙 Night mode active – skipping forward")
            return

        # Rest mode
        if runtime["rest_until"] and now < runtime["rest_until"]:
            logger.info("⏸ Rest active – skipping forward")
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

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, lambda: asyncio.create_task(client.disconnect()))

    await client.run_until_disconnected()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python runner.py <phone>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
