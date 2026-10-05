import os
import re
import shutil
import sqlite3
import asyncio
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

import yt_dlp
from PIL import Image, ImageDraw, ImageFont

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# SETTINGS
# =========================

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود.")

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)
WATERMARK_TEXT = "@AhmedMediaDL_bot"
WATERMARK_LOGO = "watermark.png"   # لو حطيت اللوجو هنا هيستخدمه بدل النص
COOKIES_FILE = "cookies.txt"       # اختياري (إنستجرام / فيسبوك)
CHANNEL_USERNAME = "@AhmedMediaDL"  # قناة الاشتراك الإجباري
CHANNEL_URL = "https://t.me/AhmedMediaDL"

DB_FILE = "users.db"
MAX_FILE_SIZE = 49 * 1024 * 1024
PREMIUM_STARS = 100
FREE_MAX_HEIGHT = 720

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
LIMIT = asyncio.Semaphore(2)  # عمليتين تحميل كحد أقصى مع بعض


# =========================
# DATABASE
# =========================

def db():
    con = sqlite3.connect(DB_FILE)
    con.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "user_id INTEGER PRIMARY KEY, premium_until TEXT)"
    )
    con.commit()
    return con


def is_premium(user_id):
    con = db()
    row = con.execute(
        "SELECT premium_until FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    con.close()
    if not row or not row[0]:
        return False
    try:
        return datetime.fromisoformat(row[0]) > datetime.now(timezone.utc)
    except Exception:
        return False


def activate_premium(user_id, days=30):
    con = db()
    row = con.execute(
        "SELECT premium_until FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    now = datetime.now(timezone.utc)
    start = now
    if row and row[0]:
        try:
            start = max(now, datetime.fromisoformat(row[0]))
        except Exception:
            pass
    until = start + timedelta(days=days)
    con.execute(
        "INSERT INTO users (user_id, premium_until) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET premium_until = excluded.premium_until",
        (user_id, until.isoformat()),
    )
    con.commit()
    con.close()


# =========================
# FFMPEG
# =========================

def ffmpeg_path():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = ffmpeg_path()


# =========================
# YT-DLP OPTIONS
# =========================

def base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "geo_bypass": True,
    }
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG

    # impersonate لازم يكون كائن مش نص (ده كان سبب الخطأ)
    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        opts["impersonate"] = ImpersonateTarget("chrome")
    except Exception as e:
        print("impersonate غير متاح:", repr(e))

    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts


STANDARD = [144, 240, 360, 480, 720, 1080, 1440, 2160]


def get_formats(url):
    opts = {**base_opts(), "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist" and info.get("entries"):
        info = next((e for e in info["entries"] if e), info)

    heights = set()
    has_video = False
    for f in info.get("formats", []) or []:
        if f.get("vcodec") not in (None, "none"):
            has_video = True
            h = f.get("height")
            if h:
                # أقرب جودة قياسية
                nearest = min(STANDARD, key=lambda s: abs(s - h))
                if h >= nearest * 0.9:
                    heights.add(nearest)
    if not has_video and info.get("url") and info.get("ext") not in ("mp3", "m4a"):
        has_video = True
    return sorted(heights), has_video


# =========================
# WATERMARK
# =========================

def make_watermark_png(path, video_width):
    target_w = max(160, int((video_width or 720) * 0.30))

    if os.path.exists(WATERMARK_LOGO):
        img = Image.open(WATERMARK_LOGO).convert("RGBA")
        ratio = target_w / img.width
        img = img.resize((target_w, max(1, int(img.height * ratio))))
        img.putalpha(img.getchannel("A").point(lambda a: int(a * 0.75)))
        img.save(path)
        return

    size = max(18, target_w // 8)
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        font = ImageFont.load_default()
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), WATERMARK_TEXT, font=font)
    w, h = box[2] - box[0] + 16, box[3] - box[1] + 16
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((10, 10), WATERMARK_TEXT, font=font, fill=(0, 0, 0, 150))
    d.text((8, 8), WATERMARK_TEXT, font=font, fill=(255, 255, 255, 200))
    img.save(path)


def add_watermark(video_path, out_dir, video_width):
    if not FFMPEG:
        return video_path
    wm = os.path.join(out_dir, "wm.png")
    make_watermark_png(wm, video_width)
    out = os.path.join(out_dir, "final.mp4")

    # العلامة بتتحرك رايحة جاية (ارتداد) على الشاشة
    x = "'abs(mod(t*70,2*(W-w))-(W-w))'"
    y = "'abs(mod(t*45,2*(H-h))-(H-h))'"
    flt = f"[0:v][1:v]overlay=x={x}:y={y}:format=auto[v]"

    cmd = [
        FFMPEG, "-y", "-i", video_path, "-i", wm,
        "-filter_complex", flt,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    except Exception as e:
        print("WATERMARK ERROR:", repr(e))
    return video_path


# =========================
# DOWNLOAD
# =========================

def download_media(url, media_type, quality, premium):
    temp_dir = tempfile.mkdtemp(prefix="amd_")
    try:
        opts = {**base_opts(), "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s")}

        if media_type == "audio":
            if not FFMPEG:
                raise RuntimeError("FFmpeg غير متاح، لا يمكن تحويل MP3.")
            opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": quality,
                }],
            })
        else:
            cap = 2160 if premium else FREE_MAX_HEIGHT
            h = cap if quality == "best" else min(int(quality), cap)
            opts.update({
                "format": f"bv*[height<={h}]+ba/b[height<={h}]/b",
                "format_sort": ["vcodec:h264", "res", "acodec:aac"],
                "merge_output_format": "mp4",
            })

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next((e for e in info["entries"] if e), info)

        files = [p for p in Path(temp_dir).glob("*") if p.is_file()]
        if media_type == "audio":
            files = [p for p in files if p.suffix == ".mp3"] or files
        if not files:
            raise FileNotFoundError("لم يتم العثور على الملف بعد التحميل.")
        filename = str(max(files, key=lambda p: p.stat().st_size))

        if media_type == "video" and not premium:
            filename = add_watermark(filename, temp_dir, info.get("width") or 720)

        size = os.path.getsize(filename)
        if size > MAX_FILE_SIZE:
            raise RuntimeError(
                f"الملف حجمه {size // (1024 * 1024)}MB وتليجرام يسمح بـ 49MB فقط. "
                "جرب جودة أقل."
            )
        return filename, temp_dir

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# =========================
# HANDLERS
# =========================

def short_error(e):
    text = str(e)
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    low = text.lower()
    if "login" in low or "cookies" in low or "private" in low:
        return "الفيديو خاص أو الموقع بيطلب تسجيل دخول."
    if "unsupported url" in low:
        return "الرابط ده مش مدعوم."
    if "unavailable" in low or "removed" in low or "not found" in low:
        return "الفيديو غير متاح أو اتحذف."
    return text[:300]


JOIN_TEXT = (
    "📢 لازم تشترك في القناة الأول عشان تستخدم البوت.\n\n"
    "اشترك ثم اضغط «✅ تحققت»."
)


def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 اشترك في القناة", url=CHANNEL_URL)],
        [InlineKeyboardButton("✅ تحققت", callback_data="check_sub")],
    ])


async def is_allowed(bot, user_id):
    """مشترك في القناة، أو Premium."""
    if is_premium(user_id):
        return True
    try:
        m = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        if m.status in ("member", "administrator", "creator"):
            return True
        return m.status == "restricted" and getattr(m, "is_member", False)
    except Exception as e:
        # غالباً البوت مش أدمن في القناة — نسمح بدل ما نقفل البوت على الكل
        print("SUB CHECK ERROR:", repr(e))
        return True


async def check_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await is_allowed(context.bot, q.from_user.id):
        await q.answer("لسه مشتركتش في القناة ❌", show_alert=True)
        return
    await q.answer("تمام ✅")
    if context.user_data.get("download_url"):
        kb = [
            [
                InlineKeyboardButton("🎥 فيديو", callback_data="choose_video"),
                InlineKeyboardButton("🎵 MP3", callback_data="choose_audio"),
            ],
            [InlineKeyboardButton("⭐ Premium", callback_data="premium")],
        ]
        await q.edit_message_text(
            "✅ تمام! اختار نوع التحميل:", reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await q.edit_message_text("✅ تمام! ابعت رابط الفيديو.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(context.bot, update.effective_user.id):
        await update.message.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return
    kb = [[InlineKeyboardButton("⭐ Premium", callback_data="premium")]]
    await update.message.reply_text(
        "🎬 Ahmed Media Downloader\n\n"
        "ابعتلي رابط أي فيديو من تيك توك، إنستجرام، فيسبوك، يوتيوب، تويتر "
        "وغيرهم، وأنا أحمله لك.",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /grant <user_id> للأدمن فقط (للتجربة)
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        return
    try:
        uid = int(context.args[0]) if context.args else ADMIN_ID
        activate_premium(uid)
        await update.message.reply_text(f"✅ تم تفعيل Premium للمستخدم {uid}")
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = URL_RE.search(update.message.text or "")
    if not match:
        await update.message.reply_text("❌ ابعت رابط صحيح.")
        return
    context.user_data["download_url"] = match.group(0).rstrip(").,،")

    if not await is_allowed(context.bot, update.effective_user.id):
        await update.message.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return

    kb = [
        [
            InlineKeyboardButton("🎥 فيديو", callback_data="choose_video"),
            InlineKeyboardButton("🎵 MP3", callback_data="choose_audio"),
        ],
        [InlineKeyboardButton("⭐ Premium", callback_data="premium")],
    ]
    await update.message.reply_text(
        "اختار نوع التحميل:", reply_markup=InlineKeyboardMarkup(kb)
    )


async def premium_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if is_premium(q.from_user.id):
        await q.edit_message_text("⭐ أنت مشترك Premium وفعال.")
        return
    kb = [[InlineKeyboardButton(
        f"⭐ اشترك — {PREMIUM_STARS} Stars", callback_data="buy_premium"
    )]]
    await q.edit_message_text(
        "⭐ Ahmed Media Downloader Premium\n\n"
        "• بدون علامة مائية\n"
        "• جودات 1080p وأعلى\n"
        "• مدة الاشتراك 30 يوم\n\n"
        f"السعر: {PREMIUM_STARS} Stars",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def send_premium_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await context.bot.send_invoice(
        chat_id=q.from_user.id,
        title="Ahmed Media Downloader Premium",
        description="اشتراك Premium لمدة 30 يوم.",
        payload=f"premium_30_{q.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Premium 30 Days", PREMIUM_STARS)],
    )


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    activate_premium(update.effective_user.id)
    await update.message.reply_text(
        "🎉 تم تفعيل Premium بنجاح!\n⭐ اشتراكك فعال لمدة 30 يوم."
    )


async def quality_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await is_allowed(context.bot, q.from_user.id):
        await q.edit_message_text(JOIN_TEXT, reply_markup=join_keyboard())
        return

    url = context.user_data.get("download_url")
    if not url:
        await q.edit_message_text("❌ ابعت الرابط مرة ثانية.")
        return

    if q.data == "choose_audio":
        kb = [[
            InlineKeyboardButton("128 kbps", callback_data="dl:a:128"),
            InlineKeyboardButton("192 kbps", callback_data="dl:a:192"),
            InlineKeyboardButton("320 kbps", callback_data="dl:a:320"),
        ]]
        await q.edit_message_text(
            "🎵 اختار جودة الصوت:", reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    await q.edit_message_text("⏳ بفحص الجودات المتاحة...")
    try:
        heights, has_video = await asyncio.to_thread(get_formats, url)
    except Exception as e:
        print("FORMAT ERROR:", repr(e))
        await q.edit_message_text(f"❌ مقدرتش أقرأ الرابط.\n\n{short_error(e)}")
        return

    premium = is_premium(q.from_user.id)
    kb = []
    row = []
    for h in heights:
        locked = h > FREE_MAX_HEIGHT and not premium
        label = f"🔒 {h}p ⭐" if locked else f"{h}p"
        data = "lock" if locked else f"dl:v:{h}"
        row.append(InlineKeyboardButton(label, callback_data=data))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("🔥 أفضل جودة متاحة", callback_data="dl:v:best")])

    note = "" if premium else "\n\n(النسخة المجانية: حتى 720p وعليها علامة مائية)"
    await q.edit_message_text(
        "🎥 اختار الجودة:" + note, reply_markup=InlineKeyboardMarkup(kb)
    )


async def locked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer(
        "الجودة دي لمشتركي Premium ⭐", show_alert=True
    )


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    if len(parts) != 3:
        return
    _, code, value = parts

    if not await is_allowed(context.bot, q.from_user.id):
        await q.edit_message_text(JOIN_TEXT, reply_markup=join_keyboard())
        return

    url = context.user_data.get("download_url")
    if not url:
        await q.edit_message_text("❌ انتهت الجلسة. ابعت الرابط مرة ثانية.")
        return

    premium = is_premium(q.from_user.id)

    if code == "a":
        media_type, quality = "audio", value
    else:
        media_type, quality = "video", value
        if value != "best" and int(value) > FREE_MAX_HEIGHT and not premium:
            await q.answer("الجودة دي لمشتركي Premium ⭐", show_alert=True)
            return

    await q.edit_message_text("⏳ جاري التحميل...")
    temp_dir = None
    try:
        async with LIMIT:
            filename, temp_dir = await asyncio.to_thread(
                download_media, url, media_type, quality, premium
            )

        caption = "🎬 @AhmedMediaDL_bot"
        with open(filename, "rb") as f:
            if media_type == "audio":
                await q.message.reply_audio(
                    audio=f, caption=caption, write_timeout=300, read_timeout=300
                )
            else:
                try:
                    await q.message.reply_video(
                        video=f, caption=caption, supports_streaming=True,
                        write_timeout=300, read_timeout=300,
                    )
                except Exception as send_error:
                    print("VIDEO SEND ERROR:", repr(send_error))
                    f.seek(0)
                    await q.message.reply_document(
                        document=f, caption=caption,
                        write_timeout=300, read_timeout=300,
                    )
        await q.edit_message_text("✅ تم التحميل بنجاح.")

    except Exception as e:
        print("DOWNLOAD ERROR:", repr(e))
        await q.edit_message_text(f"❌ حصل خطأ أثناء التحميل.\n\n{short_error(e)}")
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


async def error_handler(update, context):
    print("BOT ERROR:", repr(context.error))


# =========================
# MAIN
# =========================

def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CallbackQueryHandler(check_sub, pattern=r"^check_sub$"))
    app.add_handler(CallbackQueryHandler(premium_menu, pattern=r"^premium$"))
    app.add_handler(CallbackQueryHandler(send_premium_invoice, pattern=r"^buy_premium$"))
    app.add_handler(CallbackQueryHandler(quality_menu, pattern=r"^choose_(video|audio)$"))
    app.add_handler(CallbackQueryHandler(locked, pattern=r"^lock$"))
    app.add_handler(CallbackQueryHandler(download_callback, pattern=r"^dl:"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_error_handler(error_handler)

    print("FFmpeg:", FFMPEG)
    print("Ahmed Media Downloader is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
