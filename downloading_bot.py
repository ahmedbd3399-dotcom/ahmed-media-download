import os
import re
import shutil
import sqlite3
import asyncio
import tempfile
import threading
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import yt_dlp
from PIL import Image, ImageDraw, ImageFont

from telegram import (
    Update,
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    TypeHandler,
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

try:
    from dotenv import load_dotenv
    load_dotenv()  # يقرأ التوكن من ملف .env لو موجود
except Exception:
    pass

TOKEN = (
    os.environ.get("BOT_TOKEN")
    or os.environ.get("TOKEN")
    or os.environ.get("TELEGRAM_BOT_TOKEN")
)
if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN غير موجود. حطه في ملف .env أو في Variables على السيرفر."
    )

OWNER_ID = 7219900342  # صاحب البوت: Premium دائم
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0) or OWNER_ID
WATERMARK_TEXT = "@AhmedMediaDL_bot"
WATERMARK_LOGO = "watermark.png"   # لو حطيت اللوجو هنا هيستخدمه بدل النص
COOKIES_FILE = "cookies.txt"       # اختياري (إنستجرام / فيسبوك)
CHANNEL_USERNAME = "@AhmedMediaDL"  # قناة الاشتراك الإجباري
CHANNEL_URL = "https://t.me/AhmedMediaDL"

DB_FILE = "users.db"
MAINTENANCE_TEXT = "🛠 البوت متوقف مؤقتاً للصيانة.\nجرّب مرة تانية بعد شوية. شكراً لصبرك 🙏"
MAINTENANCE_FLAG = "maintenance.flag"  # وجود الملف = وضع الصيانة شغال
MAX_FILE_SIZE = 49 * 1024 * 1024
TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # حد تليجرام لتحميل الملفات اللي المستخدم يبعتها للبوت
# خطط الاشتراك: الكود -> (عدد الأيام, السعر بالنجوم, الاسم)
PLANS = {
    "m1": (30, 30, "شهر"),
    "m3": (90, 70, "3 شهور"),
    "y1": (365, 150, "سنة"),
}
FREE_MAX_HEIGHT = 720

TTS_MAX_FREE = 500       # أقصى عدد حروف للنص (مجاني)
TTS_MAX_PREMIUM = 3000   # أقصى عدد حروف للنص (Premium)

# الأصوات المتاحة لتحويل النص لصوت (edge-tts)
VOICES = {
    "egf": ("🇪🇬 صوت مصري (أنثى)", "ar-EG-SalmaNeural"),
    "egm": ("🇪🇬 صوت مصري (ذكر)", "ar-EG-ShakirNeural"),
    "sam": ("🇸🇦 صوت سعودي (ذكر)", "ar-SA-HamedNeural"),
    "sag": ("🇸🇦 صوت سعودي (أنثى)", "ar-SA-ZariyahNeural"),
    "uae": ("🇦🇪 صوت إماراتي (ذكر)", "ar-AE-HamdanNeural"),
    "enf": ("🇺🇸 English (female)", "en-US-AriaNeural"),
    "enm": ("🇬🇧 English (male)", "en-GB-RyanNeural"),
}

# أدوات الصوت/الفيديو اللي لمشتركي Premium فقط (عدّل القايمة زي ما تحب)
PREMIUM_ACTIONS = {"cut", "compress"}
CUT_MAX_SECONDS = 600     # أقصى مدة للمقطع المقصوص
SPEEDS = ["0.75", "1.25", "1.5", "2"]
TTS_RATES = [("🐢 بطيء", "-25"), ("🙂 عادي", "0"), ("🐇 سريع", "+25"), ("⚡ أسرع", "+50")]


class UserError(Exception):
    """خطأ رسالته مفهومة للمستخدم (مش عطل) — بنعرضه زي ما هو."""


URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
LIMIT = asyncio.Semaphore(2)  # عمليتين تحميل كحد أقصى مع بعض


# =========================
# KEEP ALIVE (لموقع bot keep)
# =========================

class _PingHandler(BaseHTTPRequestHandler):
    def _ok(self, body=True):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if body:
            self.wfile.write(b"Bot is running")

    def do_GET(self):
        self._ok()

    def do_HEAD(self):
        self._ok(body=False)

    def log_message(self, *args):
        pass


def start_keep_alive():
    port = int(os.environ.get("PORT", "8080"))

    def run():
        try:
            HTTPServer(("0.0.0.0", port), _PingHandler).serve_forever()
        except Exception as e:
            print("KEEP ALIVE SERVER ERROR:", repr(e))

    threading.Thread(target=run, daemon=True).start()
    print(f"Keep-alive server on port {port}")


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
    if user_id == OWNER_ID:
        return True
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


def premium_until(user_id):
    """تاريخ انتهاء الاشتراك (datetime) أو None."""
    con = db()
    row = con.execute(
        "SELECT premium_until FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    con.close()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except Exception:
        return None


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
            raise UserError("لم يتم العثور على الملف بعد التحميل.")
        filename = str(max(files, key=lambda p: p.stat().st_size))

        if media_type == "video" and not premium:
            filename = add_watermark(filename, temp_dir, info.get("width") or 720)

        size = os.path.getsize(filename)
        if size > MAX_FILE_SIZE:
            raise UserError(
                f"الملف حجمه {size // (1024 * 1024)}MB وتليجرام يسمح بـ 49MB فقط. "
                "جرب جودة أقل."
            )
        return filename, temp_dir

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# =========================
# CONVERT FILE -> MP3
# =========================

def convert_to_mp3(src, out):
    if not FFMPEG:
        raise RuntimeError("FFmpeg غير متاح، لا يمكن التحويل.")
    cmd = [
        FFMPEG, "-y", "-i", src,
        "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
        out,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", "ignore").lower()
        if "does not contain any stream" in err or "output file is empty" in err:
            raise UserError("الملف ده مفيهوش صوت.")
        raise UserError("مقدرتش أحول الملف ده، جرب ملف تاني.")
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise UserError("الملف ده مفيهوش صوت.")


# =========================
# MEDIA TOOLS (صوت / فيديو)
# =========================

MEDIA_ACTIONS = {
    # action: (label, video_only)
    "mp3": ("🎵 MP3", False),
    "voice": ("🎤 فويس", False),
    "speed": ("⏩ سرعة الصوت", False),
    "volume": ("🔊 تقوية الصوت", False),
    "cut": ("✂️ قص مقطع ⭐", False),
    "frame": ("🖼 صورة من الفيديو", True),
    "gif": ("🎞 مقطع متحرك", True),
    "compress": ("🗜 ضغط الفيديو ⭐", True),
}


def _ff(args, timeout=900):
    if not FFMPEG:
        raise RuntimeError("FFmpeg غير متاح.")
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise UserError("العملية أخدت وقت طويل، جرب ملف أقصر.")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", "ignore").lower()
        if "does not contain any stream" in err or "matches no streams" in err \
                or "output file is empty" in err or "stream specifier" in err:
            raise UserError("الملف ده مفيهوش الجزء المطلوب (صوت أو فيديو) للعملية دي.")
        print("FFMPEG ERROR:", err[-300:])
        raise UserError("مقدرتش أعالج الملف ده، جرب ملف تاني.")


def _check_out(out):
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise UserError("العملية ما طلعتش نتيجة، جرب ملف تاني.")
    if os.path.getsize(out) > MAX_FILE_SIZE:
        raise UserError("الناتج أكبر من حد تليجرام (49MB).")


def parse_time(t):
    t = t.strip().replace("،", ".").replace(",", ".")
    parts = t.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def parse_cut(text):
    """'0:10-0:45' أو '10 الى 45' -> (10.0, 45.0)"""
    bits = re.split(r"\s*(?:-|–|—|to|الى|إلى)\s*", text.strip(), maxsplit=1)
    if len(bits) != 2:
        raise ValueError
    start, end = parse_time(bits[0]), parse_time(bits[1])
    if start < 0 or end <= start:
        raise ValueError
    return start, end


def process_media(src, temp_dir, action, param, is_video):
    """يرجع (مسار الناتج, نوعه): audio / voice / video / animation / photo"""
    if action == "mp3":
        out = os.path.join(temp_dir, "audio.mp3")
        convert_to_mp3(src, out)
        _check_out(out)
        return out, "audio"

    if action == "voice":
        out = os.path.join(temp_dir, "voice.ogg")
        _ff(["-i", src, "-vn", "-c:a", "libopus", "-b:a", "48k", "-ac", "1", out])
        _check_out(out)
        return out, "voice"

    if action == "speed":
        if param not in SPEEDS:
            raise UserError("سرعة غير مدعومة.")
        out = os.path.join(temp_dir, "speed.mp3")
        _ff(["-i", src, "-vn", "-filter:a", f"atempo={param}",
             "-c:a", "libmp3lame", "-b:a", "192k", out])
        _check_out(out)
        return out, "audio"

    if action == "volume":
        out = os.path.join(temp_dir, "louder.mp3")
        _ff(["-i", src, "-vn", "-filter:a", "loudnorm=I=-14:TP=-1.5:LRA=11",
             "-c:a", "libmp3lame", "-b:a", "192k", out])
        _check_out(out)
        return out, "audio"

    if action == "cut":
        start, end = param
        dur = min(end - start, CUT_MAX_SECONDS)
        if is_video:
            out = os.path.join(temp_dir, "cut.mp4")
            _ff(["-ss", str(start), "-t", str(dur), "-i", src,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                 "-movflags", "+faststart", out])
            _check_out(out)
            return out, "video"
        out = os.path.join(temp_dir, "cut.mp3")
        _ff(["-ss", str(start), "-t", str(dur), "-i", src, "-vn",
             "-c:a", "libmp3lame", "-b:a", "192k", out])
        _check_out(out)
        return out, "audio"

    if action == "frame":
        out = os.path.join(temp_dir, "frame.jpg")
        try:
            _ff(["-ss", "1", "-i", src, "-frames:v", "1", "-q:v", "2", out])
            _check_out(out)
        except UserError:
            _ff(["-i", src, "-frames:v", "1", "-q:v", "2", out])
            _check_out(out)
        return out, "photo"

    if action == "gif":
        out = os.path.join(temp_dir, "clip.mp4")
        _ff(["-i", src, "-t", "8", "-an",
             "-vf", "fps=15,scale='min(480,iw)':-2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out])
        _check_out(out)
        return out, "animation"

    if action == "compress":
        out = os.path.join(temp_dir, "small.mp4")
        _ff(["-i", src, "-vf", "scale='min(854,iw)':-2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
             "-movflags", "+faststart", out])
        _check_out(out)
        return out, "video"

    raise UserError("عملية غير مدعومة.")


def fetch_extra(url, kind):
    """kind: thumb (صورة الغلاف) أو subs (الترجمة). يرجع (قايمة ملفات, temp_dir)"""
    temp_dir = tempfile.mkdtemp(prefix="amx_")
    try:
        opts = {**base_opts(), "skip_download": True,
                "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s")}
        if kind == "thumb":
            opts["writethumbnail"] = True
        else:
            opts.update({
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["ar", "en"],
                "subtitlesformat": "srt/vtt/best",
            })
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)

        if kind == "thumb":
            imgs = [p for p in Path(temp_dir).glob("*")
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
            if not imgs:
                raise UserError("الرابط ده مفيهوش صورة غلاف.")
            jpg = os.path.join(temp_dir, "cover.jpg")
            Image.open(imgs[0]).convert("RGB").save(jpg, "JPEG", quality=92)
            return [jpg], temp_dir

        subs = sorted(p for p in Path(temp_dir).glob("*")
                      if p.suffix.lower() in (".srt", ".vtt"))
        if not subs:
            raise UserError("الفيديو ده مفيهوش ترجمة (عربي أو إنجليزي).")
        return [str(p) for p in subs[:2]], temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# =========================
# TEXT -> SPEECH
# =========================

async def tts_to_file(text, voice, path, rate="0"):
    import edge_tts
    r = int(rate)
    await edge_tts.Communicate(text, voice, rate=f"{r:+d}%").save(path)


# =========================
# HANDLERS
# =========================

def short_error(e):
    if isinstance(e, UserError):
        return str(e)
    text = str(e)
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    low = text.lower()
    if "login" in low or "cookies" in low or "private" in low:
        return "الفيديو خاص أو الموقع بيطلب تسجيل دخول."
    if "unsupported url" in low:
        return "الرابط ده مش مدعوم."
    if "unavailable" in low or "removed" in low or "not found" in low:
        return "الفيديو غير متاح أو اتحذف."
    print("UNEXPECTED ERROR:", text[:300])
    return MAINTENANCE_TEXT


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
            [
                InlineKeyboardButton("🖼 صورة الغلاف", callback_data="extra:thumb"),
                InlineKeyboardButton("📝 الترجمة", callback_data="extra:subs"),
            ],
            [InlineKeyboardButton("⭐ Premium", callback_data="premium")],
        ]
        await q.edit_message_text(
            "✅ تمام! اختار نوع التحميل:", reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await q.edit_message_text(START_TEXT, reply_markup=main_menu_kb())


START_TEXT = (
    "🎬 Ahmed Media Downloader\n\n"
    "اختار اللي عايزه من القايمة، أو ابعت مباشرة:\n"
    "• رابط فيديو ← أحمله لك\n"
    "• فيديو أو ملف صوت ← أدوات (MP3، فويس، قص، سرعة، ضغط...)\n"
    "• نص عادي ← أحوله لصوت"
)

MENU_HELP = {
    "dl": "🎥 *تحميل من رابط*\n\nابعت رابط الفيديو (تيك توك، إنستجرام، فيسبوك، يوتيوب، تويتر...) وأنا أحمله لك.",
    "mp3": "🎛 *أدوات الصوت والفيديو*\n\nابعت أي فيديو أو ملف صوت أو فويس (حتى 20MB) وهتختار:\n• 🎵 MP3 • 🎤 فويس • ⏩ سرعة الصوت • 🔊 تقوية الصوت\n• ✂️ قص مقطع ⭐ • 🖼 صورة من الفيديو • 🎞 مقطع متحرك • 🗜 ضغط الفيديو ⭐\n\nلو الفيديو أكبر، ابعت رابطه بدل الملف.",
    "tts": "🔊 *نص ← صوت*\n\nاكتب أو ابعت أي نص (حتى 500 حرف) واختار الصوت والسرعة وأرجعه لك MP3.",
}


def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 تحميل من رابط", callback_data="menu:dl")],
        [InlineKeyboardButton("🎛 أدوات الصوت والفيديو", callback_data="menu:mp3")],
        [InlineKeyboardButton("🔊 نص ← صوت", callback_data="menu:tts")],
        [InlineKeyboardButton("⭐ Premium", callback_data="premium")],
    ])


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data.split(":", 1)[1]
    if key == "home":
        await q.edit_message_text(START_TEXT, reply_markup=main_menu_kb())
        return
    text = MENU_HELP.get(key)
    if not text:
        return
    back = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]]
    )
    await q.edit_message_text(text, reply_markup=back, parse_mode="Markdown")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed(context.bot, update.effective_user.id):
        await update.message.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return
    await update.message.reply_text(START_TEXT, reply_markup=main_menu_kb())


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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لو الرسالة فيها رابط -> تحميل. لو نص عادي -> تحويل لصوت."""
    text = (update.message.text or "").strip()
    match = URL_RE.search(text)

    # لو مستني وقت القص من المستخدم
    if context.user_data.get("await_cut") and not match:
        try:
            cut = parse_cut(text)
        except ValueError:
            await update.message.reply_text(
                "❌ الصيغة مش مفهومة. اكتب البداية والنهاية كده:\n0:10-0:45\nأو: 10-45 (بالثواني)"
            )
            return
        context.user_data["await_cut"] = False
        status = await update.message.reply_text("⏳ جاري القص...")
        await run_media_job(status, context, update.effective_user.id, "cut", cut)
        return
    context.user_data["await_cut"] = False

    if not await is_allowed(context.bot, update.effective_user.id):
        if match:
            context.user_data["download_url"] = match.group(0).rstrip(").,،")
        await update.message.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return

    if match:
        context.user_data["download_url"] = match.group(0).rstrip(").,،")
        kb = [
            [
                InlineKeyboardButton("🎥 فيديو", callback_data="choose_video"),
                InlineKeyboardButton("🎵 MP3", callback_data="choose_audio"),
            ],
            [
                InlineKeyboardButton("🖼 صورة الغلاف", callback_data="extra:thumb"),
                InlineKeyboardButton("📝 الترجمة", callback_data="extra:subs"),
            ],
            [InlineKeyboardButton("⭐ Premium", callback_data="premium")],
        ]
        await update.message.reply_text(
            "اختار نوع التحميل:", reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    # نص عادي -> تحويل لصوت
    limit = TTS_MAX_PREMIUM if is_premium(update.effective_user.id) else TTS_MAX_FREE
    if len(text) > limit:
        await update.message.reply_text(
            f"❌ النص طويل ({len(text)} حرف). الحد الأقصى {limit} حرف."
            + ("" if limit == TTS_MAX_PREMIUM else "\n⭐ مشتركي Premium لهم حد أكبر.")
        )
        return

    context.user_data["tts_text"] = text
    kb = [
        [InlineKeyboardButton(label, callback_data=f"tts:{key}")]
        for key, (label, _) in VOICES.items()
    ]
    await update.message.reply_text(
        "🔊 اختار الصوت اللي عايز أقرأ به النص:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def tts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    key = parts[1] if len(parts) > 1 else ""
    if key not in VOICES:
        return

    if not await is_allowed(context.bot, q.from_user.id):
        await q.edit_message_text(JOIN_TEXT, reply_markup=join_keyboard())
        return

    text = context.user_data.get("tts_text")
    if not text:
        await q.edit_message_text("❌ انتهت الجلسة. ابعت النص مرة ثانية.")
        return

    # الخطوة التانية: اختيار سرعة القراءة
    if len(parts) == 2:
        kb = [[InlineKeyboardButton(label, callback_data=f"tts:{key}:{rate}")]
              for label, rate in TTS_RATES]
        await q.edit_message_text("⏩ اختار سرعة القراءة:", reply_markup=InlineKeyboardMarkup(kb))
        return

    rate = parts[2]
    if rate not in {r for _, r in TTS_RATES}:
        return

    label, voice = VOICES[key]
    await q.edit_message_text("⏳ جاري تحويل النص لصوت...")
    temp_dir = tempfile.mkdtemp(prefix="amt_")
    try:
        out = os.path.join(temp_dir, "speech.mp3")
        async with LIMIT:
            await tts_to_file(text, voice, out, rate)
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise UserError("معرفتش أطلع صوت من النص ده.")
        with open(out, "rb") as f:
            await q.message.reply_audio(
                audio=f,
                title="Text to Speech",
                caption="🔊 @AhmedMediaDL_bot",
                write_timeout=300,
                read_timeout=300,
            )
        await q.edit_message_text("✅ تم تحويل النص لصوت.")
    except UserError as e:
        await q.edit_message_text(f"❌ {e}")
    except ModuleNotFoundError:
        print("TTS ERROR: edge-tts غير مثبت")
        await q.edit_message_text(MAINTENANCE_TEXT)
    except Exception as e:
        print("TTS ERROR:", repr(e))
        await q.edit_message_text(MAINTENANCE_TEXT)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def media_menu_kb(is_video):
    rows, row = [], []
    for action, (label, video_only) in MEDIA_ACTIONS.items():
        if video_only and not is_video:
            continue
        row.append(InlineKeyboardButton(label, callback_data=f"m:{action}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def handle_file_to_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أي فيديو/صوت/فويس يتبعت للبوت -> قايمة أدوات."""
    msg = update.message
    if not await is_allowed(context.bot, update.effective_user.id):
        await msg.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return

    att = msg.video or msg.audio or msg.voice or msg.video_note or msg.document
    if not att:
        return

    if att.file_size and att.file_size > TG_DOWNLOAD_LIMIT:
        await msg.reply_text(
            "❌ الملف أكبر من 20MB، وتليجرام مابيسمحش للبوتات تحمل ملفات أكبر من كده.\n"
            "جرب ملف أصغر، أو ابعت رابط الفيديو بدل الملف."
        )
        return

    mime = (getattr(att, "mime_type", "") or "").lower()
    is_video = bool(msg.video or msg.video_note or mime.startswith("video/"))
    context.user_data["await_cut"] = False
    context.user_data["media"] = {
        "file_id": att.file_id,
        "is_video": is_video,
        "name": getattr(att, "file_name", None),
    }
    await msg.reply_text(
        "🎛 اختار اللي عايز تعمله في الملف:\n(⭐ = لمشتركي Premium)",
        reply_markup=media_menu_kb(is_video),
    )


async def run_media_job(status, context, user_id, action, param=None):
    """ينزّل الملف المحفوظ، يطبّق الأداة، ويبعت الناتج."""
    media = context.user_data.get("media")
    if not media:
        await status.edit_text("❌ انتهت الجلسة. ابعت الملف مرة ثانية.")
        return

    temp_dir = tempfile.mkdtemp(prefix="amc_")
    try:
        tg_file = await context.bot.get_file(media["file_id"])
        src = os.path.join(temp_dir, "input")
        await tg_file.download_to_drive(src)

        async with LIMIT:
            out, kind = await asyncio.to_thread(
                process_media, src, temp_dir, action, param, media["is_video"]
            )

        title = None
        if media.get("name"):
            title = os.path.splitext(media["name"])[0][:60]
        cap = "@AhmedMediaDL_bot"
        opts = dict(write_timeout=300, read_timeout=300)
        with open(out, "rb") as f:
            if kind == "audio":
                await status.reply_audio(audio=f, title=title, caption="🎵 " + cap, **opts)
            elif kind == "voice":
                await status.reply_voice(voice=f, caption="🎤 " + cap, **opts)
            elif kind == "photo":
                await status.reply_photo(photo=f, caption="🖼 " + cap, **opts)
            elif kind == "animation":
                await status.reply_animation(animation=f, caption="🎞 " + cap, **opts)
            else:
                try:
                    await status.reply_video(video=f, caption="🎬 " + cap,
                                             supports_streaming=True, **opts)
                except Exception as send_error:
                    print("VIDEO SEND ERROR:", repr(send_error))
                    f.seek(0)
                    await status.reply_document(document=f, caption="🎬 " + cap, **opts)
        await status.edit_text("✅ تم.")
    except UserError as e:
        await status.edit_text(f"❌ {e}")
    except Exception as e:
        print("MEDIA JOB ERROR:", action, repr(e))
        await status.edit_text(MAINTENANCE_TEXT)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def media_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action not in MEDIA_ACTIONS:
        await q.answer()
        return

    if not await is_allowed(context.bot, q.from_user.id):
        await q.answer()
        await q.edit_message_text(JOIN_TEXT, reply_markup=join_keyboard())
        return

    media = context.user_data.get("media")
    if not media:
        await q.answer()
        await q.edit_message_text("❌ انتهت الجلسة. ابعت الملف مرة ثانية.")
        return

    if action in PREMIUM_ACTIONS and not is_premium(q.from_user.id):
        await q.answer("الأداة دي لمشتركي Premium ⭐", show_alert=True)
        return
    if MEDIA_ACTIONS[action][1] and not media["is_video"]:
        await q.answer("الأداة دي للفيديو بس.", show_alert=True)
        return
    await q.answer()

    if action == "speed" and len(parts) == 2:
        kb = [[InlineKeyboardButton(f"{sp}x", callback_data=f"m:speed:{sp}") for sp in SPEEDS]]
        await q.edit_message_text("⏩ اختار سرعة الصوت:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "cut":
        context.user_data["await_cut"] = True
        await q.edit_message_text(
            "✂️ ابعت البداية والنهاية للمقطع، مثال:\n0:10-0:45\nأو بالثواني: 10-45\n"
            f"(أقصى مدة {CUT_MAX_SECONDS // 60} دقايق)"
        )
        return

    param = parts[2] if len(parts) > 2 else None
    await q.edit_message_text("⏳ جاري المعالجة...")
    await run_media_job(q.message, context, q.from_user.id, action, param)


async def extra_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """صورة الغلاف / الترجمة من رابط."""
    q = update.callback_query
    await q.answer()
    kind = q.data.split(":", 1)[1]
    if kind not in ("thumb", "subs"):
        return

    if not await is_allowed(context.bot, q.from_user.id):
        await q.edit_message_text(JOIN_TEXT, reply_markup=join_keyboard())
        return

    url = context.user_data.get("download_url")
    if not url:
        await q.edit_message_text("❌ انتهت الجلسة. ابعت الرابط مرة ثانية.")
        return

    await q.edit_message_text("⏳ جاري الجلب...")
    temp_dir = None
    try:
        async with LIMIT:
            files, temp_dir = await asyncio.to_thread(fetch_extra, url, kind)
        for path in files:
            with open(path, "rb") as f:
                if kind == "thumb":
                    try:
                        await q.message.reply_photo(photo=f, caption="🖼 @AhmedMediaDL_bot",
                                                    write_timeout=120, read_timeout=120)
                    except Exception as send_error:
                        print("PHOTO SEND ERROR:", repr(send_error))
                        f.seek(0)
                        await q.message.reply_document(document=f, write_timeout=120, read_timeout=120)
                else:
                    await q.message.reply_document(document=f, caption="📝 @AhmedMediaDL_bot",
                                                   write_timeout=120, read_timeout=120)
        await q.edit_message_text("✅ تم.")
    except Exception as e:
        print("EXTRA ERROR:", kind, repr(e))
        await q.edit_message_text(f"❌ {short_error(e)}")
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def premium_view(user_id):
    """نص + أزرار صفحة Premium (للأمر والزر)."""
    month_price = PLANS["m1"][1]
    lines = [
        "⭐ Ahmed Media Downloader Premium\n",
        "• بدون علامة مائية",
        "• جودات 1080p وأعلى",
        f"• نصوص أطول لتحويل النص لصوت (حتى {TTS_MAX_PREMIUM} حرف)",
        "• أدوات مقفولة: قص المقاطع وضغط الفيديو\n",
        "🎁 العروض:",
    ]
    kb = []
    for code, (days, stars, name) in PLANS.items():
        full = month_price * days // 30
        save = ""
        if stars < full:
            pct = round((1 - stars / full) * 100)
            save = f" — وفّر {pct}%"
            lines.append(f"• {name}: {stars} ⭐{save}")
        else:
            lines.append(f"• {name}: {stars} ⭐")
        kb.append([InlineKeyboardButton(
            f"⭐ {name} — {stars} Stars{save}", callback_data=f"buy:{code}"
        )])

    if user_id in (OWNER_ID, ADMIN_ID):
        head = "👑 أنت صاحب البوت — Premium دائم.\n\n"
    elif is_premium(user_id):
        until = premium_until(user_id)
        head = f"✅ اشتراكك فعال لحد {until:%Y-%m-%d}. تقدر تجدد من هنا (بتتضاف على المدة الحالية).\n\n"
    else:
        head = ""
    return head + "\n".join(lines), InlineKeyboardMarkup(kb)


async def premium_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text, kb = premium_view(q.from_user.id)
    await q.edit_message_text(text, reply_markup=kb)


async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb = premium_view(update.effective_user.id)
    await update.message.reply_text(text, reply_markup=kb)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in (OWNER_ID, ADMIN_ID):
        await update.message.reply_text("👑 حسابك: صاحب البوت — Premium دائم.")
    elif is_premium(uid):
        until = premium_until(uid)
        await update.message.reply_text(f"⭐ اشتراكك Premium فعال لحد {until:%Y-%m-%d}.")
    else:
        await update.message.reply_text(
            "حسابك مجاني (حتى 720p وبعلامة مائية).\nللاشتراك: /premium"
        )


HELP_TEXT = (
    "📖 أوامر البوت:\n\n"
    "/start — القائمة الرئيسية\n"
    "/help — الأوامر وشرح الاستخدام\n"
    "/premium — الاشتراك والعروض ⭐\n"
    "/status — حالة اشتراكي\n"
    "/cancel — إلغاء العملية الحالية\n\n"
    "💡 طريقة الاستخدام:\n"
    "• ابعت رابط فيديو ← تحميل (فيديو / MP3 / غلاف / ترجمة)\n"
    "• ابعت فيديو أو صوت ← أدوات (MP3، فويس، قص، سرعة، ضغط...)\n"
    "• اكتب نص عادي ← أحوله لصوت"
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["await_cut"] = False
    await update.message.reply_text("✅ تم الإلغاء. ابعت رابط أو ملف أو نص عشان نبدأ من جديد.")


async def post_init(app):
    """قايمة الاختصارات اللي بتظهر في زر (Menu) جنب خانة الكتابة."""
    user_cmds = [
        BotCommand("start", "القائمة الرئيسية"),
        BotCommand("help", "الأوامر وشرح الاستخدام"),
        BotCommand("premium", "الاشتراك والعروض ⭐"),
        BotCommand("status", "حالة اشتراكي"),
        BotCommand("cancel", "إلغاء العملية الحالية"),
    ]
    try:
        await app.bot.set_my_commands(user_cmds)
        for admin in {OWNER_ID, ADMIN_ID}:
            await app.bot.set_my_commands(
                user_cmds + [
                    BotCommand("grant", "تفعيل Premium لمستخدم (أدمن)"),
                    BotCommand("maintenance", "وضع الصيانة on/off (أدمن)"),
                ],
                scope=BotCommandScopeChat(chat_id=admin),
            )
    except Exception as e:
        print("SET COMMANDS ERROR:", repr(e))


async def send_premium_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    code = q.data.split(":", 1)[1]
    if code not in PLANS:
        return
    days, stars, name = PLANS[code]
    await context.bot.send_invoice(
        chat_id=q.from_user.id,
        title=f"Premium — {name}",
        description=f"اشتراك Premium لمدة {name} ({days} يوم).",
        payload=f"premium:{code}:{q.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(f"Premium {name}", stars)],
    )


def plan_from_payment(payment):
    """يرجّع كود الخطة من الفاتورة (والمبلغ لازم يطابق السعر)."""
    parts = (payment.invoice_payload or "").split(":")
    if len(parts) >= 2 and parts[0] == "premium" and parts[1] in PLANS:
        code = parts[1]
        if payment.total_amount == PLANS[code][1]:
            return code
        return None
    return None


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pq = update.pre_checkout_query
    legacy = (pq.invoice_payload or "").startswith("premium_30_")  # فواتير قديمة
    if legacy or plan_from_payment(pq):
        await pq.answer(ok=True)
    else:
        await pq.answer(ok=False, error_message="الفاتورة دي غير صالحة، افتح /premium وجرب تاني.")


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pay = update.message.successful_payment
    code = plan_from_payment(pay)
    days, name = (PLANS[code][0], PLANS[code][2]) if code else (30, "شهر")
    activate_premium(update.effective_user.id, days)
    until = premium_until(update.effective_user.id)
    await update.message.reply_text(
        f"🎉 تم تفعيل Premium بنجاح!\n⭐ الخطة: {name}\n📅 فعال لحد {until:%Y-%m-%d}."
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
        msg = short_error(e)
        await q.edit_message_text(msg if msg == MAINTENANCE_TEXT else f"❌ مقدرتش أقرأ الرابط.\n\n{msg}")
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
        msg = short_error(e)
        await q.edit_message_text(msg if msg == MAINTENANCE_TEXT else f"❌ حصل خطأ أثناء التحميل.\n\n{msg}")
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


async def error_handler(update, context):
    print("BOT ERROR:", repr(context.error))
    # أخطاء الشبكة المؤقتة: متبعتش رسالة صيانة
    if isinstance(context.error, (NetworkError, TimedOut)):
        return
    try:
        if isinstance(update, Update):
            if update.callback_query:
                await update.callback_query.answer(MAINTENANCE_TEXT, show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(MAINTENANCE_TEXT)
    except Exception as e:
        print("MAINTENANCE NOTICE FAILED:", repr(e))


def maintenance_on():
    return os.path.exists(MAINTENANCE_FLAG)


async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """وقت الصيانة: كل المستخدمين يشوفوا رسالة الصيانة، ما عدا صاحب البوت."""
    if not maintenance_on():
        return
    user = update.effective_user
    if user and user.id in (OWNER_ID, ADMIN_ID):
        return
    try:
        if update.callback_query:
            await update.callback_query.answer(MAINTENANCE_TEXT, show_alert=True)
        elif update.pre_checkout_query:
            await update.pre_checkout_query.answer(ok=False, error_message="البوت في صيانة، حاول لاحقاً.")
        elif update.effective_message:
            await update.effective_message.reply_text(MAINTENANCE_TEXT)
    except Exception as e:
        print("MAINTENANCE GATE ERROR:", repr(e))
    raise ApplicationHandlerStop


async def maintenance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /maintenance on | off  (للأدمن فقط)
    if update.effective_user.id not in (OWNER_ID, ADMIN_ID):
        return
    arg = (context.args[0].lower() if context.args else "")
    if arg == "on":
        open(MAINTENANCE_FLAG, "w").close()
        await update.message.reply_text("🛠 وضع الصيانة شغال. المستخدمين هيشوفوا رسالة الصيانة.")
    elif arg == "off":
        if os.path.exists(MAINTENANCE_FLAG):
            os.remove(MAINTENANCE_FLAG)
        await update.message.reply_text("✅ وضع الصيانة اتقفل. البوت رجع يشتغل.")
    else:
        state = "شغال" if maintenance_on() else "مقفول"
        await update.message.reply_text(f"وضع الصيانة حالياً: {state}\nالاستخدام: /maintenance on أو /maintenance off")


# =========================
# MAIN
# =========================

def main():
    start_keep_alive()

    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .post_init(post_init)
        .build()
    )

    media_filter = (
        filters.VIDEO
        | filters.AUDIO
        | filters.VOICE
        | filters.VIDEO_NOTE
        | filters.Document.VIDEO
        | filters.Document.AUDIO
    )

    app.add_handler(TypeHandler(Update, maintenance_gate), group=-1)
    app.add_handler(CommandHandler("maintenance", maintenance_cmd))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CallbackQueryHandler(check_sub, pattern=r"^check_sub$"))
    app.add_handler(CallbackQueryHandler(premium_menu, pattern=r"^premium$"))
    app.add_handler(CallbackQueryHandler(send_premium_invoice, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(quality_menu, pattern=r"^choose_(video|audio)$"))
    app.add_handler(CallbackQueryHandler(locked, pattern=r"^lock$"))
    app.add_handler(CallbackQueryHandler(download_callback, pattern=r"^dl:"))
    app.add_handler(CallbackQueryHandler(tts_callback, pattern=r"^tts:"))
    app.add_handler(CallbackQueryHandler(media_callback, pattern=r"^m:"))
    app.add_handler(CallbackQueryHandler(extra_callback, pattern=r"^extra:"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(media_filter, handle_file_to_mp3))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    print("FFmpeg:", FFMPEG)
    print("Ahmed Media Downloader is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
