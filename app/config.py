"""
Configuration loader for the Telegram forwarder.

Reads settings from environment variables with safe defaults.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List


def _int_env(name: str, default: int) -> int:
    v = os.getenv(name)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    sessions_dir: Path
    users_dir: Path
    log_level: str
    admin_user_ids: List[int]

    # Auto-join pacing
    join_min_delay_sec: int = 45
    join_max_delay_sec: int = 90
    joins_per_hour_limit: int = 15


def load_config() -> Config:
    return Config(
        api_id=int(os.environ["API_ID"]),
        api_hash=os.environ["API_HASH"],
        sessions_dir=Path(os.getenv("SESSIONS_DIR", "sessions")),
        users_dir=Path(os.getenv("USERS_DIR", "users")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        admin_user_ids=[int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()],
        join_min_delay_sec=_int_env("JOIN_MIN_DELAY_SEC", 45),
        join_max_delay_sec=_int_env("JOIN_MAX_DELAY_SEC", 90),
        joins_per_hour_limit=_int_env("JOINS_PER_HOUR_LIMIT", 15),
    )
  
