import asyncio
import json
import os
import re
import sys
from datetime import datetime, time as dt_time, timedelta
from typing import List

import pytz
from telethon import TelegramClient, events
from telethon.errors import RPCError
from telethon.tl.types import PeerUser

# ====================== Config & Defaults ======================
TZ = pytz.timezone("Asia/Kolkata")

DEFAULT_CONFIG = {
    "phone": "",
    "api_id": 0,
    "api_hash": "",
    "targets": [],                 # list of usernames/links/-100 ids
    "interval_seconds": 30,        # wait between Saved-jobs
    "gap_seconds": 5,              # delay between multiple targets (broadcast)
    "mode": "rotation",            # rotation | broadcast
    "quiet": {"enabled": True, "start": "23:00", "end": "07:00"},
    "expire_date": "2026-01-10",   # YYYY-MM-DD
    "rot_index": 0,                # persisted round-robin pointer
}

CONFIG_PATH = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("USER_CONFIG", "")
if not CONFIG_PATH:
    print("Usage: python3 runner.py users/<label_or_phone>.json")
    sys.exit(1)

with open(CONFIG_PATH, "r") as f:
    CFG = {**DEFAULT_CONFIG, **json.load(f)}

def save_cfg():
    with open(CONFIG_PATH, "w") as f:
        json.dump(CFG, f, indent=2)

def _parse_hhmm(s: str) -> dt_time:
    hh, mm = s.strip().split(":")
    return dt_time(int(hh), int(mm), 0)

def parse_duration(s: str) -> int:
    """30s 5m 2h -> seconds"""
    s = s.strip().lower()
    m = re.fullmatch(r"(\d+)([smh])", s)
    if not m:
        raise ValueError("Use formats like 30s, 5m, 2h")
    n, unit = int(m.group(1)), m.group(2)
    return n if unit == "s" else n * 60 if unit == "m" else n * 3600

def now_local():
    return datetime.now(TZ)

def is_quiet_hours() -> bool:
    q = CFG["quiet"]
    if not q.get("enabled"):
        return False
    start = _parse_hhmm(q["start"])
    end = _parse_hhmm(q["end"])
    n = now_local().time()
    if start <= end:
        return start <= n < end
    # over-midnight window
    return n >= start or n < end

def quiet_ends_in_seconds() -> int:
    q = CFG["quiet"]
    end = _parse_hhmm(q["end"])
    n = now_local()
    end_dt = n.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if end_dt <= n:
        end_dt += timedelta(days=1)
    return int((end_dt - n).total_seconds())

def is_expired() -> bool:
    try:
        ed = datetime.strptime(CFG["expire_date"], "%Y-%m-%d").date()
    except Exception:
        return False
    return now_local().date() > ed

# ====================== Telethon Setup ======================
SESS_DIR = "sessions"
os.makedirs(SESS_DIR, exist_ok=True)
session_name = os.path.join(SESS_DIR, CFG["phone"])

client = TelegramClient(session_name, CFG["api_id"], CFG["api_hash"])
queue = asyncio.Queue()
SELF_ID = None  # filled on start


# ====================== Helpers ======================
async def resolve_targets(raw_list: List[str]):
    """Resolve @handles, t.me links, or -100... ids."""
    ok = []
    for item in raw_list:
        s = item.strip()
        if not s:
            continue
        try:
            # allow raw numeric ids (e.g., -1001234567890)
            if s.lstrip("-").isdigit():
                ent = await client.get_entity(int(s))
            else:
                ent = await client.get_entity(s)
            ok.append(ent)
        except Exception as e:
            print(f"[targets] could not resolve {s}: {e}")
    return ok

def is_saved_messages(ev: events.NewMessage.Event) -> bool:
    # Saved Messages (chat with yourself)
    peer = ev.message.peer_id
    return isinstance(peer, PeerUser) and getattr(peer, "user_id", None) == SELF_ID

async def forward_one(msg, target):
    try:
        await client.forward_messages(entity=target, messages=msg)
    except RPCError as e:
        print(f"[forward] RPCError to {target}: {e}")
    except Exception as e:
        print(f"[forward] error to {target}: {e}")

async def send_status(title="✅ Online"):
    tgts = ", ".join(CFG["targets"]) if CFG["targets"] else "—"
    txt = (
        f"{title}\n"
        f"Mode: {CFG['mode']} | Interval: {CFG['interval_seconds']}s | Gap: {CFG['gap_seconds']}s\n"
        f"Targets: {tgts}\n"
        f"Quiet: {'ON' if CFG['quiet']['enabled'] else 'OFF'} ({CFG['quiet']['start']}-{CFG['quiet']['end']})\n"
        f"Expire date: {CFG['expire_date']} | RotIndex: {CFG['rot_index']}"
    )
    await client.send_message("me", txt)

# ====================== Commands ======================
CMD_HELP = """
Commands:
.addgroup a,b,c         -> add/merge targets (@user, https://t.me/xxx or -100id)
.delgroup a             -> remove one (by username or id)
.cleargroups            -> remove all
.targets                -> show targets
.time 30s|5m|2h         -> set interval between Saved jobs
.gap 5s|10s             -> set delay between multiple targets (broadcast)
.mode rotation|broadcast-> choose send mode (default rotation)
.quiet off|HH:MM-HH:MM  -> toggle/set quiet hours
.expire YYYY-MM-DD      -> set expire date
.status                 -> show status
.help                   -> this help
""".strip()

async def process_command(ev, raw) -> bool:
    text = (raw or "").strip()
    if not text.startswith("."):
        return False

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == ".help":
        await ev.reply(CMD_HELP)
        return True

    if cmd == ".targets":
        await send_status("🎯 Targets")
        return True

    if cmd == ".addgroup":
        if not arg:
            await ev.reply("Usage: .addgroup @a,@b,-100123456")
            return True
        raw_list = [x for y in arg.split(",") for x in [y.strip()] if x]
        ents = await resolve_targets(raw_list)
        added = []
        for x, ent in zip(raw_list, ents):
            if x not in CFG["targets"]:
                CFG["targets"].append(x)
                added.append(x)
        save_cfg()
        await ev.reply(f"✅ Added: {', '.join(added) if added else 'None'}")
        return True

    if cmd == ".delgroup":
        if not arg:
            await ev.reply("Usage: .delgroup @handle or -100id")
            return True
        before = len(CFG["targets"])
        CFG["targets"] = [t for t in CFG["targets"] if t != arg]
        save_cfg()
        await ev.reply(f"🗑️ Removed: {arg} ({before-len(CFG['targets'])} removed)")
        return True

    if cmd == ".cleargroups":
        CFG["targets"] = []
        save_cfg()
        await ev.reply("🧹 Cleared all targets.")
        return True

    if cmd == ".time":
        try:
            CFG["interval_seconds"] = parse_duration(arg)
            save_cfg()
            await ev.reply(f"⏱️ Interval set to {CFG['interval_seconds']}s")
        except Exception as e:
            await ev.reply(f"Error: {e}")
        return True

    if cmd == ".gap":
        try:
            CFG["gap_seconds"] = parse_duration(arg)
            save_cfg()
            await ev.reply(f"↔️ Gap set to {CFG['gap_seconds']}s")
        except Exception as e:
            await ev.reply(f"Error: {e}")
        return True

    if cmd == ".mode":
        if arg.lower() not in ("rotation", "broadcast"):
            await ev.reply("Use: .mode rotation | broadcast")
            return True
        CFG["mode"] = arg.lower()
        save_cfg()
        await ev.reply(f"🎛️ Mode set to {CFG['mode']}")
        return True

    if cmd == ".quiet":
        if arg.lower() == "off":
            CFG["quiet"]["enabled"] = False
        else:
            m = re.fullmatch(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", arg)
            if not m:
                await ev.reply("Use: .quiet off OR .quiet 23:00-07:00")
                return True
            CFG["quiet"]["enabled"] = True
            CFG["quiet"]["start"] = m.group(1)
            CFG["quiet"]["end"] = m.group(2)
        save_cfg()
        await ev.reply(f"🌙 Quiet: {'ON' if CFG['quiet']['enabled'] else 'OFF'} "
                       f"({CFG['quiet']['start']}-{CFG['quiet']['end']})")
        return True

    if cmd == ".expire":
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", arg)
        if not m:
            await ev.reply("Use: .expire YYYY-MM-DD (e.g., 2026-01-10)")
            return True
        CFG["expire_date"] = m.group(1)
        save_cfg()
        await ev.reply(f"📅 Expire date set to {CFG['expire_date']}")
        return True

    if cmd == ".status":
        await send_status("ℹ️ Status")
        return True

    return False

# ====================== Event Handlers ======================
# Commands from ANY chat sent by YOU (handles outgoing and rare incoming self-echoes).
@client.on(events.NewMessage)
async def commands_anywhere(ev):
    raw = (ev.raw_text or "")
    # only process if the message is from your own account
    is_self = bool(getattr(ev, "out", False)) or (getattr(ev, "sender_id", None) == SELF_ID)
    if not is_self:
        return
    # allow leading spaces before the dot
    if not raw.lstrip().startswith("."):
        return
    await process_command(ev, raw.lstrip())

# Catch non-command messages in Saved Messages and enqueue them for forwarding
@client.on(events.NewMessage(outgoing=True))
async def saved_catcher(ev):
    raw = ev.raw_text or ""
    if raw.lstrip().startswith("."):
        return  # commands handled above
    # forward ONLY when posted in Saved Messages
    if not is_saved_messages(ev):
        return
    await queue.put(ev.message)

# ====================== Worker ======================
async def worker_loop():
    while True:
        msg = await queue.get()
        try:
            if is_expired():
                await send_status("⛔ Expired. Stopping.")
                os._exit(0)

            if is_quiet_hours():
                wait = quiet_ends_in_seconds()
                await client.send_message("me", f"🌙 Quiet hours active. Sleeping {wait}s…")
                await asyncio.sleep(wait)

            if not CFG["targets"]:
                await client.send_message("me", "⚠️ No targets set; message skipped.")
                continue

            entities = await resolve_targets(CFG["targets"])
            if not entities:
                await client.send_message("me", "⚠️ Targets unresolved; check .targets or .addgroup")
                continue

            if CFG["mode"] == "rotation":
                idx = CFG["rot_index"] % len(entities)
                target = entities[idx]
                CFG["rot_index"] += 1
                save_cfg()
                await forward_one(msg, target)
            else:
                for i, target in enumerate(entities):
                    await forward_one(msg, target)
                    if i < len(entities) - 1 and CFG["gap_seconds"] > 0:
                        await asyncio.sleep(CFG["gap_seconds"])

            if CFG["interval_seconds"] > 0:
                await asyncio.sleep(CFG["interval_seconds"])

        finally:
            queue.task_done()

# ====================== Main ======================
async def main():
    global SELF_ID
    await client.start()
    me = await client.get_me()
    SELF_ID = me.id

    await send_status()  # greet in Saved
    asyncio.create_task(worker_loop())
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
  
