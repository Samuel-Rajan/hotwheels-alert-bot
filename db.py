import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "inventory.db"

# An item must be missing from this many consecutive successful scans
# before it is considered "out of stock". Protects against flaky scrapes
# causing fake restock alerts.
MISS_THRESHOLD = 2


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                key        TEXT PRIMARY KEY,
                platform   TEXT NOT NULL,
                title      TEXT,
                link       TEXT,
                status     TEXT NOT NULL DEFAULT 'in',   -- 'in' or 'out'
                miss_count INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT,
                last_seen  TEXT
            )
            """
        )


def is_empty() -> bool:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 0


def check_item(key: str, platform: str, title: str, link: str) -> bool:
    """
    Call for every item currently seen IN STOCK.
    Returns True when an alert should be sent:
      - brand new item (never seen before), or
      - item previously marked 'out' that is now back.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    with _conn() as c:
        row = c.execute("SELECT status FROM inventory WHERE key = ?", (key,)).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO inventory (key, platform, title, link, status, miss_count, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, 'in', 0, ?, ?)",
                (key, platform, title, link, now, now),
            )
            return True

        was_out = row[0] == "out"
        c.execute(
            "UPDATE inventory SET status='in', miss_count=0, title=?, link=?, last_seen=? WHERE key=?",
            (title, link, now, key),
        )
        return was_out


def mark_missing(platform: str, seen_keys: set) -> int:
    """
    Call once per platform AFTER a successful scan, with the keys seen in stock.
    Items not seen get their miss_count bumped and flip to 'out' at the threshold.
    """
    flipped = 0
    with _conn() as c:
        rows = c.execute(
            "SELECT key, miss_count FROM inventory WHERE platform = ? AND status = 'in'",
            (platform,),
        ).fetchall()
        for key, miss in rows:
            if key in seen_keys:
                continue
            miss += 1
            if miss >= MISS_THRESHOLD:
                c.execute("UPDATE inventory SET status='out', miss_count=? WHERE key=?", (miss, key))
                flipped += 1
            else:
                c.execute("UPDATE inventory SET miss_count=? WHERE key=?", (miss, key))
    return flipped
