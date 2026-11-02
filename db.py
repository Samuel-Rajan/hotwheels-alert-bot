import sqlite3

def init_db():
    """Initializes the SQLite database to store seen products."""
    conn = sqlite3.connect("inventory.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seen_products (
            platform_id TEXT PRIMARY KEY,
            platform TEXT,
            title TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def is_new_item(platform_id):
    """Checks if a product has already been alerted."""
    conn = sqlite3.connect("inventory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM seen_products WHERE platform_id = ?", (platform_id,))
    result = cursor.fetchone()
    conn.close()
    return result is None

def save_item(platform_id, platform, title):
    """Saves a product to the database so it won't trigger duplicate alerts."""
    conn = sqlite3.connect("inventory.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO seen_products (platform_id, platform, title) VALUES (?, ?, ?)",
        (platform_id, platform, title)
    )
    conn.commit()
    conn.close()