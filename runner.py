# runner.py — simple: auto-forward Saved Messages with per-message interval
import asyncio, json, os, atexit
from datetime import datetime, time as dtime
from pathlib import Path

import pytz
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import ImportChatInviteRequest

CONFIG_DIR = Path("users")
SESS_DIR = Path("sessions"); SESS_DIR.mkdir(exist_ok=True)
RUNNER_PID = Path("runner.pid")

LOCAL_TZ = pytz.timezone("Asia/Kolkata")

# ----- PID file -----
def _write_pid(): RUNNER_PID.write_text(str(os.getpid()))
def _cleanup_pid():
    try: RUNNER_PID.unlink(missing_ok=True)
    except Exception: pass
_write_pid()
atexit.register(_cleanup_pid)

# ----- utils -----
def load_cfg(path: Path):
    with open(path, "r", encoding="utf-8") as f: return json.load(f)
def save_cfg(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)

def parse_interval(text: str) -> int:
    t = (text or "").strip().lower().replace(" ", "")
    if not t: return 0
    total = 0; num = ""; last = "s"; i = 0
    def add(val, unit):
        nonlocal total
        if unit == "s": total += val
        elif unit == "m": total += val*60
        elif unit == "h": total += val*3600
        elif unit == "d": total += val*86400
    while i < len(t):
        c = t[i]
        if c.isdigit(): num += c; i += 1; continue
        if t[i:].startswith(("sec","s")): unit="s"; i += 3 if t[i:].startswith("sec") else 1
        elif t[i:].startswith(("min","m")): unit="m"; i += 3 if t[i:].startswith("min") else 1
        elif t[i:].startswith(("hour","h")): unit="h"; i += 4 if t[i:].startswith("hour") else 1
        elif t[i:].startswith(("day","d")): unit="d"; i += 3 if t[i:].startswith("day") else 1
        else: unit = last; i += 1
        add(int(num) if num else 0, unit); last = unit; num = ""
    if num: add(int(num), "s")
    return total

def in_quiet_window(quiet_start: str|None, quiet_end: str|None) -> bool:
    if not quiet_start or not quiet_end: return False
    now = datetime.now(LOCAL_TZ).time()
    def _p(t): hh, mm = t.split(":"); return dtime(int(hh), int(mm))
    s, e = _p(quiet_start), _p(quiet_end)
    return (s <= now < e) if s < e else (now >= s or now < e)

async def resolve_target(client: TelegramClient, target: str):
    target = target.strip()
    try:
        if target.startswith("https://t.me/+"):
            h = target.split("+", 1)[1]
            await client(ImportChatInviteRequest(h))
        return await client.get_entity(target)
    except Exception:
        try:
            return await client.get_entity(int(target))
        except Exception as e:
            print(f"[warn] cannot resolve target '{target}': {e}")
            return None

# ----- per-user process -----
async def start_user(cfg_path: Path):
    cfg = load_cfg(cfg_path)
    phone = cfg["phone"]
    client = TelegramClient(str(SESS_DIR / phone), int(cfg["api_id"]), cfg["api_hash"])
    await client.start(phone=phone)
    me = await client.get_me()
    print(f"[{phone}] logged in as {me.first_name} ({me.id})")

    # mutable state for hot updates via commands
    interval_seconds = {"v": int(cfg.get("send_interval_seconds", 30))}
    quiet = {"s": cfg.get("quiet_start"), "e": cfg.get("quiet_end")}

    # resolve targets
    targets_cfg = list(cfg.get("targets", []))
    targets = []
    for t in targets_cfg:
        e = await resolve_target(client, t)
        if e: targets.append(e)
    print(f"[{phone}] interval={interval_seconds['v']}s; targets={targets_cfg or '[]'}")

    # work queue: forward each message to all targets, then sleep interval
    q: asyncio.Queue = asyncio.Queue()

    async def worker():
        while True:
            msg = await q.get()
            try:
                if in_quiet_window(quiet["s"], quiet["e"]):
                    print(f"[{phone}] quiet hours; skipped a message.")
                else:
                    for t in targets:
                        try:
                            await client.forward_messages(t, msg)
                            print(f"[{phone}] forwarded -> {t}")
                        except FloodWaitError as e:
                            print(f"[{phone}] FloodWait {e.seconds}s; sleeping…")
                            await asyncio.sleep(e.seconds + 1)
                            await client.forward_messages(t, msg)
                        except Exception as e:
                            print(f"[{phone}] error to {t}: {e}")
                await asyncio.sleep(interval_seconds["v"])
            finally:
                q.task_done()

    asyncio.create_task(worker())

    @client.on(events.NewMessage(chats="me"))
    async def saved_handler(ev):
        txt = (ev.raw_text or "").strip().lower()

        # ---- only these commands are supported ----
        if txt.startswith(".time"):
            parts = ev.raw_text.split(maxsplit=1)
            secs = parse_interval(parts[1]) if len(parts)>1 else 0
            interval_seconds["v"] = secs
            cfg["send_interval_seconds"] = secs
            save_cfg(cfg_path, cfg)
            await ev.reply(f"⏱ interval set to {secs}s")
            return

        if txt.startswith(".quiet"):
            parts = ev.raw_text.split(maxsplit=1)
            if len(parts)==1 or parts[1].strip().lower()=="off":
                quiet["s"] = quiet["e"] = None
                cfg["quiet_start"] = cfg["quiet_end"] = None
                save_cfg(cfg_path, cfg)
                await ev.reply("🔕 quiet hours disabled")
                return
            rng = parts[1].replace(" ", "")
            if "-" in rng:
                s, e = rng.split("-", 1)
                quiet["s"], quiet["e"] = s, e
                cfg["quiet_start"], cfg["quiet_end"] = s, e
                save_cfg(cfg_path, cfg)
                await ev.reply(f"🔕 quiet hours set {s}-{e}")
            else:
                await ev.reply("Format: .quiet 23:00-07:00  or  .quiet off")
            return

        if txt.startswith(".targets"):
            parts = ev.raw_text.split(maxsplit=1)
            if len(parts)>1:
                wanted = [s for s in parts[1].split(",") if s.strip()]
                ents = []
                for w in wanted:
                    e = await resolve_target(client, w)
                    if e: ents.append(e)
                targets[:] = ents
                cfg["targets"] = wanted
                save_cfg(cfg_path, cfg)
                await ev.reply(f"🎯 targets set: {', '.join(wanted)}")
            else:
                await ev.reply("Usage: .targets @group1,@group2,me")
            return

        # ---- normal flow: any other Saved message -> queue ----
        await q.put(ev.message)

    print(f"[{phone}] listening to Saved Messages. Drop anything there to forward.")
    await client.run_until_disconnected()

# ----- run all users -----
async def main():
    cfgs = list(CONFIG_DIR.glob("*.json"))
    if not cfgs:
        print("No users configured. Run: python3 login.py")
        return
    await asyncio.gather(*(start_user(p) for p in cfgs))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


