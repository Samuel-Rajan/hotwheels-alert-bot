import sqlite3
from datetime import datetime, timedelta

def init_db():
    """Initializes SQLite database to track active inventory with TTL support for restocks."""
    conn = sqlite3.connect("inventory.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS active_inventory (
            platform_id TEXT PRIMARY KEY,
            platform TEXT,
            title TEXT,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def process_item_and_check_restock(platform_id, platform, title, expiry_minutes=30):
    """
    Checks if an item is new or has returned from being out of stock.
    - If it's not in the DB, or hasn't been seen for > expiry_minutes (meaning it went out of stock),
      it returns True (triggers a new alert).
    - Upserts the item with the current timestamp.
    """
    conn = sqlite3.connect("inventory.db")
    cursor = conn.cursor()
    
    now = datetime.now()
    cutoff_time = now - timedelta(minutes=expiry_minutes)
    
    # Check if item exists in database
    cursor.execute("SELECT last_seen FROM active_inventory WHERE platform_id = ?", (platform_id,))
    row = cursor.fetchone()
    
    is_alertable = False
    
    if row is None:
        # Brand new item
        is_alertable = True
    else:
        # Item exists, check when it was last seen
        last_seen_str = row[0]
        try:
            last_seen_time = datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            try:
                last_seen_time = datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                last_seen_time = cutoff_time - timedelta(days=1)
                
        if last_seen_time < cutoff_time:
            # It was out of stock long enough to expire, so its return is a restock alert!
            is_alertable = True
            
    # Upsert the item with current timestamp
    cursor.execute('''
        INSERT OR REPLACE INTO active_inventory (platform_id, platform, title, last_seen)
        VALUES (?, ?, ?, ?)
    ''', (platform_id, platform, title, now))
    
    conn.commit()
    conn.close()
    
    return is_alertable
