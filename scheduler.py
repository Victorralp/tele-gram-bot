"""
Background async loops that run inside the bot's event loop.
  - posting_loop  : every 60 s, posts anything that's due
  - analytics_loop: every 6 h,  refreshes analytics for recent posts
"""
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import db
import facebook
from config import ALLOWED_USER_ID

log = logging.getLogger(__name__)

DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


# ── Recurrence helpers ─────────────────────────────────────────────────────────

def _next_occurrence(post: dict) -> datetime | None:
    scheduled = datetime.fromisoformat(post["scheduled_at"])
    recurring = post.get("recurring", "none")

    if recurring == "daily":
        return scheduled + timedelta(days=1)

    if recurring == "weekly":
        days_str = post.get("recurring_days") or "mon"
        targets  = {DAY_MAP[d] for d in days_str.split(",") if d in DAY_MAP}
        for offset in range(1, 8):
            candidate = scheduled + timedelta(days=offset)
            if candidate.weekday() in targets:
                return candidate

    return None


# ── Posting loop ───────────────────────────────────────────────────────────────

async def posting_loop(bot):
    log.info("📤 Posting loop started (checking every 60 s)")
    while True:
        await asyncio.sleep(60)
        try:
            for post in db.get_due_posts():
                await _process_post(bot, post)
        except Exception as e:
            log.error(f"Posting loop error: {e}")


async def _process_post(bot, post: dict):
    post_id = post["id"]
    kind    = post["type"]
    caption = post.get("caption") or ""

    fb_post_id = None
    error_msg = None
    try:
        if kind == "photo":
            path = post.get("file_path", "")
            if not Path(path).exists():
                error_msg = f"File missing: {path}"
                log.error(f"File missing for post #{post_id}: {path}")
            else:
                fb_post_id, error_msg = facebook.post_photo(path, caption)
        elif kind == "video":
            path = post.get("file_path", "")
            if not Path(path).exists():
                error_msg = f"File missing: {path}"
                log.error(f"File missing for post #{post_id}: {path}")
            else:
                fb_post_id, error_msg = facebook.post_video(path, caption)
        elif kind == "text":
            fb_post_id, error_msg = facebook.post_text(caption)
    except Exception as e:
        error_msg = f"Exception: {e}"
        log.error(f"Post #{post_id} exception: {e}")

    if fb_post_id:
        db.mark_posted(post_id, fb_post_id)
        log.info(f"✅ Post #{post_id} ({kind}) → FB {fb_post_id}")

        # Re-queue for recurring posts
        next_dt = _next_occurrence(post)
        if next_dt:
            new_id = db.add_post(
                type          = kind,
                file_path     = post.get("file_path"),
                caption       = caption,
                scheduled_at  = next_dt.isoformat(),
                recurring     = post["recurring"],
                recurring_days= post.get("recurring_days"),
            )
            log.info(f"🔁 Re-queued as #{new_id} for {next_dt.strftime('%d %b %Y %H:%M')}")

        # Notify you on Telegram
        try:
            dt = datetime.fromisoformat(post["scheduled_at"])
            rec = post.get("recurring", "none")
            rec_label = "" if rec == "none" else f"\n🔁 Recurring: {rec}"
            await bot.send_message(
                chat_id    = ALLOWED_USER_ID,
                text       = (
                    f"✅ *Posted to Facebook!*\n"
                    f"Type: {kind.upper()}\n"
                    f"At: {dt.strftime('%d %b %Y %H:%M')}"
                    f"{rec_label}"
                ),
                parse_mode = "Markdown",
            )
        except Exception as notify_err:
            log.warning(f"Notify error: {notify_err}")
    else:
        db.mark_failed(post_id)
        log.warning(f"❌ Post #{post_id} failed")
        err_str = f"\n\n*Error details:*\n`{error_msg}`" if error_msg else ""
        try:
            await bot.send_message(
                chat_id    = ALLOWED_USER_ID,
                text       = f"❌ *Post #{post_id} failed* to post to Facebook.{err_str}",
                parse_mode = "Markdown",
            )
        except Exception:
            pass


# ── Analytics loop ─────────────────────────────────────────────────────────────

async def analytics_loop():
    log.info("📊 Analytics loop started (refreshing every 6 h)")
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            posts = db.get_posts_needing_analytics()
            log.info(f"📊 Refreshing analytics for {len(posts)} post(s)")
            for p in posts:
                stats = facebook.fetch_analytics(p["fb_post_id"])
                if stats:
                    db.upsert_analytics(
                        p["id"],
                        stats["likes"], stats["comments"], stats["shares"],
                        stats["reach"], stats["impressions"],
                    )
                await asyncio.sleep(1)  # Respect FB rate limits
        except Exception as e:
            log.error(f"Analytics loop error: {e}")
