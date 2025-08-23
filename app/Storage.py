"""
Storage helpers for user configuration and join queue.

- UserConfig: stores plan expiry, cycle, and group IDs
- Storage: JSON wrapper for loading/saving UserConfig
- JoinQueue: persistent queue for safe joining of groups
"""

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent)) as tf:
        json.dump(data, tf, indent=2, ensure_ascii=False)
        tmpname = tf.name
    shutil.move(tmpname, path)


@dataclass
class UserConfig:
    phone: str
    plan_expiry: str  # YYYY-MM-DD
    cycle_minutes: int = 10
    groups: Set[int] = field(default_factory=set)

    def extend_days(self, days: int) -> None:
        d = date.fromisoformat(self.plan_expiry)
        self.plan_expiry = (d + timedelta(days=days)).isoformat()

    def is_expired(self) -> bool:
        return date.fromisoformat(self.plan_expiry) < date.today()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phone": self.phone,
            "plan_expiry": self.plan_expiry,
            "cycle_minutes": self.cycle_minutes,
            "groups": sorted(self.groups),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserConfig":
        return cls(
            phone=data["phone"],
            plan_expiry=data["plan_expiry"],
            cycle_minutes=int(data.get("cycle_minutes", 10)),
            groups=set(map(int, data.get("groups", []))),
        )


class Storage:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> UserConfig:
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        with self.path.open("r") as f:
            return UserConfig.from_dict(json.load(f))

    def save(self, cfg: UserConfig) -> None:
        _atomic_write_json(self.path, cfg.to_dict())

    def add_groups(self, chat_ids: List[int]) -> int:
        cfg = self.load()
        before = len(cfg.groups)
        cfg.groups.update(map(int, chat_ids))
        self.save(cfg)
        return len(cfg.groups) - before

    def remove_group(self, chat_id: int) -> bool:
        cfg = self.load()
        existed = chat_id in cfg.groups
        if existed:
            cfg.groups.remove(chat_id)
            self.save(cfg)
        return existed

    def list_groups(self) -> List[int]:
        return sorted(self.load().groups)


class JoinQueue:
    """
    Persistent FIFO queue of join targets.
    Each item is a dict: {"kind": "invite"|"username"|"entity_id", "value": "<hash|username|id>"}
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            _atomic_write_json(self.path, {"items": []})

    def _load(self) -> Dict[str, Any]:
        with self.path.open("r") as f:
            return json.load(f)

    def _save(self, data: Dict[str, Any]) -> None:
        _atomic_write_json(self.path, data)

    def enqueue_many(self, items: List[Dict[str, str]]) -> int:
        data = self._load()
        existing = {(it["kind"], it["value"]) for it in data.get("items", [])}
        added = 0
        for it in items:
            key = (it["kind"], it["value"])
            if key not in existing:
                data["items"].append(it)
                existing.add(key)
                added += 1
        self._save(data)
        return added

    def enqueue(self, kind: str, value: str) -> bool:
        return self.enqueue_many([{"kind": kind, "value": value}]) > 0

    def dequeue(self) -> Optional[Dict[str, str]]:
        data = self._load()
        items = data.get("items", [])
        if not items:
            return None
        it = items.pop(0)
        data["items"] = items
        self._save(data)
        return it

    def size(self) -> int:
        return len(self._load().get("items", []))
      
