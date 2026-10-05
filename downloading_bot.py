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


# =========================
# SETTINGS
# =========================

TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود.")

DB_FILE = "users.db"

MAX_FILE_SIZE = 49 * 1024 * 1024

PREMIUM_STARS = 100


# =========================
# DATABASE
# =========================

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
        (
            user_id,
            premium_until.isoformat()
        )
    )

    connection.commit()

    connection.close()


# =========================
# YT-DLP OPTIONS
# =========================

def base_yt_options():

    return {

        "quiet": True,

        "no_warnings": False,

        "noplaylist": True,

        "retries": 3,

        "fragment_retries": 3,

        "continuedl": True,

        # مهم جدًا لـ TikTok وبعض المواقع
        "impersonate": "chrome",

    }


# =========================
# GET AVAILABLE QUALITIES
# =========================

def get_formats(url):

    options = {
        **base_yt_options(),

        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=False
        )

    qualities = set()

    allowed = {
        144,
        240,
        360,
        480,
        720,
        1080,
        1440,
        2160
    }

    for fmt in info.get("formats", []):

        height = fmt.get("height")

        if height in allowed:

            qualities.add(height)

    return sorted(qualities)


# =========================
# DOWNLOAD
# =========================

def download_media(
    url,
    media_type,
    quality,
    add_watermark
):

    temp_dir = tempfile.mkdtemp(
        prefix="ahmed_media_"
    )

    try:

        output = os.path.join(
            temp_dir,
            "%(title).80s.%(ext)s"
        )

        # =====================
        # AUDIO
        # =====================

        if media_type == "audio":

            # MP3 يحتاج FFmpeg
            if not shutil.which("ffmpeg"):

                raise RuntimeError(
                    "FFmpeg غير موجود على السيرفر. "
                    "تحميل MP3 يحتاج FFmpeg."
                )

            bitrate = quality.replace(
                "mp3_",
                ""
            )

            options = {

                **base_yt_options(),

                "format": "bestaudio/best",

                "outtmpl": output,

                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",

                        "preferredcodec": "mp3",

                        "preferredquality": bitrate,
                    }
                ],
            }

        # =====================
        # VIDEO
        # =====================

        else:

            if quality == "best":

                video_format = (
                    "best[ext=mp4]/"
                    "best"
                )

            else:

                height = int(quality)

                # الأول نحاول ملف فيديو جاهز
                # بدون دمج FFmpeg
                video_format = (

                    f"best[height<={height}]"
                    f"[ext=mp4]/"

                    f"best[height<={height}]/"

                    "best"
                )

            options = {

                **base_yt_options(),

                "format": video_format,

                "outtmpl": output,
            }

        # =====================
        # DOWNLOAD
        # =====================

        print(
            "DOWNLOAD:",
            url,
            media_type,
            quality
        )

        with yt_dlp.YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            filename = ydl.prepare_filename(
                info
            )

        # =====================
        # MP3 FILE NAME
        # =====================

        if media_type == "audio":

            filename = str(
                Path(filename).with_suffix(
                    ".mp3"
                )
            )

        # =====================
        # FIND FILE IF NEEDED
        # =====================

        if not os.path.exists(filename):

            files = [
                p
                for p in Path(temp_dir).glob("*")
                if p.is_file()
            ]

            if files:

                filename = str(
                    max(
                        files,
                        key=lambda p: p.stat().st_size
                    )
                )

        # =====================
        # FILE CHECK
        # =====================

        if not os.path.exists(filename):

            raise FileNotFoundError(
                "Downloaded file not found."
            )

        file_size = os.path.getsize(
            filename
        )

        print(
            "FILE:",
            filename,
            "SIZE:",
            file_size
        )

        if file_size > MAX_FILE_SIZE:

            raise RuntimeError(
                "الملف أكبر من 49MB."
            )

        # =====================
        # WATERMARK
        # =====================

        # سيتم تفعيل العلامة المائية
        # بعد تثبيت FFmpeg ومعالجة الفيديو.

        return filename, temp_dir

    except Exception:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        raise


# =========================
# START
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# RECEIVE URL
# =========================

async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    url = update.message.text.strip()

    if not url.startswith(
        (
            "http://",
            "https://"
        )
    ):

        await update.message.reply_text(
            "❌ ابعت رابط صحيح."
        )

        return

    context.user_data[
        "download_url"
    ] = url

    keyboard = [

        [

            InlineKeyboardButton(
                "🎥 فيديو",
                callback_data="choose_video"
            ),

            InlineKeyboardButton(
                "🎵 MP3",
                callback_data="choose_audio"
            )

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

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# PREMIUM MENU
# =========================

async def premium_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if is_premium(
        query.from_user.id
    ):

        await query.edit_message_text(

            "⭐ أنت مشترك Premium بالفعل.\n\n"

            "اشتراكك فعال لمدة 30 يوم من تاريخ التفعيل."
        )

        return

    keyboard = [

        [

            InlineKeyboardButton(

                f"⭐ اشترك Premium — "
                f"{PREMIUM_STARS} Stars",

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

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# PREMIUM PAYMENT
# =========================

async def send_premium_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    await context.bot.send_invoice(

        chat_id=query.from_user.id,

        title="Ahmed Media Downloader Premium",

        description="اشتراك Premium لمدة 30 يوم.",

        payload=(
            f"premium_30_"
            f"{query.from_user.id}"
        ),

        currency="XTR",

        prices=[
            {
                "label": "Premium 30 Days",
                "amount": PREMIUM_STARS,
            }
        ],
    )


async def precheckout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.pre_checkout_query

    await query.answer(
        ok=True
    )


async def successful_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    activate_premium(
        user_id
    )

    await update.message.reply_text(

        "🎉 تم تفعيل Premium بنجاح!\n\n"

        "⭐ اشتراكك فعال لمدة 30 يوم."
    )


# =========================
# QUALITY MENU
# =========================

async def quality_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    url = context.user_data.get(
        "download_url"
    )

    if not url:

        await query.edit_message_text(
            "❌ ابعت الرابط مرة ثانية."
        )

        return

    # =====================
    # AUDIO
    # =====================

    if query.data == "choose_audio":

        keyboard = [

            [

                InlineKeyboardButton(
                    "128 kbps",
                    callback_data="dl:a:128"
                ),

                InlineKeyboardButton(
                    "192 kbps",
                    callback_data="dl:a:192"
                )

            ],

            [

                InlineKeyboardButton(
                    "320 kbps",
                    callback_data="dl:a:320"
                )

            ]

        ]

        await query.edit_message_text(

            "🎵 اختار جودة الصوت:",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    # =====================
    # VIDEO
    # =====================

    await query.edit_message_text(
        "⏳ بفحص الجودات المتاحة..."
    )

    try:

        qualities = await asyncio.to_thread(

            get_formats,

            url
        )

        # نحفظ الجودات للمستخدم
        context.user_data[
            "qualities"
        ] = qualities

        # لو الموقع لم يعطِ قائمة
        if not qualities:

            qualities = [360]

            context.user_data[
                "qualities"
            ] = qualities

        keyboard = []

        for index, height in enumerate(
            qualities
        ):

            keyboard.append(

                [

                    InlineKeyboardButton(

                        f"{height}p",

                        # قصير جدًا
                        callback_data=(
                            f"dl:v:{index}"
                        )
                    )

                ]
            )

        keyboard.append(

            [

                InlineKeyboardButton(

                    "🔥 أفضل جودة",

                    callback_data="dl:v:best"
                )

            ]
        )

        await query.edit_message_text(

            "🎥 اختار الجودة:",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    except Exception as e:

        print(
            "FORMAT ERROR:",
            repr(e)
        )

        await query.edit_message_text(

            "❌ لم أستطع قراءة جودات الرابط.\n\n"

            "جرب رابط فيديو آخر."
        )


# =========================
# DOWNLOAD BUTTON
# =========================

async def download_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data.split(":")

    if len(data) != 3:

        await query.edit_message_text(
            "❌ الزر غير صالح."
        )

        return

    if data[0] != "dl":

        return

    media_code = data[1]

    value = data[2]

    url = context.user_data.get(
        "download_url"
    )

    if not url:

        await query.edit_message_text(

            "❌ انتهت جلسة الرابط.\n"
            "ابعت الرابط مرة ثانية."
        )

        return

    # =====================
    # AUDIO
    # =====================

    if media_code == "a":

        media_type = "audio"

        quality = (
            "mp3_" + value
        )

    # =====================
    # VIDEO
    # =====================

    elif media_code == "v":

        media_type = "video"

        if value == "best":

            quality = "best"

        else:

            qualities = context.user_data.get(
                "qualities",
                []
            )

            try:

                index = int(value)

                quality = str(
                    qualities[index]
                )

            except (
                ValueError,
                IndexError
            ):

                await query.edit_message_text(

                    "❌ قائمة الجودة انتهت.\n"
                    "ابعت الرابط مرة ثانية."
                )

                return

    else:

        await query.edit_message_text(
            "❌ اختيار غير صحيح."
        )

        return

    # =====================
    # START DOWNLOAD
    # =====================

    await query.edit_message_text(
        "⏳ جاري التحميل..."
    )

    temp_dir = None

    try:

        filename, temp_dir = (
            await asyncio.to_thread(

                download_media,

                url,

                media_type,

                quality,

                False
            )
        )

        caption = (
            "🎬 Ahmed Media Downloader"
        )

        # =====================
        # SEND
        # =====================

        with open(
            filename,
            "rb"
        ) as file:

            if media_type == "audio":

                await query.message.reply_audio(

                    audio=file,

                    caption=caption
                )

            else:

                try:

                    await query.message.reply_video(

                        video=file,

                        caption=caption,

                        supports_streaming=True
                    )

                except Exception as send_error:

                    print(
                        "VIDEO SEND ERROR:",
                        repr(send_error)
                    )

                    file.seek(0)

                    await query.message.reply_document(

                        document=file,

                        caption=caption
                    )

        await query.message.reply_text(
            "✅ تم التحميل بنجاح."
        )

    except Exception as e:

        # ده مهم جدًا عشان نعرف السبب الحقيقي
        print(
            "DOWNLOAD ERROR:",
            repr(e)
        )

        await query.message.reply_text(

            "❌ حصل خطأ أثناء التحميل.\n\n"

            "السبب التقني:\n"

            f"{str(e)[:700]}"
        )

    finally:

        if temp_dir:

            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update,
    context
):

    print(
        "BOT ERROR:",
        repr(context.error)
    )


# =========================
# MAIN
# =========================

def main():

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    # START
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # PREMIUM
    app.add_handler(
        CallbackQueryHandler(

            premium_menu,

            pattern=r"^premium$"
        )
    )

    # BUY PREMIUM
    app.add_handler(
        CallbackQueryHandler(

            send_premium_invoice,

            pattern=r"^buy_premium$"
        )
    )

    # VIDEO / AUDIO
    app.add_handler(
        CallbackQueryHandler(

            quality_menu,

            pattern=r"^choose_(video|audio)$"
        )
    )

    # DOWNLOAD
    app.add_handler(
        CallbackQueryHandler(

            download_callback,

            pattern=r"^dl:"
        )
    )

    # PAYMENT
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

    # URL
    app.add_handler(
        MessageHandler(

            filters.TEXT
            & ~filters.COMMAND,

            handle_url
        )
    )

    # ERRORS
    app.add_error_handler(
        error_handler
    )

    print(
        "Ahmed Media Downloader is running..."
    )

    app.run_polling()


if __name__ == "__main__":

    main()
