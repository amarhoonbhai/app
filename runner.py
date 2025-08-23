#!/usr/bin/env python3
# Runner for one user, started automatically after login.

import sys, json, asyncio, re, time, random
from pathlib import Path
import httpx
from telethon import TelegramClient, events, functions, types, errors

USERS_DIR = Path("users")
SESSIONS_DIR = Path("sessions")

# runtime state
runtime = {
    "cycle_minutes": 10,
    "forward_delay": 5,
    "queue": [],
    "groups": [],
    "join_marks": [],
    "active_fw": 0
}

# regex
RE_FOLDER = re.compile(r'(?:https?://)?t\.me/(?:addlist/)([A-Za-z0-9_-]+)', re.I)
RE_INVITE = re.compile(r'https?://t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)', re.I)
RE_USERLN = re.compile(r'https?://t\.me/([A-Za-z0-9_]{5,})', re.I)


def load_user(phone: str):
    with open(USERS_DIR / f"{phone}.json") as fp:
        return json.load(fp)


def _fmt(sec: int) -> str:
    sec = max(0, sec)
    h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


def _clean_marks():
    now = time.time()
    while runtime["join_marks"] and now - runtime["join_marks"][0] > 3600:
        runtime["join_marks"].pop(0)


def _avg_gap():
    avg = 60
    cap = 3600.0 / 15
    return max(avg, cap)


def _eta(qsize: int):
    _clean_marks()
    return runtime["active_fw"] + int(qsize * _avg_gap())


async def _pause():
    await asyncio.sleep(random.randint(45, 90))


def _extract(html: str):
    invites = set(RE_INVITE.findall(html))
    users = set(u for u in RE_USERLN.findall(html) if not u.startswith(('+','joinchat')))
    return invites, users


async def _fetch_folder(url: str):
    async with httpx.AsyncClient(timeout=20) as hc:
        r = await hc.get(url if url.startswith('http') else f'https://{url}')
        r.raise_for_status()
        return _extract(r.text)


async def _join_worker(client):
    while True:
        if not runtime["queue"]:
            await asyncio.sleep(5)
            continue
        item = runtime["queue"].pop(0)
        kind, val = item["kind"], item["value"]

        try:
            added_id = None
            if kind == "invite":
                try:
                    await client(functions.messages.ImportChatInviteRequest(val))
                except errors.UserAlreadyParticipantError:
                    pass
                added_id = await _resolve_id(client, val)
            elif kind == "username":
                ent = await _ensure_join_user(client, val)
                added_id = getattr(ent, "id", None)
            elif kind == "entity_id":
                ent = await client.get_entity(int(val))
                added_id = getattr(ent, "id", None)

            if added_id and added_id not in runtime["groups"]:
                runtime["groups"].append(added_id)
                runtime["join_marks"].append(time.time())
                print(f"✅ Joined {added_id} (queue {len(runtime['queue'])})")
            else:
                print(f"ℹ️ Already in group or could not resolve {val}")

        except errors.FloodWaitError as fw:
            sec = int(getattr(fw, "seconds", 60))
            runtime["active_fw"] = sec
            print(f"⏳ FloodWait {sec}s")
            runtime["queue"].insert(0, item)  # requeue
            await asyncio.sleep(sec + random.randint(5, 15))
            runtime["active_fw"] = 0
        except Exception as e:
            print(f"❌ Join error {val}: {e}")

        await _pause()


async def _ensure_join_user(client, u: str):
    ent = await client.get_entity(u)
    if isinstance(ent, types.Channel):
        try:
            await client(functions.channels.JoinChannelRequest(ent))
        except errors.UserAlreadyParticipantError:
            pass
    return ent


async def _resolve_id(client, val: str):
    try:
        ent = await client.get_entity(val)
        return getattr(ent, "id", None)
    except Exception:
        return None


async def main(phone: str):
    cfg = load_user(phone)
    sess = SESSIONS_DIR / f"{phone}.session"
    client = TelegramClient(str(sess), cfg["api_id"], cfg["api_hash"])
    await client.start()
    me = await client.get_me()
    print(f"✅ Runner started for {phone} ({me.first_name})")

    # --- Commands ---
    @client.on(events.NewMessage(pattern=r"^\.help$"))
    async def cmd_help(ev):
        await ev.reply(
            ".help\n.status\n.info\n.time <m>\n.delay <s>\n"
            ".addgroup <link|@user|id>\n.joinqueue\n.listgroups\n.delgroup <id>"
        )

    @client.on(events.NewMessage(pattern=r"^\.status$"))
    async def cmd_status(ev):
        await ev.reply(
            f"Queue: {len(runtime['queue'])}\n"
            f"Groups: {len(runtime['groups'])}\n"
            f"Cycle: {runtime['cycle_minutes']} min\n"
            f"Delay: {runtime['forward_delay']} sec"
        )

    @client.on(events.NewMessage(pattern=r"^\.info$"))
    async def cmd_info(ev):
        me = await client.get_me()
        await ev.reply(f"Phone: {cfg['phone']}\nUser: {me.first_name}\nID: {me.id}\nUsername: @{me.username}")

    @client.on(events.NewMessage(pattern=r"^\.time\s+(.+)$"))
    async def cmd_time(ev):
        val = ev.pattern_match.group(1).lower()
        m = re.match(r"(\d+)(m|min|minutes?)", val)
        if not m:
            await ev.reply("Usage: .time 10m")
            return
        runtime["cycle_minutes"] = int(m.group(1))
        await ev.reply(f"Cycle set to {runtime['cycle_minutes']} min")

    @client.on(events.NewMessage(pattern=r"^\.delay\s+(.+)$"))
    async def cmd_delay(ev):
        val = ev.pattern_match.group(1).lower()
        m = re.match(r"(\d+)(s|sec|seconds?)", val)
        if not m:
            await ev.reply("Usage: .delay 200s")
            return
        runtime["forward_delay"] = int(m.group(1))
        await ev.reply(f"Delay set to {runtime['forward_delay']} s")

    @client.on(events.NewMessage(pattern=r"^\.listgroups$"))
    async def cmd_list(ev):
        gs = runtime["groups"]
        await ev.reply("Groups:\n" + ("\n".join(map(str, gs)) if gs else "(none)"))

    @client.on(events.NewMessage(pattern=r"^\.delgroup\s+(\d+)$"))
    async def cmd_del(ev):
        gid = int(ev.pattern_match.group(1))
        if gid in runtime["groups"]:
            runtime["groups"].remove(gid)
            await ev.reply(f"Removed {gid}")
        else:
            await ev.reply("Not found.")

    @client.on(events.NewMessage(pattern=r"^\.addgroup\s+(.+)$"))
    async def cmd_add(ev):
        arg = ev.pattern_match.group(1).strip()
        if RE_FOLDER.search(arg):
            invites, users = await _fetch_folder(arg)
            items = [{"kind": "invite", "value": h} for h in invites] + [{"kind": "username", "value": u} for u in users]
            runtime["queue"].extend(items)
            await ev.reply(f"Folder queued. {len(items)} items. Queue={len(runtime['queue'])}, ETA={_fmt(_eta(len(runtime['queue'])))}")
            return
        m_inv = RE_INVITE.search(arg)
        m_usr = RE_USERLN.search(arg)
        if m_inv:
            runtime["queue"].append({"kind": "invite", "value": m_inv.group(1)})
        elif m_usr:
            runtime["queue"].append({"kind": "username", "value": m_usr.group(1)})
        elif arg.isdigit():
            runtime["queue"].append({"kind": "entity_id", "value": arg})
        else:
            runtime["queue"].append({"kind": "username", "value": arg.lstrip('@')})
        await ev.reply(f"Queued. Size={len(runtime['queue'])}, ETA={_fmt(_eta(len(runtime['queue'])))}")

    @client.on(events.NewMessage(pattern=r"^\.joinqueue$"))
    async def cmd_queue(ev):
        q = len(runtime["queue"])
        await ev.reply(f"Queue: {q} | ETA={_fmt(_eta(q))}")

    # start join worker
    asyncio.create_task(_join_worker(client))

    await client.run_until_disconnected()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python runner.py <phone>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
