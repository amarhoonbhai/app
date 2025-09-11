#!/usr/bin/env python3
# Runner with round-robin forwarding & safety features
# - Safe delays (min 5s)
# - Round-robin forwarding (1 msg → 1 group at a time)
# - Night mode
# - Max groups cap (50)

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
    "cycle_minutes": 30,
    "groups": [],
    "saved_msgs": [],
    "night_mode": False
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

async def forward_cycle(client, phone: str, cfg: dict):
    msg_index = 0
    group_index = 0

    while True:
        now = datetime.datetime.now().time()
        if runtime.get("night_mode", False) and datetime.time(0, 0) <= now <= datetime.time(5, 0):
            logger.info("🌙 Night mode active – skipping cycle")
            await asyncio.sleep(300)
            continue

        if runtime["groups"] and runtime["saved_msgs"]:
            msg = runtime["saved_msgs"][msg_index % len(runtime["saved_msgs"])]
            gid = runtime["groups"][group_index % len(runtime["groups"])]

            try:
                await msg.forward_to(gid)
                logger.info(f"Forwarded msg[{msg_index}] to group {gid}")
            except Exception as e:
                logger.error(f"Forward failed to {gid}: {e}")

            msg_index += 1
            group_index += 1

        await asyncio.sleep(runtime["forward_delay"])

async def main(phone: str):
    cfg = load_user(phone)
    sess = SESSIONS_DIR / f"{phone}.session"
    client = TelegramClient(str(sess), cfg["api_id"], cfg["api_hash"], use_ipv6=False)
    await client.start()
    me = await client.get_me()

    runtime.update({
        "groups": cfg.get("groups", []),
        "forward_delay": max(cfg.get("forward_delay", 5), 5),
        "cycle_minutes": cfg.get("cycle_minutes", 30),
        "night_mode": cfg.get("night_mode", False)
    })

    logger.info(f"Runner started for {me.first_name} (@{me.username})")

    def is_me(ev):
        return ev.sender_id == me.id

    # Commands ---
    @client.on(events.NewMessage(pattern=r"^\.addgroup\s+(.+)$"))
    async def cmd_add(ev):
        if not is_me(ev): return
        if len(runtime["groups"]) >= MAX_GROUPS:
            await ev.reply(f"⚠️ Group limit reached ({MAX_GROUPS}), cannot add more.")
            return
        arg = ev.pattern_match.group(1).strip()
        joined = 0
        m_inv = RE_INVITE.search(arg)
        m_usr = RE_USERLN.search(arg)

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

    @client.on(events.NewMessage())
    async def save_from_me(ev):
        if not is_me(ev): return
        runtime["saved_msgs"].append(ev.message)
        if len(runtime["saved_msgs"]) > 1000:
            runtime["saved_msgs"] = runtime["saved_msgs"][-1000:]
        await ev.reply(f"💾 Saved for cycle forward. Total: {len(runtime['saved_msgs'])}")

    asyncio.create_task(forward_cycle(client, phone, cfg))

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, lambda: asyncio.create_task(client.disconnect()))

    await client.run_until_disconnected()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python runner.py <phone>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
    
