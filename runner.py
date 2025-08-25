#!/usr/bin/env python3
# Runner for one user, started automatically after login.
# - Instantly joins groups from .addgroup
# - Saves groups & settings to users/<phone>.json (no duplicates)
# - Forwards messages from Saved Messages to all groups
# - Cycle forwarder: repeat all Saved Messages every X minutes with delay
# - .clear command to reset saved messages

import sys, json, asyncio, re
from pathlib import Path
import httpx
from telethon import TelegramClient, events, functions, types, errors

USERS_DIR = Path("users")
SESSIONS_DIR = Path("sessions")

runtime = {
    "forward_delay": 5,
    "cycle_minutes": 30,
    "groups": [],
    "saved_msgs": []
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
    save_user(phone, {
        "phone": cfg["phone"],
        "api_id": cfg["api_id"],
        "api_hash": cfg["api_hash"],
        "groups": runtime["groups"],
        "forward_delay": runtime["forward_delay"],
        "cycle_minutes": runtime["cycle_minutes"]
    })


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
                await client(functions.messages.ImportChatInviteRequest(val))
            except errors.UserAlreadyParticipantError:
                pass
            ent = await client.get_entity(val)
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
            print(f"✅ Joined and saved {added_id}")
            return True
    except Exception as e:
        print(f"❌ Failed to join {val}: {e}")
    return False


async def forward_cycle(client, phone: str, cfg: dict):
    while True:
        if runtime["groups"] and runtime["saved_msgs"]:
            print(f"⏳ Cycle triggered, forwarding {len(runtime['saved_msgs'])} saved messages...")
            for msg in runtime["saved_msgs"]:
                for gid in runtime["groups"]:
                    try:
                        await msg.forward_to(gid)
                        await asyncio.sleep(runtime["forward_delay"])
                    except Exception as e:
                        print(f"❌ Forward failed to {gid}: {e}")
        await asyncio.sleep(runtime["cycle_minutes"] * 60)


async def main(phone: str):
    cfg = load_user(phone)
    sess = SESSIONS_DIR / f"{phone}.session"
    client = TelegramClient(str(sess), cfg["api_id"], cfg["api_hash"])
    await client.start()
    me = await client.get_me()

    if "groups" in cfg:
        runtime["groups"] = cfg["groups"]
    if "forward_delay" in cfg:
        runtime["forward_delay"] = cfg["forward_delay"]
    if "cycle_minutes" in cfg:
        runtime["cycle_minutes"] = cfg["cycle_minutes"]

    print(f"✅ Runner started for {me.first_name} (@{me.username})")

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
            "➻ .clear   (reset saved messages)\n\n"
            "📌 Send messages to Saved Messages → included in cycle forward"
        )

    @client.on(events.NewMessage(pattern=r"^\.status$"))
    async def cmd_status(ev):
        await ev.reply(
            f"Groups: {len(runtime['groups'])}\n"
            f"Delay: {runtime['forward_delay']} sec\n"
            f"Cycle: {runtime['cycle_minutes']} min\n"
            f"Saved messages: {len(runtime['saved_msgs'])}"
        )

    @client.on(events.NewMessage(pattern=r"^\.info$"))
    async def cmd_info(ev):
        me = await client.get_me()
        await ev.reply(
            f"User: {me.first_name}\n"
            f"Username: @{me.username}\n"
            f"ID: {me.id}"
        )

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

    @client.on(events.NewMessage(pattern=r"^\.addgroup\s+(.+)$"))
    async def cmd_add(ev):
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
        elif m_usr:
            ok = await _join_group(client, phone, "username", m_usr.group(1), cfg)
        elif arg.isdigit():
            ok = await _join_group(client, phone, "entity_id", arg, cfg)
        else:
            ok = await _join_group(client, phone, "username", arg.lstrip('@'), cfg)
        await ev.reply("✅ Added group" if ok else "❌ Could not join")

    @client.on(events.NewMessage(pattern=r"^\.clear$"))
    async def cmd_clear(ev):
        runtime["saved_msgs"] = []
        await ev.reply("🗑️ Cleared all saved messages.")

    @client.on(events.NewMessage(chats="me"))
    async def save_from_me(ev):
        runtime["saved_msgs"].append(ev.message)
        await ev.reply(f"💾 Saved for cycle forward. Total: {len(runtime['saved_msgs'])}")

    asyncio.create_task(forward_cycle(client, phone, cfg))

    await client.run_until_disconnected()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python runner.py <phone>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
    
