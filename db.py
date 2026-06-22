"""
Database layer — SQLite with WAL mode for concurrent bot + dashboard access.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = "posts.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                type            TEXT NOT NULL,
                file_path       TEXT,
                caption         TEXT DEFAULT '',
                scheduled_at    TEXT NOT NULL,
                posted_at       TEXT,
                status          TEXT DEFAULT 'pending',
                fb_post_id      TEXT,
                recurring       TEXT DEFAULT 'none',
                recurring_days  TEXT,
                bulk_batch_id   TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS analytics (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id         INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                fetched_at      TEXT DEFAULT (datetime('now')),
                likes_count     INTEGER DEFAULT 0,
                comments_count  INTEGER DEFAULT 0,
                shares_count    INTEGER DEFAULT 0,
                reach           INTEGER DEFAULT 0,
                impressions     INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_posts_status    ON posts(status);
            CREATE INDEX IF NOT EXISTS idx_posts_scheduled ON posts(scheduled_at);
            CREATE INDEX IF NOT EXISTS idx_analytics_post  ON analytics(post_id);
        """)


# ── Posts ──────────────────────────────────────────────────────────────────────

def add_post(type, file_path, caption, scheduled_at,
             recurring="none", recurring_days=None, bulk_batch_id=None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO posts
               (type, file_path, caption, scheduled_at, recurring, recurring_days, bulk_batch_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (type, file_path, caption or "", scheduled_at,
             recurring, recurring_days, bulk_batch_id)
        )
        return cur.lastrowid


def get_due_posts() -> list:
    now = datetime.now().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE status='pending' AND scheduled_at<=? ORDER BY scheduled_at",
            (now,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_pending_posts() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE status='pending' ORDER BY scheduled_at"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_posts(limit: int = 100) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM posts ORDER BY scheduled_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_posted_posts(limit: int = 30) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE status='posted' ORDER BY posted_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_posted(post_id: int, fb_post_id: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE posts SET status='posted', fb_post_id=?, posted_at=datetime('now') WHERE id=?",
            (fb_post_id, post_id)
        )


def mark_failed(post_id: int):
    with get_db() as conn:
        conn.execute("UPDATE posts SET status='failed' WHERE id=?", (post_id,))


def delete_post(post_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM posts WHERE id=?", (post_id,))


def get_stats() -> dict:
    with get_db() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status='posted'  THEN 1 ELSE 0 END) as posted,
                SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END) as failed
            FROM posts
        """).fetchone()
    return dict(row) if row else {}


# ── Analytics ──────────────────────────────────────────────────────────────────

def upsert_analytics(post_id: int, likes: int, comments: int,
                     shares: int, reach: int, impressions: int):
    with get_db() as conn:
        exists = conn.execute(
            "SELECT id FROM analytics WHERE post_id=?", (post_id,)
        ).fetchone()
        if exists:
            conn.execute(
                """UPDATE analytics SET fetched_at=datetime('now'), likes_count=?,
                   comments_count=?, shares_count=?, reach=?, impressions=?
                   WHERE post_id=?""",
                (likes, comments, shares, reach, impressions, post_id)
            )
        else:
            conn.execute(
                """INSERT INTO analytics
                   (post_id, likes_count, comments_count, shares_count, reach, impressions)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (post_id, likes, comments, shares, reach, impressions)
            )


def get_analytics_for_post(post_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM analytics WHERE post_id=? ORDER BY fetched_at DESC LIMIT 1",
            (post_id,)
        ).fetchone()
    return dict(row) if row else None


def get_posts_needing_analytics() -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, fb_post_id FROM posts
               WHERE status='posted' AND fb_post_id IS NOT NULL
               AND posted_at >= datetime('now', '-30 days')"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_analytics_totals() -> dict:
    with get_db() as conn:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(a.likes_count),    0) as total_likes,
                COALESCE(SUM(a.comments_count), 0) as total_comments,
                COALESCE(SUM(a.shares_count),   0) as total_shares,
                COALESCE(SUM(a.reach),           0) as total_reach,
                COALESCE(SUM(a.impressions),     0) as total_impressions
            FROM analytics a JOIN posts p ON a.post_id = p.id
        """).fetchone()
    return dict(row) if row else {}


def get_chart_data(days: int = 30) -> list:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                DATE(p.posted_at)              as date,
                COALESCE(SUM(a.likes_count),   0) as likes,
                COALESCE(SUM(a.comments_count),0) as comments,
                COALESCE(SUM(a.shares_count),  0) as shares,
                COALESCE(SUM(a.reach),         0) as reach,
                COUNT(p.id)                       as posts_count
            FROM posts p
            JOIN analytics a ON a.post_id = p.id
            WHERE p.status='posted'
              AND p.posted_at >= datetime('now', ? || ' days')
            GROUP BY DATE(p.posted_at)
            ORDER BY date
        """, (f"-{days}",)).fetchall()
    return [dict(r) for r in rows]


def get_post(post_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    return dict(row) if row else None


def update_caption(post_id: int, caption: str):
    with get_db() as conn:
        conn.execute("UPDATE posts SET caption=? WHERE id=?", (caption or "", post_id))

