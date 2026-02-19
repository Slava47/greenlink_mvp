import os
import sqlite3
from datetime import datetime
from flask import current_app, g

SCHEMA_SQL = "\nPRAGMA foreign_keys = ON;\n\nCREATE TABLE IF NOT EXISTS universities (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  name TEXT NOT NULL UNIQUE\n);\n\nCREATE TABLE IF NOT EXISTS users (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  username TEXT NOT NULL UNIQUE,\n  password_hash TEXT NOT NULL,\n  role TEXT NOT NULL CHECK(role IN ('admin','organizer','volunteer')),\n  created_at TEXT NOT NULL,\n  is_blocked INTEGER NOT NULL DEFAULT 0,\n  warnings_count INTEGER NOT NULL DEFAULT 0,\n  last_warning_at TEXT,\n  full_name TEXT,\n  group_name TEXT,\n  faculty TEXT,\n  age INTEGER,\n  university_id INTEGER,\n  points INTEGER NOT NULL DEFAULT 0,\n  FOREIGN KEY (university_id) REFERENCES universities(id)\n);\n\nCREATE TABLE IF NOT EXISTS subscribers (\n  user_id INTEGER PRIMARY KEY,\n  is_subscribed INTEGER NOT NULL DEFAULT 1,\n  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n);\n\nCREATE TABLE IF NOT EXISTS events (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  name TEXT NOT NULL,\n  description TEXT,\n  link TEXT,\n  points INTEGER NOT NULL DEFAULT 0,\n  start_time TEXT,\n  end_time TEXT,\n  max_participants INTEGER NOT NULL DEFAULT 0,\n  created_by INTEGER,\n  created_at TEXT NOT NULL,\n  FOREIGN KEY (created_by) REFERENCES users(id)\n);\n\nCREATE TABLE IF NOT EXISTS event_applications (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  event_id INTEGER NOT NULL,\n  user_id INTEGER NOT NULL,\n  needs_release INTEGER NOT NULL DEFAULT 0,\n  needs_volunteer_hours INTEGER NOT NULL DEFAULT 0,\n  status TEXT NOT NULL DEFAULT 'на рассмотрении',\n  created_at TEXT NOT NULL,\n  UNIQUE(event_id, user_id),\n  FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,\n  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n);\n\nCREATE TABLE IF NOT EXISTS event_reports (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  event_id INTEGER NOT NULL,\n  user_id INTEGER NOT NULL,\n  report_text TEXT,\n  media_path TEXT,\n  status TEXT NOT NULL DEFAULT 'на рассмотрении',\n  created_at TEXT NOT NULL,\n  UNIQUE(event_id, user_id),\n  FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,\n  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n);\n\nCREATE TABLE IF NOT EXISTS tasks (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  name TEXT NOT NULL,\n  description TEXT,\n  points INTEGER NOT NULL DEFAULT 0,\n  start_time TEXT,\n  end_time TEXT,\n  max_participants INTEGER NOT NULL DEFAULT 0,\n  created_by INTEGER,\n  created_at TEXT NOT NULL,\n  FOREIGN KEY (created_by) REFERENCES users(id)\n);\n\nCREATE TABLE IF NOT EXISTS task_applications (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  task_id INTEGER NOT NULL,\n  user_id INTEGER NOT NULL,\n  status TEXT NOT NULL DEFAULT 'на рассмотрении',\n  created_at TEXT NOT NULL,\n  UNIQUE(task_id, user_id),\n  FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,\n  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n);\n\nCREATE TABLE IF NOT EXISTS task_reports (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  task_id INTEGER NOT NULL,\n  user_id INTEGER NOT NULL,\n  report_text TEXT,\n  media_path TEXT,\n  status TEXT NOT NULL DEFAULT 'на рассмотрении',\n  created_at TEXT NOT NULL,\n  UNIQUE(task_id, user_id),\n  FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,\n  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n);\n"

def get_db():
    if "db" not in g:
        db_path = current_app.config["DB_PATH"]
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
        # foreign keys
        g.db.execute("PRAGMA foreign_keys = ON;")
    return g.db

def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_user_columns(db):
    """Lightweight schema migration for optional profile fields."""
    cols = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    # organizer/admin profile fields (volunteer fields remain)
    if "education_text" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN education_text TEXT")
    if "bio_text" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN bio_text TEXT")



def ensure_report_columns(db):
    """Lightweight schema migration for report award tracking."""
    for table in ("event_reports", "task_reports"):
        cols = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if "points_awarded" not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN points_awarded INTEGER DEFAULT 0")


def init_db():
    db = get_db()
    db.executescript(SCHEMA_SQL)
    ensure_user_columns(db)
    ensure_report_columns(db)
    db.commit()

def init_db_if_needed(app):
    # Create DB and optionally seed if missing
    with app.app_context():
        db_path = app.config["DB_PATH"]
        first_run = not os.path.exists(db_path)
        init_db()
        if first_run and app.config.get("SEED_ON_FIRST_RUN", True):
            from .seed import seed_data
            seed_data()

def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
