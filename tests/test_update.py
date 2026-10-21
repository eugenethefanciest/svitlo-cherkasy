"""Перевірка скрипта на реальних постах каналу (21 жовтня 2026). Запуск: python3 tests/test_update.py"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import update_schedule as u  # noqa: E402

HTML = Path(__file__).with_name("fixture.html").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)


def test_parse():
    posts = u.parse_posts(HTML)
    assert [p["post"] for p in posts] == [1877, 1878, 1880], posts
    d, t = u.extract_schedule(posts[0], NOW)
    assert d == "2026-10-21"
    assert t.split("\n")[2] == "2.1 12:30 - 14:00, 16:00 - 18:00, 22:00 - 24:00"
    assert u.extract_schedule(posts[1], NOW) is None  # ГОП, не ГПВ
    d, t = u.extract_schedule(posts[2], NOW)
    assert d == "2026-10-22" and t.split("\n")[0] == "1.1 06:00 - 08:00, 12:00 - 14:00, 18:00 - 20:00"
    assert len(t.split("\n")) == 12


def test_merge():
    posts = u.parse_posts(HTML)
    new, ch = u.merge({"days": {}}, posts, NOW)
    assert sorted(new["days"]) == ["2026-10-21", "2026-10-22"] and new["lastPost"] == 1880, new
    again, ch2 = u.merge(new, posts, NOW)
    assert again["days"] == new["days"] and ch2 == []  # нічого нового — без змін
    manual = {"days": {"2026-10-22": {"text": "x", "post": None, "at": "2026-10-21T18:00:00Z", "manual": True}}}
    kept, _ = u.merge(manual, posts, NOW)
    assert kept["days"]["2026-10-22"]["text"] == "x"  # ручне виправлення пізніше за пост — лишається


def test_rejects_garbage():
    bad = {"post": 9999, "at": None, "text": "ГПВ на 23 жовтня\n1.1 10:00 - 12:00\n1.2 зранку трохи"}
    assert u.extract_schedule(bad, NOW) is None
    partial = {"post": 9998, "at": None, "text": "ГПВ на 23 жовтня\n1.1 10:00 - 12:00"}
    assert u.extract_schedule(partial, NOW) is None


def test_year_boundary():
    p = {"post": 1, "at": None, "text": "ГПВ на 1 січня\n" + "\n".join(f"{q} 10:00 - 12:00" for q in u.QUEUES)}
    d, _ = u.extract_schedule(p, datetime(2026, 12, 31, 20, 0, tzinfo=timezone.utc))
    assert d == "2027-01-01"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
