# login.py — minimal manager: Setup/Login user, Delete, Restart, Exit
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
    "rot_index": 0
}

# ---------- runner control ----------
def _alive(pid: int) -> bool:
    try: os.kill(pid, 0); return True
    except Exception: return False

def ensure_runner():
    if RUNNER_PID.exists():
        try:
            pid = int(RUNNER_PID.read_text().strip())
            if _alive(pid):
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
            if _alive(pid):
                os.kill(pid, signal.SIGTERM)
                print(f"Killed runner pid {pid}.")
        except Exception as e:
            print(f"(warn) couldn’t stop previous runner: {e}")
    ensure_runner()

# ---------- helpers ----------
def banner():
    print("\n==== Forwarder Manager ====")
    print("  1) Setup/Login user")
    print("  2) Delete user")
    print("  3) Restart runner")
    print("  4) Exit")

def load_cfg(path: Path):
    with open(path, "r", encoding="utf-8") as f: return json.load(f)

def save_cfg(path: Path, cfg: dict):
    with open(path, "w", encoding="utf-8") as f: json.dump(cfg, f, indent=2, ensure_ascii=False)

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

def parse_quiet_window(s: str) -> tuple[str|None,str|None]:
    s = s.strip()
    if not s or s.lower() == "off": return None, None
    if not re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", s):
        print("Format HH:MM-HH:MM (e.g., 23:00-07:00)."); return None, None
    a, b = s.split("-", 1)
    try:
        h1,m1 = map(int, a.split(":")); h2,m2 = map(int, b.split(":"))
        if not (0<=h1<=23 and 0<=m1<=59 and 0<=h2<=23 and 0<=m2<=59): raise ValueError
    except Exception:
        print("Invalid hour/minute."); return None, None
    return a, b

# ---------- flows ----------
def flow_setup():
    # Create or update one user, collecting everything needed for runner
    phone = input("Phone (+countrycode): ").strip()
    if not phone:
        print("Phone required."); return
    p = CONFIG_DIR / f"{phone}.json"
    if p.exists():
        cfg = load_cfg(p)
        print(f"Editing existing: {p.name}")
    else:
        cfg = TEMPLATE.copy(); cfg["phone"] = phone

    # API
    cur_id = int(cfg.get("api_id", 0))
    s = input(f"API_ID [{cur_id or ''}]: ").strip()
    if s:
        if s.isdigit(): cfg["api_id"] = int(s)
        else: print("API_ID must be a number; keeping previous.")
    elif not cur_id:
        while True:
            s = input("API_ID: ").strip()
            if s.isdigit(): cfg["api_id"] = int(s); break
            print("Enter number.")

    cur_hash = cfg.get("api_hash", "")
    s = getpass(f"API_HASH (hidden) [{'set' if cur_hash else 'empty'}]: ").strip()
    if s: cfg["api_hash"] = s
    elif not cur_hash:
        cfg["api_hash"] = getpass("API_HASH (hidden): ").strip()

    # Targets quick set (press Enter to keep)
    cur_targets = ", ".join(cfg.get("targets", [])) or "none"
    s = input(f"Targets @g1,@g2,-100... [{cur_targets}]: ").strip()
    if s:
        cfg["targets"] = [x for x in s.replace(" ", "").split(",") if x]

    # Timings
    si = cfg.get("send_interval_seconds", TEMPLATE["send_interval_seconds"])
    s = input(f".time per-message delay (e.g., 45s, 2m, 1h30m) [{si}s]: ").strip()
    if s: cfg["send_interval_seconds"] = parse_interval(s)

    gap = cfg.get("forward_gap_seconds", TEMPLATE["forward_gap_seconds"])
    s = input(f".gap between forwards (e.g., 5s) [{gap}s]: ").strip()
    if s: cfg["forward_gap_seconds"] = parse_interval(s)

    # Night & rotation
    qs, qe = cfg.get("quiet_start"), cfg.get("quiet_end")
    cur_quiet = f"{qs}-{qe}" if (qs and qe) else "off"
    s = input(f".quiet window (HH:MM-HH:MM or 'off') [{cur_quiet}]: ").strip()
    if s:
        q1, q2 = parse_quiet_window(s)
        if s.lower() == "off":
            cfg["quiet_start"] = None; cfg["quiet_end"] = None
        elif q1 and q2:
            cfg["quiet_start"], cfg["quiet_end"] = q1, q2

    auto = bool(cfg.get("autonight", False))
    s = input(f".autonight (on/off) [{'on' if auto else 'off'}]: ").strip().lower()
    if s in ("on","off"): cfg["autonight"] = (s == "on")

    rot = cfg.get("rotation_mode", "broadcast")
    s = input(f".rotate (on=roundrobin / off=broadcast) [{'on' if rot=='roundrobin' else 'off'}]: ").strip().lower()
    if s in ("on","off"): cfg["rotation_mode"] = "roundrobin" if s=="on" else "broadcast"

    save_cfg(p, cfg)
    print(f"✔ Saved: {p.name}")
    # keep the runner alive or start it if not running
    ensure_runner()

def flow_delete():
    phone = input("Phone to delete (+countrycode): ").strip()
    if not phone:
        print("Phone required."); return
    f = CONFIG_DIR / f"{phone}.json"
    if f.exists():
        os.remove(f)
        print("Deleted:", f.name)
    else:
        print("No such user:", f.name)

# ---------- main ----------
def main():
    ensure_runner()  # keep background runner alive
    while True:
        banner()
        choice = input("➤ Choose: ").strip()
        if choice == "1": flow_setup()
        elif choice == "2": flow_delete()
        elif choice == "3": restart_runner()
        elif choice == "4": sys.exit(0)
        else: print("Invalid choice.")

if __name__ == "__main__":
    main()

