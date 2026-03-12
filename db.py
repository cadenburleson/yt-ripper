"""SQLite database wrapper for yt-ripper multi-user support."""

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path


DB_PATH = Path(os.getenv("DB_PATH", "app.db"))


def get_connection():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database schema if not exists."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT,
                channel_name TEXT,
                access_token TEXT,
                refresh_token TEXT,
                token_expiry INTEGER
            );

            CREATE TABLE IF NOT EXISTS upload_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                filename TEXT,
                filepath TEXT,
                youtube_id TEXT,
                title TEXT,
                status TEXT,
                created_at TEXT,
                uploaded_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS scheduler_config (
                user_id TEXT PRIMARY KEY,
                enabled INTEGER,
                interval_hours REAL,
                title_template TEXT,
                description TEXT,
                output_dir TEXT,
                made_for_kids INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_upload_jobs_user_id ON upload_jobs(user_id);
            CREATE INDEX IF NOT EXISTS idx_upload_jobs_status ON upload_jobs(status);
        """)
        conn.commit()
    finally:
        conn.close()


def get_user(user_id):
    """Get user by ID."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_user(user_id, email, channel_name, access_token, refresh_token, token_expiry):
    """Insert or update a user."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO users
            (id, email, channel_name, access_token, refresh_token, token_expiry)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, email, channel_name, access_token, refresh_token, token_expiry),
        )
        conn.commit()
    finally:
        conn.close()


def get_scheduler_config(user_id):
    """Get scheduler config for a user."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM scheduler_config WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_scheduler_config(
    user_id, enabled, interval_hours, title_template, description, output_dir, made_for_kids=0
):
    """Insert or update scheduler config."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO scheduler_config
            (user_id, enabled, interval_hours, title_template, description, output_dir, made_for_kids)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, enabled, interval_hours, title_template, description, output_dir, made_for_kids),
        )
        conn.commit()
    finally:
        conn.close()


def get_active_schedulers():
    """Get all users with active schedulers."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduler_config WHERE enabled = 1"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def add_upload_job(user_id, filename, filepath, title):
    """Add a new upload job."""
    conn = get_connection()
    try:
        now = datetime.utcnow().isoformat()
        cursor = conn.execute(
            """
            INSERT INTO upload_jobs
            (user_id, filename, filepath, title, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, filename, filepath, title, "queued", now),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_upload_job(job_id, status, youtube_id=None, uploaded_at=None):
    """Update job status and metadata."""
    conn = get_connection()
    try:
        if uploaded_at is None and status == "uploaded":
            uploaded_at = datetime.utcnow().isoformat()

        conn.execute(
            """
            UPDATE upload_jobs
            SET status = ?, youtube_id = ?, uploaded_at = ?
            WHERE id = ?
            """,
            (status, youtube_id, uploaded_at, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_upload_jobs(user_id):
    """Get all upload jobs for a user."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM upload_jobs
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_next_queued_job(user_id):
    """Get the oldest queued job for a user."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM upload_jobs
            WHERE user_id = ? AND status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_last_upload_time(user_id):
    """Get timestamp of last successful upload for a user (or None if no uploads)."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT uploaded_at FROM upload_jobs
            WHERE user_id = ? AND status = 'uploaded'
            ORDER BY uploaded_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row and row["uploaded_at"]:
            dt = datetime.fromisoformat(row["uploaded_at"])
            return dt.timestamp()
        return None
    finally:
        conn.close()
