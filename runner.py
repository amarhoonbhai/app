# runner.py — Saved -> groups with autonight, per-message delay, per-forward gap, rotation
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
DEFAULT_AUTONIGHT = ("23:00", "07:00")  # used when .autonight ON but no .quiet set

# ---------- PID ----------
def _write_pid(): RUNNER_PID.write_text(str(os.getpid()))
def _cleanup_pid():
    try: RUNNER_PID.unlink(missing_ok=True)
    except Exception: pass
_write_pid(); atexit.register(_cleanup_pid)

# ---------- utils ----------
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
    t = target.strip()
    try:
        if t in ("me","self","saved","saved_messages"):
            return await client.get_entity("me")
        if "t.me/+" in t or "t.me/joinchat/" in t:
            plus = t.split("+", 1)[-1] if "+" in t else t.rstrip("/").split("/", 3)[-1]
            await client(ImportChatInviteRequest(plus))
        return await client.get_entity(t)
    except Exception:
        try:
            return await client.get_entity(int(t))
        except Exception as e:
            print(f"[warn] cannot resolve '{t}': {e}")
            return None

# ---------- per-user ----------
async def start_user(cfg_path: Path):
    cfg = load_cfg(cfg_path)
    phone = cfg["phone"]
    client = TelegramClient(str(SESS_DIR / phone), int(cfg["api_id"]), cfg["api_hash"])
    await client.start(phone=phone)
    me = await client.get_me()
    my_saved = await client.get_input_entity("me")

    # state
    interval_s = {"v": int(cfg.get("send_interval_seconds", 30))}  # per-message sleep
    forward_gap_s = {"v": int(cfg.get("forward_gap_seconds", 2))}  # between each group send
    quiet = {"s": cfg.get("quiet_start"), "e": cfg.get("quiet_end")}
    autonight = {"v": bool(cfg.get("autonight", False))}
    rotation_mode = {"v": cfg.get("rotation_mode", "broadcast")}  # "broadcast" | "roundrobin"
    rot_index = {"v": int(cfg.get("rot_index", 0))}

    # targets
    targets_cfg = list(cfg.get("targets", []))
    targets = []
    for t in targets_cfg:
        e = await resolve_target(client, t)
        if e: targets.append(e)

    print(f"[{phone}] logged in as {me.first_name} ({me.id})")
    print(f"[{phone}] interval={interval_s['v']}s | gap={forward_gap_s['v']}s | mode={rotation_mode['v']} | autonight={autonight['v']} | quiet={quiet['s']}→{quiet['e']}")
    await client.send_message(my_saved,
        f"✅ Online.\nInterval: {interval_s['v']}s\nGap: {forward_gap_s['v']}s\nMode: {rotation_mode['v']}\n"
        f"Autonight: {autonight['v']} (quiet {quiet['s'] or DEFAULT_AUTONIGHT[0]}–{quiet['e'] or DEFAULT_AUTONIGHT[1]})\n"
        f"Targets: {', '.join(targets_cfg) if targets_cfg else '—'}"
    )

    def quiet_active() -> bool:
        s, e = quiet["s"], quiet["e"]
        if autonight["v"] and (not s or not e):
            s, e = DEFAULT_AUTONIGHT
        return in_quiet_window(s, e)

    # queue worker
    q: asyncio.Queue = asyncio.Queue()

    async def worker():
        while True:
            msg = await q.get()
            try:
                if quiet_active():
                    print(f"[{phone}] quiet hours — skipped 1 message.")
                elif not targets:
                    print(f"[{phone}] no targets set; message skipped.")
                else:
                    if rotation_mode["v"] == "roundrobin":
                        t = targets[rot_index["v"] % len(targets)]
                        try:
                            await client.forward_messages(t, msg)
                            print(f"[{phone}] RR sent -> {t}")
                        except FloodWaitError as e:
                            print(f"[{phone}] FloodWait {e.seconds}s; sleeping…")
                            await asyncio.sleep(e.seconds + 1)
                            await client.forward_messages(t, msg)
                        except Exception as e:
                            print(f"[{phone}] error -> {t}: {e}")
                        rot_index["v"] = (rot_index["v"] + 1) % max(1, len(targets))
                        cfg["rot_index"] = rot_index["v"]; save_cfg(cfg_path, cfg)
                    else:
                        # broadcast to all; wait 'gap' between each
                        for i, t in enumerate(targets):
                            try:
                                await client.forward_messages(t, msg)
                                print(f"[{phone}] sent -> {t}")
                            except FloodWaitError as e:
                                print(f"[{phone}] FloodWait {e.seconds}s; sleeping…")
                                await asyncio.sleep(e.seconds + 1)
                                await client.forward_messages(t, msg)
                            except Exception as e:
                                print(f"[{phone}] error -> {t}: {e}")
                            if i < len(targets)-1 and forward_gap_s["v"] > 0:
                                await asyncio.sleep(forward_gap_s["v"])

                    # sleep once per message (queue pacing)
                    if interval_s["v"] > 0:
                        await asyncio.sleep(interval_s["v"])
            finally:
                q.task_done()

    asyncio.create_task(worker())

    # -------- Saved Messages listener --------
    @client.on(events.NewMessage(chats=my_saved))
    async def saved_handler(ev):
        raw = ev.raw_text or ""
        low = raw.strip().lower()

        if low.startswith(".addgroup"):
            parts = raw.split(maxsplit=1)
            if len(parts) > 1:
                items = [s for s in parts[1].replace(" ", "").split(",") if s]
                added, bad = [], []
                for it in items:
                    if it in targets_cfg: continue
                    e = await resolve_target(client, it)
                    if e: targets.append(e); targets_cfg.append(it); added.append(it)
                    else: bad.append(it)
                cfg["targets"] = targets_cfg; save_cfg(cfg_path, cfg)
                msg = []
                if added: msg.append(f"✅ added: {', '.join(added)}")
                if bad:   msg.append(f"⚠️ failed: {', '.join(bad)}")
                await ev.reply(" | ".join(msg) if msg else "(no changes)")
            else:
                await ev.reply("Usage: .addgroup @g1,@g2,-100..., t.me/+invite")
            return

        if low.startswith(".delgroup"):
            parts = raw.split(maxsplit=1)
            if len(parts) > 1:
                items = [s for s in parts[1].replace(" ", "").split(",") if s]
                keep = [s for s in targets_cfg if s not in items]
                if len(keep) != len(targets_cfg):
                    targets_cfg[:] = keep
                    # rebuild entities
                    new_entities = []
                    for s in targets_cfg:
                        e = await resolve_target(client, s)
                        if e: new_entities.append(e)
                    targets[:] = new_entities
                    cfg["targets"] = targets_cfg; save_cfg(cfg_path, cfg)
                    await ev.reply(f"🧹 removed: {', '.join(items)}")
                else:
                    await ev.reply("(no changes)")
            else:
                await ev.reply("Usage: .delgroup @g1,-100...")
            return

        if low.startswith(".listgroups"):
            if targets_cfg:
                body = "\n".join(f"{i+1}. {s}" for i, s in enumerate(targets_cfg))
                await ev.reply("📜 groups:\n" + body)
            else:
                await ev.reply("📜 groups: (empty)")
            return

        if low.startswith(".time"):
            parts = raw.split(maxsplit=1)
            secs = parse_interval(parts[1]) if len(parts) > 1 else 0
            interval_s["v"] = secs
            cfg["send_interval_seconds"] = secs; save_cfg(cfg_path, cfg)
            await ev.reply(f"⏱ per-message interval = {secs}s")
            return

        if low.startswith(".gap"):
            parts = raw.split(maxsplit=1)
            secs = parse_interval(parts[1]) if len(parts) > 1 else 0
            forward_gap_s["v"] = secs
            cfg["forward_gap_seconds"] = secs; save_cfg(cfg_path, cfg)
            await ev.reply(f"↔️ gap between forwards = {secs}s")
            return

        if low.startswith(".rotate"):
            parts = raw.split(maxsplit=1)
            mode = (parts[1].strip().lower() if len(parts)>1 else "on")
            if mode in ("on","off"):
                rotation_mode["v"] = "roundrobin" if mode == "on" else "broadcast"
                cfg["rotation_mode"] = rotation_mode["v"]; save_cfg(cfg_path, cfg)
                await ev.reply(f"🔁 rotation = {mode} ({rotation_mode['v']})")
            else:
                await ev.reply("Usage: .rotate on | .rotate off")
            return

        if low.startswith(".autonight"):
            parts = raw.split(maxsplit=1)
            onoff = (parts[1].strip().lower() if len(parts)>1 else "on")
            if onoff in ("on","off"):
                autonight["v"] = (onoff == "on")
                cfg["autonight"] = autonight["v"]; save_cfg(cfg_path, cfg)
                s = quiet["s"] or DEFAULT_AUTONIGHT[0]
                e = quiet["e"] or DEFAULT_AUTONIGHT[1]
                await ev.reply(f"🌙 autonight = {autonight['v']} (quiet {s}–{e})")
            else:
                await ev.reply("Usage: .autonight on | .autonight off")
            return

        if low.startswith(".quiet"):
            parts = raw.split(maxsplit=1)
            if len(parts)==1 or parts[1].strip().lower()=="off":
                quiet["s"]=quiet["e"]=None
                cfg["quiet_start"]=cfg["quiet_end"]=None; save_cfg(cfg_path, cfg)
                await ev.reply("🔕 quiet hours disabled")
            else:
                rng = parts[1].replace(" ", "")
                if "-" in rng:
                    s,e = rng.split("-",1)
                    quiet["s"], quiet["e"] = s, e
                    cfg["quiet_start"], cfg["quiet_end"] = s, e; save_cfg(cfg_path, cfg)
                    await ev.reply(f"🔕 quiet hours set {s}-{e}")
                else:
                    await ev.reply("Format: .quiet 23:00-07:00  or  .quiet off")
            return

        if low.startswith(".status"):
            s = quiet["s"] or DEFAULT_AUTONIGHT[0]
            e = quiet["e"] or DEFAULT_AUTONIGHT[1]
            await ev.reply(
                "🟢 status:\n"
                f"• per-message interval: {interval_s['v']}s\n"
                f"• gap between forwards: {forward_gap_s['v']}s\n"
                f"• rotation: {rotation_mode['v']}\n"
                f"• autonight: {autonight['v']} (quiet {s}–{e})\n"
                f"• groups: {', '.join(targets_cfg) if targets_cfg else '—'}"
            )
            return

        if low.startswith(".help"):
            await ev.reply(
                "Commands:\n"
                "• .addgroup @g1,@g2,-100..., t.me/+invite\n"
                "• .delgroup @g1,-100...\n"
                "• .listgroups\n"
                "• .time 30m              (sleep after each message)\n"
                "• .gap 5s                (delay between each group forward)\n"
                "• .rotate on|off         (round-robin single group vs broadcast)\n"
                "• .quiet 23:00-07:00 | .quiet off\n"
                "• .autonight on|off      (use .quiet if set, else 23:00–07:00)\n"
                "• .status"
            )
            return

        # normal flow: queue any other Saved message
        await q.put(ev.message)

    print(f"[{phone}] listening to Saved Messages. Drop anything there to forward.")
    await client.run_until_disconnected()

# ---------- run all users ----------
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
