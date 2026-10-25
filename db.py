"""db.py — حفظ سجل المحادثة في SQLite."""
import sqlite3

import config


def init_db() -> None:
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   user_id TEXT, role TEXT, content TEXT)"""
        )


def save_message(user_id: str, role: str, content: str) -> None:
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, str(content)),
        )
