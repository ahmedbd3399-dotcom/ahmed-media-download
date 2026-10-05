import os
import sqlite3
import asyncio
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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

TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود.")

DB_FILE = "users.db"
MAX_FILE_SIZE = 49 * 1024 * 1024

# غيّر السعر لاحقًا إذا أردت
PREMIUM_STARS = 100


def db():
    connection = sqlite3.connect(DB_FILE)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            premium_until TEXT
        )
    """)
    connection.commit()
    return connection


def is_premium(user_id):
    connection = db()
    row = connection.execute(
        "SELECT premium_until FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    connection.close()

    if not row or not row[0]:
        return False

    try:
        until = datetime.fromisoformat(row[0])
        return until > datetime.now(timezone.utc)
    except Exception:
        return False


def activate_premium(user_id):
    connection = db()

    current = connection.execute(
        "SELECT premium_until FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    now = datetime.now(timezone.utc)

    if current and current[0]:
        try:
            old_until = datetime.fromisoformat(current[0])
            start = max(now, old_until)
        except Exception:
            start = now
    else:
        start = now

    premium_until = start + timedelta(days=30)

    connection.execute(
        """
        INSERT INTO users (user_id, premium_until)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET premium_until = excluded.premium_until
        """,
        (user_id, premium_until.isoformat())
    )

    connection.commit()
    connection.close()


def get_formats(url):
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = []

    for fmt in info.get("formats", []):
        height = fmt.get("height")
        if not height:
            continue

        if height not in [144, 240, 360, 480, 720, 1080, 1440, 2160]:
            continue

        if height not in formats:
            formats.append(height)

    formats.sort()

    return formats


def download_media(url, media_type, quality, add_watermark):
    temp_dir = tempfile.mkdtemp()

    try:
        output = os.path.join(
            temp_dir,
            "%(title)s.%(ext)s"
        )

        if media_type == "audio":

            bitrate = quality.replace("mp3_", "")

            options = {
                "format": "bestaudio/best",
                "outtmpl": output,
                "noplaylist": True,
                "quiet": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": bitrate,
                    }
                ],
            }

        else:

            if quality == "best":
                video_format = "bestvideo+bestaudio/best"
            else:
                video_format = (
                    f"bestvideo[height<={quality}]"
                    f"+bestaudio/"
                    f"best[height<={quality}]"
                )

            options = {
                "format": video_format,
                "outtmpl": output,
                "noplaylist": True,
                "quiet": True,
                "merge_output_format": "mp4",
            }

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        if media_type == "audio":
            filename = (
                os.path.splitext(filename)[0] + ".mp3"
            )

        if not os.path.exists(filename):
            files = list(Path(temp_dir).glob("*"))
            if files:
                filename = str(files[0])

        if not os.path.exists(filename):
            raise FileNotFoundError(
                "Downloaded file not found"
            )

        if os.path.getsize(filename) > MAX_FILE_SIZE:
            raise ValueError(
                "File أكبر من الحد المسموح به في Telegram."
            )

        return filename, temp_dir

    except Exception:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )
        raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        [
            InlineKeyboardButton(
                "⭐ Premium",
                callback_data="premium"
            )
        ]
    ]

    await update.message.reply_text(
        "🎬 Ahmed Media Downloader\n\n"
        "ابعت رابط الفيديو أو المنشور هنا.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = update.message.text.strip()

    if not url.startswith(("http://", "https://")):
        await update.message.reply_text(
            "❌ ابعت رابط صحيح."
        )
        return

    context.user_data["download_url"] = url

    keyboard = [
        [
            InlineKeyboardButton(
                "🎥 فيديو",
                callback_data="choose_video"
            ),
            InlineKeyboardButton(
                "🎵 MP3",
                callback_data="choose_audio"
            ),
        ],
        [
            InlineKeyboardButton(
                "⭐ Premium",
                callback_data="premium"
            )
        ]
    ]

    await update.message.reply_text(
        "اختار نوع التحميل:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def premium_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if is_premium(query.from_user.id):

        await query.edit_message_text(
            "⭐ أنت مشترك Premium بالفعل.\n"
            "اشتراكك فعال لمدة 30 يوم من تاريخ التفعيل."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                f"⭐ اشترك Premium — {PREMIUM_STARS} Stars",
                callback_data="buy_premium"
            )
        ]
    ]

    await query.edit_message_text(
        "⭐ Ahmed Media Downloader Premium\n\n"
        "المميزات:\n"
        "• بدون علامتنا المائية\n"
        "• مزايا Premium\n"
        "• الاشتراك لمدة 30 يوم\n\n"
        f"السعر: {PREMIUM_STARS} Stars",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def send_premium_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title="Ahmed Media Downloader Premium",
        description="اشتراك Premium لمدة 30 يوم.",
        payload=f"premium_30_{query.from_user.id}",
        currency="XTR",
        prices=[
            {
                "label": "Premium 30 Days",
                "amount": PREMIUM_STARS,
            }
        ],
    )


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.pre_checkout_query

    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    activate_premium(user_id)

    await update.message.reply_text(
        "🎉 تم تفعيل Premium بنجاح!\n\n"
        "⭐ اشتراكك فعال لمدة 30 يوم."
    )


async def quality_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    url = context.user_data.get("download_url")

    if not url:
        await query.edit_message_text(
            "❌ ابعت الرابط مرة ثانية."
        )
        return

    if query.data == "choose_audio":

        keyboard = [
            [
                InlineKeyboardButton(
                    "128 kbps",
                    callback_data="download|audio|mp3_128"
                ),
                InlineKeyboardButton(
                    "192 kbps",
                    callback_data="download|audio|mp3_192"
                ),
            ],
            [
                InlineKeyboardButton(
                    "320 kbps",
                    callback_data="download|audio|mp3_320"
                )
            ]
        ]

        await query.edit_message_text(
            "🎵 اختار جودة الصوت:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    await query.edit_message_text(
        "⏳ بفحص الجودات المتاحة..."
    )

    try:
        qualities = await asyncio.to_thread(
            get_formats,
            url
        )

        keyboard = []

        for height in qualities:
            keyboard.append([
                InlineKeyboardButton(
                    f"{height}p",
                    callback_data=f"download|video|{height}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "🔥 أفضل جودة",
                callback_data="download|video|best"
            )
        ])

        await query.edit_message_text(
            "🎥 اختار الجودة:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:

        print("FORMAT ERROR:", repr(e))

        await query.edit_message_text(
            "❌ لم أستطع قراءة جودات الرابط.\n"
            "جرب رابط فيديو آخر."
        )


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")

    if len(parts) != 3:
        return

    _, media_type, quality = parts

    url = context.user_data.get("download_url")

    if not url:
        await query.message.reply_text(
            "❌ ابعت الرابط مرة ثانية."
        )
        return

    premium = is_premium(query.from_user.id)

    await query.edit_message_text(
        "⏳ جاري التحميل..."
    )

    temp_dir = None

    try:

        filename, temp_dir = await asyncio.to_thread(
            download_media,
            url,
            media_type,
            quality,
            not premium
        )

        with open(filename, "rb") as file:

            if media_type == "audio":

                await query.message.reply_audio(
                    audio=file,
                    caption=(
                        "🎵 Ahmed Media Downloader"
                        + (
                            "\n⭐ Premium"
                            if premium
                            else ""
                        )
                    )
                )

            else:

                await query.message.reply_video(
                    video=file,
                    caption=(
                        "🎬 Ahmed Media Downloader"
                        + (
                            "\n⭐ Premium"
                            if premium
                            else ""
                        )
                    )
                )

        await query.message.reply_text(
            "✅ تم التحميل بنجاح."
        )

    except Exception as e:

        print("DOWNLOAD ERROR:", repr(e))

        await query.message.reply_text(
            "❌ حصل خطأ أثناء التحميل.\n"
            "جرب جودة أقل أو رابطًا آخر."
        )

    finally:

        if temp_dir:
            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )


def main():

    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(
            premium_menu,
            pattern=r"^premium$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            send_premium_invoice,
            pattern=r"^buy_premium$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            quality_menu,
            pattern=r"^choose_(video|audio)$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            download_callback,
            pattern=r"^download\|"
        )
    )

    app.add_handler(
        PreCheckoutQueryHandler(
            precheckout
        )
    )

    app.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_url
        )
    )

    print("Downloading Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
