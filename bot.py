import sys
from pathlib import Path
from datetime import timedelta, date

from app.config import load_config
from app.storage import Storage, UserConfig


def extend_all(users_dir: Path, days: int):
    for file in users_dir.glob("*.json"):
        storage = Storage(file)
        try:
            cfg = storage.load()
            cfg.extend_days(days)
            storage.save(cfg)
            print(f"Extended {cfg.phone} to {cfg.plan_expiry}")
        except Exception as e:
            print(f"Failed to extend {file}: {e}")


def extend_one(users_dir: Path, phone: str, days: int):
    file = users_dir / f"{phone}.json"
    if not file.exists():
        print(f"No such user: {phone}", file=sys.stderr)
        sys.exit(1)

    storage = Storage(file)
    cfg = storage.load()
    cfg.extend_days(days)
    storage.save(cfg)
    print(f"Extended {cfg.phone} to {cfg.plan_expiry}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    cfg = load_config()
    users_dir: Path = cfg.users_dir

    cmd = sys.argv[1].lower()
    if cmd == "all":
        days = int(sys.argv[2])
        extend_all(users_dir, days)
    elif cmd == "one":
        if len(sys.argv) < 4:
            print("Usage: python bot.py one <phone> <days>", file=sys.stderr)
            sys.exit(1)
        phone = sys.argv[2]
        days = int(sys.argv[3])
        extend_one(users_dir, phone, days)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
  
