#!/usr/bin/env python3
"""Оновлює schedule.json з публічного Telegram-каналу АТ «Черкасиобленерго».

Читає веб-версію каналу (https://t.me/s/pat_cherkasyoblenergo), знаходить пости
з графіками погодинних відключень (ГПВ) і записує їх у schedule.json.
Лише стандартна бібліотека Python, без залежностей.

Запуск:  python3 scripts/update_schedule.py            (з інтернету)
         python3 scripts/update_schedule.py --html f.html  (з локального файлу, для тестів)
Код виходу 0 завжди, якщо не сталося несподіваної помилки; у stdout — що змінилося.
"""
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHANNEL = "pat_cherkasyoblenergo"
URL = f"https://t.me/s/{CHANNEL}"
ROOT = Path(__file__).resolve().parent.parent
SCHEDULE = ROOT / "schedule.json"
QUEUES = [f"{a}.{b}" for a in range(1, 7) for b in (1, 2)]
KEEP_DAYS = 5
MONTHS = {"січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
          "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12}
SLOT = r"\d{1,2}:\d{2}\s*[-–—]\s*\d{1,2}:\d{2}"
LINE_RE = re.compile(r"^\s*([1-6])\.([12])\s*[:\-–—]?\s*(.*?)\s*[;.]?\s*$")
SLOTS_RE = re.compile(rf"^{SLOT}(?:\s*,\s*{SLOT})*$")
DATE_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")", re.I)
NO_OUTAGES_RE = re.compile(r"^(без відключень|не відключа\w*|-|—)?$", re.I)
GAV_RE = re.compile(r"\bГАВ\b|аварійн\w*\s+(графік\w*|відключен\w*)", re.I)
GAV_END_RE = re.compile(r"(скасов\w*|припин\w*|завершен\w*|відмін\w*|більше не застосов\w*|не застосовуют\w*)", re.I)
TIME_FROM_RE = re.compile(r"\bз\s+(\d{1,2}:\d{2})")


def kyiv_now():
    """Поточний час у Києві (UTC+2 взимку, UTC+3 влітку) без зовнішніх бібліотек."""
    return to_kyiv(datetime.now(timezone.utc))


def to_kyiv(dt):
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Kyiv"))
    except Exception:  # дуже старий Python або немає tzdata
        now = dt.astimezone(timezone.utc)
        y = now.year
        last_sun = lambda m: max(d for d in range(25, 32) if datetime(y, m, d).weekday() == 6)
        start = datetime(y, 3, last_sun(3), 1, tzinfo=timezone.utc)
        end = datetime(y, 10, last_sun(10), 1, tzinfo=timezone.utc)
        return now + timedelta(hours=3 if start <= now < end else 2)


def fetch_html():
    req = urllib.request.Request(URL, headers={
        "User-Agent": "Mozilla/5.0 (svitlo-cherkasy schedule bot; +https://github.com)",
        "Accept-Language": "uk,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def html_to_text(fragment):
    t = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).replace("\xa0", " ")


def parse_posts(page):
    """Повертає список постів: {post, at, text}."""
    posts = []
    for m in re.finditer(r'data-post="' + CHANNEL + r'/(\d+)"', page):
        start = m.start()
        nxt = page.find('data-post="' + CHANNEL + "/", m.end())
        block = page[start: nxt if nxt != -1 else len(page)]
        tm = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not tm:
            continue
        at = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        posts.append({"post": int(m.group(1)), "at": at.group(1) if at else None,
                      "text": html_to_text(tm.group(1))})
    return posts


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def valid_slots(s):
    if not SLOTS_RE.match(s):
        return False
    for part in s.split(","):
        a, b = [p.strip() for p in re.split(r"[-–—]", part)]
        ma, mb = minutes(a), minutes(b)
        if not (0 <= ma < 1440 and 0 < mb <= 1440 and mb > ma and int(a.split(":")[1]) < 60 and int(b.split(":")[1]) < 60):
            return False
    return True


def norm_slots(s):
    parts = [re.sub(r"\s*[-–—]\s*", " - ", p.strip()) for p in s.split(",")]
    return ", ".join(re.sub(r"(?<!\d)(\d):", r"0\1:", p) for p in parts)


def to_date(day, month_word, now):
    month = MONTHS[month_word.lower()]
    year = now.year
    if now.month == 12 and month == 1:
        year += 1
    elif now.month == 1 and month == 12:
        year -= 1
    return f"{year:04d}-{month:02d}-{int(day):02d}"


def extract_schedule(post, now):
    """Повертає (date, text) або None, якщо пост не є повним графіком ГПВ."""
    text = post["text"]
    if "ГПВ" not in text and "погодинних відключень" not in text:
        return None
    dm = DATE_RE.search(text)
    if not dm:
        return None
    date = to_date(dm.group(1), dm.group(2), now)
    found = {}
    for line in text.split("\n"):
        lm = LINE_RE.match(line)
        if not lm:
            continue
        q = f"{lm.group(1)}.{lm.group(2)}"
        val = lm.group(3).strip()
        if NO_OUTAGES_RE.match(val):
            found[q] = "без відключень"
        elif valid_slots(val):
            found[q] = norm_slots(val)
        else:
            return None  # незрозумілий рядок — краще нічого не чіпати
    if len(found) == len(QUEUES) and all(q in found for q in QUEUES):
        return date, "\n".join(f"{q} {found[q]}" for q in QUEUES)
    # пост про скасування ГПВ на дату (без рядків черг)
    if not found and not GAV_RE.search(text) and re.search(r"(не застосовуват|не будуть застосован|скасован|відмін)", text, re.I):
        return date, "\n".join(f"{q} без відключень" for q in QUEUES)
    return None


def extract_gav(post, now):
    """Пост про аварійні відключення (ГАВ): повертає (date, active, from_time) або None."""
    text = post["text"]
    if not GAV_RE.search(text):
        return None
    dm = DATE_RE.search(text)
    if dm:
        date = to_date(dm.group(1), dm.group(2), now)
    elif post.get("at"):
        try:
            date = to_kyiv(datetime.fromisoformat(post["at"])).strftime("%Y-%m-%d")
        except ValueError:
            return None
    else:
        return None
    # речення про ГАВ: чи їх запроваджують, чи скасовують
    sent = " ".join(x for x in re.split(r"(?<=[.!?])\s+|\n", text) if GAV_RE.search(x))
    active = not GAV_END_RE.search(sent)
    tm = TIME_FROM_RE.search(sent)
    return date, active, (tm.group(1).zfill(5) if tm and active else None)


def to_utc_iso(at):
    if not at:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        return datetime.fromisoformat(at).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return at


def merge(store, posts, now):
    days = dict(store.get("days", {}))
    last = store.get("lastPost") or max([d.get("post") or 0 for d in days.values()] or [0])
    changes = []
    for p in sorted(posts, key=lambda x: x["post"]):
        res = extract_schedule(p, now)
        if not res:
            continue
        date, text = res
        at = to_utc_iso(p["at"])
        cur = days.get(date)
        if cur:
            if cur.get("manual"):
                if not at > (cur.get("at") or ""):
                    continue  # ручне виправлення новіше за пост
            elif (cur.get("post") or 0) > p["post"]:
                continue  # старіший пост не перекриває новіший
            if cur.get("text") == text and cur.get("post") == p["post"]:
                continue
        days[date] = {"text": text, "post": p["post"], "at": at}
        changes.append(f"{date} з поста {p['post']}")
        last = max(last, p["post"])
    gav = dict(store.get("gav", {}))
    for p in sorted(posts, key=lambda x: x["post"]):
        res = extract_gav(p, now)
        if not res:
            continue
        date, active, frm = res
        cur = gav.get(date)
        if cur and (cur.get("post") or 0) >= p["post"]:
            continue
        gav[date] = {"active": active, "from": frm, "post": p["post"], "at": to_utc_iso(p["at"])}
        changes.append(f"ГАВ {date} {'діють' if active else 'скасовано'} (пост {p['post']})")
        last = max(last, p["post"])
    keep = sorted(days)[-KEEP_DAYS:]
    out = {"days": {k: days[k] for k in keep}, "lastPost": last}
    gkeep = sorted(gav)[-KEEP_DAYS:]
    if gkeep:
        out["gav"] = {k: gav[k] for k in gkeep}
    return out, changes


def main(argv):
    now = kyiv_now()
    if "--html" in argv:
        page = Path(argv[argv.index("--html") + 1]).read_text(encoding="utf-8")
    else:
        page = fetch_html()
    posts = parse_posts(page)
    if not posts:
        print("Не знайдено жодного поста — сторінку каналу не вдалося прочитати, нічого не змінюю.")
        return 0
    store = json.loads(SCHEDULE.read_text(encoding="utf-8")) if SCHEDULE.exists() else {"days": {}}
    new, changes = merge(store, posts, now)
    if new["days"] == store.get("days") and new.get("gav") == store.get("gav"):
        print(f"Нових графіків немає (переглянуто постів: {len(posts)}).")
        return 0
    new["checked"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    SCHEDULE.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("Оновлено: " + "; ".join(changes))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
