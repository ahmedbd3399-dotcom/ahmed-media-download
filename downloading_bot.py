import os
import asyncio
import tempfile
import shutil
from pathlib import Path

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود.")

MAX_FILE_SIZE = 49 * 1024 * 1024


def download_media(url, media_type, quality):
    temp_dir = tempfile.mkdtemp()

    try:
        output = os.path.join(temp_dir, "%(title)s.%(ext)s")

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
            height = quality.replace("p", "")

            if height == "best":
                video_format = "bestvideo+bestaudio/best"
            else:
                video_format = (
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]"
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
            filename = os.path.splitext(filename)[0] + ".mp3"

        if not os.path.exists(filename):
            files = list(Path(temp_dir).glob("*"))
            if files:
                filename = str(files[0])

        if not os.path.exists(filename):
            raise FileNotFoundError("Downloaded file not found")

        if os.path.getsize(filename) > MAX_FILE_SIZE:
            raise ValueError("File is larger than Telegram limit")

        return filename, temp_dir

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Ahmed Media Downloader\n\n"
        "ابعتلي رابط الفيديو أو المنشور اللي عايز تحمله."
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("❌ ابعت رابط صحيح.")
        return

    # حفظ الرابط بدل وضعه داخل callback_data
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
        ]
    ]

    await update.message.reply_text(
        "اختار نوع التحميل:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def quality_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    url = context.user_data.get("download_url")

    if not url:
        await query.edit_message_text(
            "❌ الرابط انتهت صلاحيته.\n"
            "ابعت الرابط مرة تانية."
        )
        return

    if query.data == "choose_video":

        keyboard = [
            [
                InlineKeyboardButton(
                    "360p",
                    callback_data="download|video|360p"
                ),
                InlineKeyboardButton(
                    "480p",
                    callback_data="download|video|480p"
                ),
            ],
            [
                InlineKeyboardButton(
                    "720p",
                    callback_data="download|video|720p"
                ),
                InlineKeyboardButton(
                    "1080p",
                    callback_data="download|video|1080p"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔥 أفضل جودة",
                    callback_data="download|video|best"
                )
            ],
        ]

        await query.edit_message_text(
            "🎥 اختار جودة الفيديو:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data == "choose_audio":

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
            ],
        ]

        await query.edit_message_text(
            "🎵 اختار جودة الصوت:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")

    if len(parts) != 3:
        await query.message.reply_text("❌ حدث خطأ في الطلب.")
        return

    _, media_type, quality = parts

    url = context.user_data.get("download_url")

    if not url:
        await query.message.reply_text(
            "❌ الرابط انتهت صلاحيته.\n"
            "ابعت الرابط مرة تانية."
        )
        return

    await query.edit_message_text(
        "⏳ جاري التحميل...\n"
        "ممكن يستغرق بعض الوقت."
    )

    temp_dir = None

    try:
        filename, temp_dir = await asyncio.to_thread(
            download_media,
            url,
            media_type,
            quality,
        )

        with open(filename, "rb") as file:

            if media_type == "audio":
                await query.message.reply_audio(
                    audio=file,
                    caption="🎵 Ahmed Media Downloader"
                )
            else:
                await query.message.reply_video(
                    video=file,
                    caption="🎬 Ahmed Media Downloader"
                )

        await query.message.reply_text(
            "✅ تم التحميل بنجاح."
        )

    except Exception as e:

        print("DOWNLOAD ERROR:", repr(e))

        await query.message.reply_text(
            "❌ حصل خطأ أثناء التحميل.\n\n"
            "جرب رابط تاني أو جودة أقل."
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
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_url
        )
    )

    print("Downloading Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
