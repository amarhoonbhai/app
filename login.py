# login.py — compact manager for your forwarder users
import json, os, sys, subprocess, signal, re
from getpass import getpass
from pathlib import Path

CONFIG_DIR = Path("users"); CONFIG_DIR.mkdir(exist_ok=True)
RUNNER_PID = Path("runner.pid")

TEMPLATE = {
    "phone": "",
    "api_id": 0,
    "api_hash": "",
    "targets": [],                  # ["@group1", "@channel2", "-100..."]
    "send_interval_seconds": 30,    # .time (sleep after each message)
    "forward_gap_seconds": 2,       # .gap (delay between each group forward)
    "quiet_start": None,            # "23:00"
    "quiet_end": None,              # "07:00"
    "autonight": False,             # .autonight on|off
    "rotation_mode": "broadcast",   # "broadcast" or "roundrobin" (.rotate)
    "rot_index": 0                  # internal pointer for round robin
}

# ---------- runner control ----------
def _alive_pid(pid: int) -> bool:
    try:
        os.kill(pid, 0); return True
    except Exception:
        return False

def ensure_runner():
    if RUNNER_PID.exists():
        try:
            pid = int(RUNNER_PID.read_text().strip())
            if _alive_pid(pid):
                print(f"Runner already running (pid {pid}).")
                return
        except Exception:
            pass
    print("Starting runner in background…")
    subprocess.Popen([sys.executable, "runner.py"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

def restart_runner():
    if RUNNER_PID.exists():
        try:
            pid = int(RUNNER_PID.read_text().strip())
            if _alive_pid(pid):
                os.kill(pid, signal.SIGTERM)
                print(f"Killed runner pid {pid}.")
        except Exception as e:
            print(f"(warn) couldn’t stop previous runner: {e}")
    ensure_runner()

# ---------- helpers ----------
def banner():
    print("==== Telegram Forwarder CLI ====")
    print("  [1] Add/Update user (wizard)")
    print("  [2] Quick add/remove groups")
    print("  [3] Quick timings (.time/.gap/.quiet/.autonight/.rotate)")
    print("  [4] List users & view config")
    print("  [5] Delete user")
    print("  [6] Restart runner")
    print("  [7] Exit")

def list_user_files():
    return sorted(CONFIG_DIR.glob("*.json"))

def pick_user(prompt_text="Select user by number", allow_empty=False):
    files = list_user_files()
    if not files:
        print("(no users yet)"); return None
    for i, p in enumerate(files, 1):
        print(f"  {i}. {p.stem}")
    sel = input(f"{prompt_text}: ").strip()
    if not sel and allow_empty:
        return None
    if not sel.isdigit() or not (1 <= int(sel) <= len(files)):
        print("Invalid choice."); return None
    return files[int(sel) - 1]

def load_cfg(path: Path):
    with open(path, "r", encoding="utf-8") as f: return json.load(f)

def save_cfg(path: Path, cfg: dict):
    with open(path, "w", encoding="utf-8") as f: json.dump(cfg, f, indent=2, ensure_ascii=False)

def prompt_int(label: str, default: int | None = None) -> int:
    while True:
        s = input(f"{label}{f' [{default}]' if default is not None else ''}: ").strip()
        if not s and default is not None: return default
        if s.isdigit(): return int(s)
        print("Please enter a number.")

def yesno(label: str, default: bool = False) -> bool:
    d = "y" if default else "n"
    s = input(f"{label} (y/n) [{d}]: ").strip().lower()
    return (s == "" and default) or s in ("y", "yes", "1", "true", "on")

def parse_interval(text: str) -> int:
    # Accepts 45s, 5m, 1h30m, 2d, or plain seconds
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

def parse_quiet_window(s: str) -> tuple[str | None, str | None]:
    s = s.strip()
    if not s or s.lower() == "off": return None, None
    if not re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", s):
        print("Format must be HH:MM-HH:MM (e.g., 23:00-07:00)."); return None, None
    a, b = s.split("-", 1)
    try:
        h1, m1 = map(int, a.split(":")); h2, m2 = map(int, b.split(":"))
        if not (0<=h1<=23 and 0<=m1<=59 and 0<=h2<=23 and 0<=m2<=59): raise ValueError
    except Exception:
        print("Invalid hour/minute."); return None, None
    return a, b

# ---------- flows ----------
def flow_add_update():
    phone = input("Enter phone (+countrycode): ").strip()
    if not phone:
        print("Phone is required."); return
    cfg_path = CONFIG_DIR / f"{phone}.json"
    if cfg_path.exists():
        cfg = load_cfg(cfg_path)
        print(f"Editing existing user: {cfg_path.name}")
    else:
        cfg = TEMPLATE.copy(); cfg["phone"] = phone

    # API creds
    cur_id = int(cfg.get("api_id", 0)); cur_hash = cfg.get("api_hash", "")
    _id = input(f"API_ID [{cur_id or ''}]: ").strip()
    if _id:
        if _id.isdigit(): cfg["api_id"] = int(_id)
        else: print("API_ID must be a number; keeping previous.")
    elif not cur_id:
        cfg["api_id"] = prompt_int("API_ID")

    _hash = getpass(f"API_HASH (hidden) [{'set' if cur_hash else 'empty'}]: ").strip()
    if _hash: cfg["api_hash"] = _hash
    elif not cur_hash: cfg["api_hash"] = getpass("API_HASH (hidden): ").strip()

    # Targets
    exist_targets = ", ".join(cfg.get("targets", [])) or "none"
    t_in = input(f"Targets comma-separated (e.g. @g1,@g2,-100...) [{exist_targets}]: ").strip()
    if t_in:
        cfg["targets"] = [s for s in t_in.replace(" ", "").split(",") if s]

    # Timing defaults
    si_def = cfg.get("send_interval_seconds", TEMPLATE["send_interval_seconds"])
    si_in = input(f"Per-message delay .time (e.g., 45s, 2m, 1h30m) [{si_def}s]: ").strip()
    if si_in: cfg["send_interval_seconds"] = parse_interval(si_in)

    gap_def = cfg.get("forward_gap_seconds", TEMPLATE["forward_gap_seconds"])
    gap_in = input(f"Gap between group forwards .gap [{gap_def}s]: ").strip()
    if gap_in: cfg["forward_gap_seconds"] = parse_interval(gap_in)

    # Quiet & Autonight
    qstart, qend = cfg.get("quiet_start"), cfg.get("quiet_end")
    q_def = f"{qstart}-{qend}" if (qstart and qend) else "off"
    q_in = input(f"Quiet window .quiet (HH:MM-HH:MM or 'off') [{q_def}]: ").strip()
    if q_in:
        qs, qe = parse_quiet_window(q_in)
        if q_in.lower() == "off":
            cfg["quiet_start"] = None; cfg["quiet_end"] = None
        elif qs and qe:
            cfg["quiet_start"] = qs; cfg["quiet_end"] = qe

    auto_def = bool(cfg.get("autonight", TEMPLATE["autonight"]))
    cfg["autonight"] = yesno("Autonight .autonight on? (uses .quiet if set else 23:00–07:00)", auto_def)

    # Rotation
    rot_def = cfg.get("rotation_mode", TEMPLATE["rotation_mode"])
    rot_in = input(f"Rotation .rotate (on=roundrobin / off=broadcast) [{'on' if rot_def=='roundrobin' else 'off'}]: ").strip().lower()
    if rot_in in ("on","off"): cfg["rotation_mode"] = "roundrobin" if rot_in=="on" else "broadcast"

    save_cfg(cfg_path, cfg)
    print(f"✔ Saved: {cfg_path}")
    if yesno("Restart runner now to apply?", True): restart_runner()
    else: ensure_runner()

def flow_quick_groups():
    cfg_file = pick_user("Select user for group changes")
    if not cfg_file: return
    cfg = load_cfg(cfg_file)
    print(f"Current groups: {', '.join(cfg.get('targets', [])) or '(none)'}")
    action = input("Type 'add' to add groups or 'del' to remove: ").strip().lower()
    if action not in ("add","del"):
        print("Type add or del."); return
    items = input("Comma list (e.g., @g1,@g2,-100...): ").strip().replace(" ", "")
    if not items:
        print("Nothing entered."); return
    parts = [s for s in items.split(",") if s]
    if action == "add":
        added = [p for p in parts if p not in cfg.get("targets", [])]
        cfg["targets"] = list(cfg.get("targets", [])) + added
        print("Added:", ", ".join(added) if added else "(none)")
    else:
        before = set(cfg.get("targets", []))
        cfg["targets"] = [s for s in cfg.get("targets", []) if s not in parts]
        removed = before - set(cfg["targets"])
        print("Removed:", ", ".join(removed) if removed else "(none)")
    save_cfg(cfg_file, cfg)
    if yesno("Restart runner now to apply?", True): restart_runner()

def flow_quick_timings():
    cfg_file = pick_user("Select user for timing changes")
    if not cfg_file: return
    cfg = load_cfg(cfg_file)

    si_def = cfg.get("send_interval_seconds", TEMPLATE["send_interval_seconds"])
    si_in = input(f".time per-message delay (e.g., 45s, 2m) [{si_def}s]: ").strip()
    if si_in: cfg["send_interval_seconds"] = parse_interval(si_in)

    gap_def = cfg.get("forward_gap_seconds", TEMPLATE["forward_gap_seconds"])
    gap_in = input(f".gap between group forwards (e.g., 5s) [{gap_def}s]: ").strip()
    if gap_in: cfg["forward_gap_seconds"] = parse_interval(gap_in)

    qstart, qend = cfg.get("quiet_start"), cfg.get("quiet_end")
    q_def = f"{qstart}-{qend}" if (qstart and qend) else "off"
    q_in = input(f".quiet window (HH:MM-HH:MM or 'off') [{q_def}]: ").strip()
    if q_in:
        qs, qe = parse_quiet_window(q_in)
        if q_in.lower() == "off":
            cfg["quiet_start"] = None; cfg["quiet_end"] = None
        elif qs and qe:
            cfg["quiet_start"] = qs; cfg["quiet_end"] = qe

    auto_def = bool(cfg.get("autonight", TEMPLATE["autonight"]))
    cfg["autonight"] = yesno(".autonight on? (uses .quiet if set else 23:00–07:00)", auto_def)

    rot_def = cfg.get("rotation_mode", TEMPLATE["rotation_mode"])
    rot_in = input(f".rotate (on=roundrobin / off=broadcast) [{'on' if rot_def=='roundrobin' else 'off'}]: ").strip().lower()
    if rot_in in ("on","off"): cfg["rotation_mode"] = "roundrobin" if rot_in=="on" else "broadcast"

    save_cfg(cfg_file, cfg)
    if yesno("Restart runner now to apply?", True): restart_runner()

def flow_list_view():
    files = list_user_files()
    if not files:
        print("(no users)")
        return
    for i, p in enumerate(files, 1):
        cfg = load_cfg(p)
        print(f"\n[{i}] {p.stem}")
        print(f"    api_id: {cfg.get('api_id')}")
        print(f"    targets: {', '.join(cfg.get('targets', [])) or '(none)'}")
        print(f"    .time: {cfg.get('send_interval_seconds', 0)}s")
        print(f"    .gap: {cfg.get('forward_gap_seconds', 0)}s")
        qs, qe = cfg.get("quiet_start"), cfg.get("quiet_end")
        print(f"    .quiet: {f'{qs}-{qe}' if (qs and qe) else 'off'}")
        print(f"    .autonight: {bool(cfg.get('autonight', False))}")
        print(f"    .rotate: {'on' if cfg.get('rotation_mode')=='roundrobin' else 'off'}")

def flow_delete():
    cfg_file = pick_user("Select user to delete")
    if not cfg_file: return
    os.remove(cfg_file)
    print("Deleted:", cfg_file.name)

# ---------- main ----------
def main():
    while True:
        banner()
        choice = input("➻ Choose option: ").strip()
        if choice == "1": flow_add_update()
        elif choice == "2": flow_quick_groups()
        elif choice == "3": flow_quick_timings()
        elif choice == "4": flow_list_view()
        elif choice == "5": flow_delete()
        elif choice == "6": restart_runner()
        elif choice == "7": sys.exit(0)
        else: print("Invalid choice.")

if __name__ == "__main__":
    main()
