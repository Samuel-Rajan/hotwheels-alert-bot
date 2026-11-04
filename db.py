import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "inventory.db"

# Bump when the meaning of keys changes. A bump wipes old rows and re-seeds silently.
SCHEMA = "2"   # v2: FirstCry keys are real product IDs (v1 used title hashes)

# An item must be missing from this many consecutive successful scans
# before it counts as "out of stock" (protects against flaky scrapes).
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
        c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        row = c.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
        if row is None or row[0] != SCHEMA:
            c.execute("DELETE FROM inventory")
            c.execute("DELETE FROM meta WHERE k='seeded'")
            c.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('schema', ?)", (SCHEMA,))


def is_first_run() -> bool:
    """True until the bot has successfully seen products at least once."""
    with _conn() as c:
        return c.execute("SELECT 1 FROM meta WHERE k='seeded'").fetchone() is None


def mark_seeded():
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('seeded', '1')")


def check_item(key: str, platform: str, title: str, link: str) -> bool:
    """
    Call for every item currently seen IN STOCK on the listing.
    Returns True when it is a candidate for an alert:
      - brand new item, or
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
        if was_out:
            c.execute(
                "UPDATE inventory SET status='in', miss_count=0, title=?, link=?, last_seen=? WHERE key=?",
                (title, link, now, key),
            )
        else:
            # Still in stock: only write if a miss counter needs resetting (keeps the file unchanged).
            c.execute("UPDATE inventory SET miss_count=0 WHERE key=? AND miss_count<>0", (key,))
        return was_out


def mark_missing(platform: str, seen_keys: set) -> int:
    """After a successful scan of `platform`: items not seen flip to 'out' after MISS_THRESHOLD scans."""
    flipped = 0
    with _conn() as c:
        rows = c.execute(
            "SELECT key, miss_count FROM inventory WHERE platform = ? AND status = 'in'", (platform,)
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


def mark_out(key: str):
    """Listing said in stock but the product page said out of stock: treat as out, alert on real restock."""
    with _conn() as c:
        c.execute("UPDATE inventory SET status='out', miss_count=? WHERE key=?", (MISS_THRESHOLD, key))


def forget(key: str):
    """Remove an item so the next run treats it as new again (used when an alert could not be sent)."""
    with _conn() as c:
        c.execute("DELETE FROM inventory WHERE key=?", (key,))
