import json
import tempfile
from pathlib import Path

from app.storage import Storage, UserConfig, JoinQueue


def test_userconfig_roundtrip_and_extend():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "user.json"
        cfg = UserConfig(
            phone="+15550000000",
            plan_expiry="2025-01-01",
            cycle_minutes=10,
            groups=set(),
        )
        st = Storage(p)
        st.save(cfg)

        # load back
        loaded = st.load()
        assert loaded.phone == cfg.phone
        assert loaded.plan_expiry == "2025-01-01"
        assert loaded.cycle_minutes == 10
        assert list(loaded.groups) == []

        # extend
        loaded.extend_days(30)
        st.save(loaded)
        again = st.load()
        assert again.plan_expiry == "2025-01-31"


def test_groups_add_remove_list():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "user.json"
        st = Storage(p)
        st.save(UserConfig(phone="+1", plan_expiry="2025-01-01", groups=set()))

        added = st.add_groups([123, 456, 123])  # duplicate 123
        assert added == 2
        assert st.list_groups() == [123, 456]

        # removing existing
        assert st.remove_group(123) is True
        assert st.list_groups() == [456]

        # removing non-existing
        assert st.remove_group(999) is False
        assert st.list_groups() == [456]


def test_joinqueue_persistence_and_fifo():
    with tempfile.TemporaryDirectory() as td:
        qp = Path(td) / "queue.json"
        q = JoinQueue(qp)

        # Initially empty
        assert q.size() == 0
        assert q.dequeue() is None

        # Enqueue many (with duplicate)
        items = [
            {"kind": "invite", "value": "abc123"},
            {"kind": "username", "value": "publicChannel"},
            {"kind": "entity_id", "value": "123456"},
            {"kind": "invite", "value": "abc123"},  # duplicate
        ]
        added = q.enqueue_many(items)
        assert added == 3
        assert q.size() == 3

        # FIFO order
        it1 = q.dequeue()
        it2 = q.dequeue()
        it3 = q.dequeue()
        it4 = q.dequeue()  # now empty

        assert it1 == {"kind": "invite", "value": "abc123"}
        assert it2 == {"kind": "username", "value": "publicChannel"}
        assert it3 == {"kind": "entity_id", "value": "123456"}
        assert it4 is None

        # Persistence check: re-open and ensure size is kept
        q2 = JoinQueue(qp)
        assert q2.size() == 0

        # Single enqueue() helper
        assert q2.enqueue("invite", "zzz999") is True
        assert q2.enqueue("invite", "zzz999") is False  # duplicate rejected
        assert q2.size() == 1
        assert q2.dequeue() == {"kind": "invite", "value": "zzz999"}
        assert q2.size() == 0
      
