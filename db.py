import sqlite3
from datetime import datetime

DB_PATH = "bread_orders.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            telegram_id INTEGER,
            bread_name TEXT,
            quantity INTEGER,
            total INTEGER,
            location TEXT,
            phone TEXT,
            status TEXT DEFAULT 'pending_payment',
            checkout_id TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_order(order):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
        (order["id"], order["telegram_id"], order["bread_name"],
         order["qty"], order["total"], order["location"],
         order["phone"], "pending_payment", order.get("checkout_id"),
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def update_checkout_id(order_id, checkout_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE orders SET checkout_id=? WHERE id=?", (checkout_id, order_id))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    conn.close()
    return row

def mark_paid(order_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,))
    conn.commit()
    conn.close()

def get_paid_orders():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT * FROM orders WHERE status='paid' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return rows
