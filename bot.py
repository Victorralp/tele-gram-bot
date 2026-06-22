"""
Advanced Telegram → Facebook Auto-Poster Bot

Features:
  - Single photo / video / text scheduling
  - Recurring posts (daily or weekly on picked days)
  - Bulk upload: send N files, auto-spread across days
  - /queue, /posted, /analytics, /delete, /clear commands
"""
import asyncio
import logging
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ConversationHandler, ContextTypes, MessageHandler, filters,
)

import db
from config import ALLOWED_USER_ID, TELEGRAM_TOKEN
from scheduler import analytics_loop, posting_loop

log = logging.getLogger(__name__)

MEDIA_DIR = Path("media")
MEDIA_DIR.mkdir(exist_ok=True)

# ── Conversation states ────────────────────────────────────────────────────────
WAIT_DATE, WAIT_RECURRING, WAIT_DAYS, WAIT_CAPTION = 0, 1, 2, 3
BULK_COLLECT, BULK_START, BULK_INTERVAL = 10, 11, 12

# ── Helpers ────────────────────────────────────────────────────────────────────

def auth(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID


def parse_dt(raw: str) -> datetime | None:
    raw = raw.strip()
    if raw.lower() == "now":
        return datetime.now()
    for fmt in ("%d %b %Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def days_keyboard(selected: set) -> InlineKeyboardMarkup:
    all_days = [("Mon","mon"),("Tue","tue"),("Wed","wed"),("Thu","thu"),
                ("Fri","fri"),("Sat","sat"),("Sun","sun")]
    row1, row2 = [], []
    for i, (label, key) in enumerate(all_days):
        text = f"✅ {label}" if key in selected else label
        btn  = InlineKeyboardButton(text, callback_data=f"day_{key}")
        (row1 if i < 4 else row2).append(btn)
    confirm = [InlineKeyboardButton("✅ Confirm Days", callback_data="days_done")]
    return InlineKeyboardMarkup([row1, row2, confirm])


# ── Commands ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    s = db.get_stats()
    await update.message.reply_text(
        "🤖 *FB Auto-Poster — Advanced*\n\n"
        f"📊 {s.get('total',0)} total · {s.get('pending',0)} pending · {s.get('posted',0)} posted\n\n"
        "*What you can do:*\n"
        "📸 Send a photo / video / text → schedule it\n"
        "/bulk — Upload many posts at once\n"
        "/queue — Upcoming scheduled posts\n"
        "/posted — Recent posts + analytics\n"
        "/analytics — Overall engagement totals\n"
        "/delete `<id>` — Remove a specific post\n"
        "/caption `<id> <new caption>` — Update caption of a post\n"
        "/clear — Remove all pending posts\n"
        "/cancel — Cancel current action\n\n"
        "🌐 Open your Railway URL to see the dashboard.",
        parse_mode="Markdown",
    )


async def cmd_queue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    posts = db.get_pending_posts()
    if not posts:
        await update.message.reply_text("📭 Queue is empty.")
        return
    lines = [f"📅 *Upcoming Posts ({len(posts)}):*\n"]
    for p in posts[:20]:
        dt  = datetime.fromisoformat(p["scheduled_at"])
        rec = "" if p["recurring"] == "none" else f" 🔁{p['recurring']}"
        pre = (p.get("caption") or "")[:35]
        lines.append(
            f"`#{p['id']}` [{p['type'].upper()}]{rec}\n"
            f"  📅 {dt.strftime('%d %b %Y %H:%M')}\n"
            f"  💬 {pre or '(no caption)'}…"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_posted(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    posts = db.get_posted_posts(10)
    if not posts:
        await update.message.reply_text("📭 Nothing posted yet.")
        return
    lines = [f"✅ *Recent Posts ({len(posts)}):*\n"]
    for p in posts:
        dt = datetime.fromisoformat(p["posted_at"])
        a  = db.get_analytics_for_post(p["id"]) or {}
        lines.append(
            f"`#{p['id']}` [{p['type'].upper()}] {dt.strftime('%d %b %Y')}\n"
            f"  ❤️ {a.get('likes_count',0)}  "
            f"💬 {a.get('comments_count',0)}  "
            f"↗️ {a.get('shares_count',0)}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_analytics(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    t = db.get_analytics_totals()
    s = db.get_stats()
    await update.message.reply_text(
        "📊 *Overall Analytics*\n\n"
        f"❤️ Likes:      {t.get('total_likes',0):,}\n"
        f"💬 Comments:  {t.get('total_comments',0):,}\n"
        f"↗️ Shares:    {t.get('total_shares',0):,}\n"
        f"👁️ Reach:     {t.get('total_reach',0):,}\n\n"
        f"✅ Posted:  {s.get('posted',0)}\n"
        f"⏳ Pending: {s.get('pending',0)}\n"
        f"❌ Failed:  {s.get('failed',0)}",
        parse_mode="Markdown",
    )


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: /delete `<id>`", parse_mode="Markdown")
        return
    try:
        db.delete_post(int(ctx.args[0]))
        await update.message.reply_text(f"🗑️ Post #{ctx.args[0]} deleted.")
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Invalid ID.")


async def cmd_caption(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("Usage: /caption `<id>` `<new caption>`", parse_mode="Markdown")
        return
    try:
        post_id = int(ctx.args[0])
        new_caption = " ".join(ctx.args[1:])
        post = db.get_post(post_id)
        if not post:
            await update.message.reply_text("⚠️ Post not found.")
            return
        db.update_caption(post_id, new_caption)
        await update.message.reply_text(f"📝 Caption for post #{post_id} updated successfully.")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid ID.")


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    pending = db.get_pending_posts()
    for p in pending:
        db.delete_post(p["id"])
    await update.message.reply_text(f"🗑️ Cleared {len(pending)} pending post(s).")


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ── Single post flow ───────────────────────────────────────────────────────────

async def recv_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return ConversationHandler.END
    msg = update.message

    if msg.photo:
        file  = await msg.photo[-1].get_file()
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        kind  = "photo"
    elif msg.video:
        file  = await msg.video.get_file()
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        kind  = "video"
    else:
        return ConversationHandler.END

    local = str(MEDIA_DIR / fname)
    await file.download_to_drive(local)
    
    if msg.caption:
        ctx.user_data["pending"] = {"type": kind, "file_path": local, "caption": msg.caption}
        await msg.reply_text(
            f"✅ Got your *{kind}* with caption!\n\n"
            "📅 When to post? (e.g. `25 Jun 2025 09:00` or `now`)",
            parse_mode="Markdown",
        )
        return WAIT_DATE
    else:
        ctx.user_data["pending"] = {"type": kind, "file_path": local, "caption": ""}
        await msg.reply_text(
            f"✅ Got your *{kind}*!\n\n"
            "💬 Send the caption for this post now, or send /skip to continue without a caption.",
            parse_mode="Markdown",
        )
        return WAIT_CAPTION


async def recv_caption(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return ConversationHandler.END
    ctx.user_data["pending"]["caption"] = update.message.text
    await update.message.reply_text(
        "📝 Caption saved!\n\n"
        "📅 When to post? (e.g. `25 Jun 2025 09:00` or `now`)",
        parse_mode="Markdown",
    )
    return WAIT_DATE


async def recv_caption_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return ConversationHandler.END
    ctx.user_data["pending"]["caption"] = ""
    await update.message.reply_text(
        "👍 No caption added.\n\n"
        "📅 When to post? (e.g. `25 Jun 2025 09:00` or `now`)",
        parse_mode="Markdown",
    )
    return WAIT_DATE


async def recv_text_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return ConversationHandler.END
    ctx.user_data["pending"] = {"type": "text", "caption": update.message.text}
    await update.message.reply_text(
        "✅ Got your *text post*!\n\n"
        "📅 When to post? (e.g. `25 Jun 2025 09:00` or `now`)",
        parse_mode="Markdown",
    )
    return WAIT_DATE


async def recv_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return ConversationHandler.END
    dt = parse_dt(update.message.text)
    if not dt:
        await update.message.reply_text(
            "⚠️ Couldn't read that.\nTry: `25 Jun 2025 09:00` or `now`",
            parse_mode="Markdown",
        )
        return WAIT_DATE

    ctx.user_data["scheduled_at"] = dt.isoformat()
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗓️ One-time", callback_data="rec_none"),
            InlineKeyboardButton("🔁 Daily",   callback_data="rec_daily"),
        ],
        [InlineKeyboardButton("📅 Weekly (pick days)", callback_data="rec_weekly")],
    ])
    await update.message.reply_text(
        f"📅 Scheduled: *{dt.strftime('%d %b %Y at %H:%M')}*\n\nShould this post repeat?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return WAIT_RECURRING


async def recv_recurring(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "rec_weekly":
        ctx.user_data.update({"recurring": "weekly", "selected_days": set()})
        await q.edit_message_text(
            "📅 Tap the days you want to post, then confirm:",
            reply_markup=days_keyboard(set()),
        )
        return WAIT_DAYS

    ctx.user_data["recurring"] = "none" if q.data == "rec_none" else "daily"
    await _save_single_post(q, ctx)
    return ConversationHandler.END


async def toggle_day(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    day = q.data.split("_")[1]
    sel = ctx.user_data.get("selected_days", set())
    sel ^= {day}
    ctx.user_data["selected_days"] = sel
    await q.edit_message_reply_markup(reply_markup=days_keyboard(sel))
    return WAIT_DAYS


async def confirm_days(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    sel = ctx.user_data.get("selected_days", set())
    if not sel:
        await q.answer("⚠️ Pick at least one day!", show_alert=True)
        return WAIT_DAYS
    await q.answer()
    order = ["mon","tue","wed","thu","fri","sat","sun"]
    ctx.user_data["recurring_days"] = ",".join(d for d in order if d in sel)
    await _save_single_post(q, ctx)
    return ConversationHandler.END


async def _save_single_post(q_or_msg, ctx: ContextTypes.DEFAULT_TYPE):
    p   = ctx.user_data.get("pending", {})
    rec = ctx.user_data.get("recurring", "none")
    rdays = ctx.user_data.get("recurring_days")
    sat = ctx.user_data.get("scheduled_at")

    post_id = db.add_post(
        type=p["type"], file_path=p.get("file_path"),
        caption=p.get("caption",""), scheduled_at=sat,
        recurring=rec, recurring_days=rdays,
    )
    ctx.user_data.clear()

    dt  = datetime.fromisoformat(sat)
    lab = {"none":"One-time","daily":"🔁 Repeats daily",
           "weekly":f"🔁 Weekly ({rdays})"}.get(rec, "")
    txt = (
        f"🗓️ *Post #{post_id} scheduled!*\n\n"
        f"• Type: {p['type'].upper()}\n"
        f"• At:   {dt.strftime('%d %b %Y %H:%M')}\n"
        f"• {lab}\n\n"
        "Use /queue to see all upcoming posts."
    )
    if hasattr(q_or_msg, "edit_message_text"):
        await q_or_msg.edit_message_text(txt, parse_mode="Markdown")
    else:
        await q_or_msg.reply_text(txt, parse_mode="Markdown")


async def recv_zip_bulk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return ConversationHandler.END
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".zip"):
        await update.message.reply_text("⚠️ Please send a valid ZIP file.")
        return ConversationHandler.END

    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text(
            "⚠️ *File is too big!*\n\n"
            "Telegram strictly limits bots from downloading files larger than **20 MB**.\n"
            "Please split your folder into smaller `.zip` files (under 20 MB each) and upload them one by one.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    status_msg = await update.message.reply_text("📥 Downloading ZIP file...")
    
    temp_dir = Path(tempfile.mkdtemp())
    try:
        file = await doc.get_file()
        zip_path = temp_dir / doc.file_name
        await file.download_to_drive(str(zip_path))
        
        await status_msg.edit_text("📂 Unpacking ZIP file...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
            
        media_extensions = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".avi"}
        items = []
        batch_id = str(uuid.uuid4())[:8]
        
        for p in temp_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in media_extensions:
                # Skip macOS metadata files
                if p.name.startswith("._") or any(part.startswith("__") or part == "__MACOSX" for part in p.parts):
                    continue
                    
                txt_path = p.with_suffix(".txt")
                caption = ""
                if txt_path.exists():
                    try:
                        caption = txt_path.read_text(encoding="utf-8").strip()
                    except Exception:
                        try:
                            caption = txt_path.read_text(encoding="latin-1").strip()
                        except Exception:
                            caption = ""
                
                fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{batch_id}_{len(items)}{p.suffix}"
                dest = MEDIA_DIR / fname
                shutil.copy2(p, dest)
                
                kind = "video" if p.suffix.lower() in {".mp4", ".mov", ".avi"} else "photo"
                items.append({
                    "type": kind,
                    "file_path": str(dest),
                    "caption": caption
                })
        
        if not items:
            await status_msg.edit_text("⚠️ No supported photos or videos found in the ZIP file.")
            return ConversationHandler.END
            
        ctx.user_data["bulk"] = {
            "items": items,
            "batch_id": batch_id
        }
        
        await status_msg.edit_text(
            f"📦 Got *{len(items)} posts* from ZIP!\n\n"
            "📅 When should the *first* post go out?\n"
            "`25 Jun 2025 09:00` or `now`",
            parse_mode="Markdown",
        )
        return BULK_START
        
    except Exception as e:
        log.error(f"Error handling ZIP bulk: {e}")
        await status_msg.edit_text(f"❌ Failed to process ZIP file: {e}")
        return ConversationHandler.END
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ── Bulk upload flow ───────────────────────────────────────────────────────────

async def cmd_bulk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    ctx.user_data["bulk"] = {"items": [], "batch_id": str(uuid.uuid4())[:8]}
    await update.message.reply_text(
        "📦 *Bulk Upload Mode*\n\n"
        "Send me all your photos/videos (with captions).\n"
        "When you're done, send /done\n"
        "To cancel, send /cancel",
        parse_mode="Markdown",
    )
    return BULK_COLLECT


async def collect_bulk_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    msg   = update.message
    items = ctx.user_data.get("bulk", {}).get("items", [])

    if msg.photo:
        file  = await msg.photo[-1].get_file()
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(items)}.jpg"
        kind  = "photo"
    elif msg.video:
        file  = await msg.video.get_file()
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(items)}.mp4"
        kind  = "video"
    else:
        return BULK_COLLECT

    local = str(MEDIA_DIR / fname)
    await file.download_to_drive(local)
    items.append({"type": kind, "file_path": local, "caption": msg.caption or ""})
    ctx.user_data["bulk"]["items"] = items

    await msg.reply_text(
        f"✅ *{kind.capitalize()} #{len(items)}* saved. Keep sending or /done.",
        parse_mode="Markdown",
    )
    return BULK_COLLECT


async def finish_bulk_collect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    items = ctx.user_data.get("bulk", {}).get("items", [])
    if not items:
        await update.message.reply_text("⚠️ No files received yet. Send some files first!")
        return BULK_COLLECT

    await update.message.reply_text(
        f"📦 Got *{len(items)} files*!\n\n"
        "📅 When should the *first* post go out?\n"
        "`25 Jun 2025 09:00` or `now`",
        parse_mode="Markdown",
    )
    return BULK_START


async def recv_bulk_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    dt = parse_dt(update.message.text)
    if not dt:
        await update.message.reply_text(
            "⚠️ Can't read that. Try: `25 Jun 2025 09:00` or `now`",
            parse_mode="Markdown",
        )
        return BULK_START

    ctx.user_data["bulk"]["start_dt"] = dt.isoformat()
    await update.message.reply_text(
        "⏱️ How many *days apart* should each post be?\n"
        "e.g. `1` = daily · `2` = every 2 days · `0.5` = every 12 h · `7` = weekly",
        parse_mode="Markdown",
    )
    return BULK_INTERVAL


async def recv_bulk_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    try:
        interval = float(update.message.text.strip())
        if interval <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Enter a positive number, e.g. `1` or `0.5`.")
        return BULK_INTERVAL

    bulk     = ctx.user_data.get("bulk", {})
    items    = bulk.get("items", [])
    batch_id = bulk.get("batch_id")
    start    = datetime.fromisoformat(bulk["start_dt"])

    scheduled = []
    for i, item in enumerate(items):
        post_dt = start + timedelta(days=i * interval)
        pid     = db.add_post(
            type=item["type"], file_path=item.get("file_path"),
            caption=item.get("caption",""), scheduled_at=post_dt.isoformat(),
            bulk_batch_id=batch_id,
        )
        scheduled.append((pid, post_dt))

    ctx.user_data.clear()

    lines = [f"🗓️ *Scheduled {len(items)} posts!*\n"]
    for pid, pdt in scheduled[:12]:
        lines.append(f"  `#{pid}` → {pdt.strftime('%d %b %Y %H:%M')}")
    if len(items) > 12:
        lines.append(f"  … and {len(items)-12} more")
    lines.append("\nUse /queue to review everything.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    return ConversationHandler.END


# ── Build Application ──────────────────────────────────────────────────────────

def setup_bot() -> Application:
    async def post_init(app):
        asyncio.create_task(posting_loop(app.bot))
        asyncio.create_task(analytics_loop())
        log.info("🚀 Background tasks started")

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Single post conversation
    single = ConversationHandler(
        entry_points=[
            MessageHandler(filters.PHOTO | filters.VIDEO, recv_media),
            MessageHandler(filters.TEXT & ~filters.COMMAND, recv_text_post),
        ],
        states={
            WAIT_CAPTION: [
                CommandHandler("skip", recv_caption_skip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_caption),
            ],
            WAIT_DATE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_date)],
            WAIT_RECURRING: [CallbackQueryHandler(recv_recurring, pattern="^rec_")],
            WAIT_DAYS: [
                CallbackQueryHandler(toggle_day,   pattern="^day_"),
                CallbackQueryHandler(confirm_days, pattern="^days_done$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    # Bulk upload conversation
    bulk = ConversationHandler(
        entry_points=[
            CommandHandler("bulk", cmd_bulk),
            MessageHandler(filters.Document.ZIP, recv_zip_bulk),
        ],
        states={
            BULK_COLLECT:  [
                MessageHandler(filters.PHOTO | filters.VIDEO, collect_bulk_item),
                CommandHandler("done", finish_bulk_collect),
            ],
            BULK_START:    [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_bulk_start)],
            BULK_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_bulk_interval)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("queue",     cmd_queue))
    app.add_handler(CommandHandler("posted",    cmd_posted))
    app.add_handler(CommandHandler("analytics", cmd_analytics))
    app.add_handler(CommandHandler("delete",    cmd_delete))
    app.add_handler(CommandHandler("caption",   cmd_caption))
    app.add_handler(CommandHandler("clear",     cmd_clear))
    app.add_handler(bulk)
    app.add_handler(single)

    return app
