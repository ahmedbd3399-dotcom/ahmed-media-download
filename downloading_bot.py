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
PREMIUM_ACTIONS = {"cut", "compress", "reels"}
AUDIO_ONLY_ACTIONS = {"wave"}   # أدوات للملفات الصوتية بس
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
    con.execute(
        "CREATE TABLE IF NOT EXISTS seen ("
        "user_id INTEGER PRIMARY KEY, first_seen TEXT, last_seen TEXT, "
        "banned INTEGER DEFAULT 0, referred_by INTEGER, ref_rewarded INTEGER DEFAULT 0)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ts TEXT, kind TEXT, detail TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS prefs ("
        "user_id INTEGER, key TEXT, value TEXT, PRIMARY KEY (user_id, key))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS codes ("
        "code TEXT PRIMARY KEY, days INTEGER, uses_left INTEGER, created TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS redemptions ("
        "code TEXT, user_id INTEGER, PRIMARY KEY (code, user_id))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS reminders ("
        "user_id INTEGER, until TEXT, PRIMARY KEY (user_id, until))"
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


REF_REWARD_DAYS = 3      # أيام Premium لصاحب الدعوة عن كل صديق جديد
REF_MAX_REWARDS = 20     # أقصى عدد دعوات بتتكافأ لكل مستخدم


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def is_admin(user_id):
    return user_id in (OWNER_ID, ADMIN_ID)


def track_user(user_id, ref=None):
    """يسجّل المستخدم. يرجّع True لو أول مرة نشوفه."""
    con = db()
    row = con.execute("SELECT user_id FROM seen WHERE user_id = ?", (user_id,)).fetchone()
    now = now_iso()
    if row:
        con.execute("UPDATE seen SET last_seen = ? WHERE user_id = ?", (now, user_id))
        con.commit()
        con.close()
        return False
    referred_by = None
    if ref and ref != user_id:
        ok = con.execute(
            "SELECT 1 FROM seen WHERE user_id = ? AND banned = 0", (ref,)
        ).fetchone()
        if ok:
            referred_by = ref
    con.execute(
        "INSERT INTO seen (user_id, first_seen, last_seen, referred_by) VALUES (?, ?, ?, ?)",
        (user_id, now, now, referred_by),
    )
    con.commit()
    con.close()
    return True


def is_banned(user_id):
    con = db()
    row = con.execute("SELECT banned FROM seen WHERE user_id = ?", (user_id,)).fetchone()
    con.close()
    return bool(row and row[0])


def set_banned(user_id, value):
    con = db()
    con.execute(
        "INSERT INTO seen (user_id, first_seen, last_seen, banned) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET banned = excluded.banned",
        (user_id, now_iso(), now_iso(), 1 if value else 0),
    )
    con.commit()
    con.close()


def log_event(user_id, kind, detail=""):
    try:
        con = db()
        con.execute(
            "INSERT INTO events (user_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
            (user_id, now_iso(), kind, (detail or "")[:300]),
        )
        con.commit()
        con.close()
    except Exception as e:
        print("LOG EVENT ERROR:", repr(e))


def recent_downloads(user_id, n=10):
    con = db()
    rows = con.execute(
        "SELECT ts, kind, detail FROM events WHERE user_id = ? AND kind LIKE 'dl_%' "
        "ORDER BY id DESC LIMIT ?", (user_id, n),
    ).fetchall()
    con.close()
    return rows


def all_user_ids():
    con = db()
    rows = con.execute("SELECT user_id FROM seen WHERE banned = 0").fetchall()
    con.close()
    return [r[0] for r in rows]


def get_stats():
    con = db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    total = q("SELECT COUNT(*) FROM seen")
    new_today = q("SELECT COUNT(*) FROM seen WHERE first_seen LIKE ?", today + "%")
    active_today = q("SELECT COUNT(*) FROM seen WHERE last_seen LIKE ?", today + "%")
    banned = q("SELECT COUNT(*) FROM seen WHERE banned = 1")
    dl_total = q("SELECT COUNT(*) FROM events WHERE kind LIKE 'dl_%'")
    dl_today = q("SELECT COUNT(*) FROM events WHERE kind LIKE 'dl_%' AND ts LIKE ?", today + "%")
    top = con.execute(
        "SELECT kind, COUNT(*) c FROM events WHERE kind LIKE 'tool_%' OR kind = 'tts' "
        "GROUP BY kind ORDER BY c DESC LIMIT 5"
    ).fetchall()
    prem_rows = con.execute("SELECT premium_until FROM users").fetchall()
    con.close()
    now = datetime.now(timezone.utc)
    premium = 0
    for (v,) in prem_rows:
        try:
            if v and datetime.fromisoformat(v) > now:
                premium += 1
        except Exception:
            pass
    return dict(total=total, new_today=new_today, active_today=active_today, banned=banned,
                premium=premium, dl_total=dl_total, dl_today=dl_today, top=top)


async def reward_referral(bot, user_id):
    """يكافئ صاحب الدعوة (مرة واحدة) بعد ما الصديق يشترك ويبدأ."""
    con = db()
    row = con.execute(
        "SELECT referred_by, ref_rewarded FROM seen WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row or not row[0] or row[1]:
        con.close()
        return
    inviter = row[0]
    con.execute("UPDATE seen SET ref_rewarded = 1 WHERE user_id = ?", (user_id,))
    done = con.execute(
        "SELECT COUNT(*) FROM seen WHERE referred_by = ? AND ref_rewarded = 1", (inviter,)
    ).fetchone()[0]
    con.commit()
    con.close()
    if done > REF_MAX_REWARDS:
        return
    activate_premium(inviter, REF_REWARD_DAYS)
    try:
        await bot.send_message(
            inviter,
            f"🎁 صديقك انضم عن طريق رابطك! اتضاف لك {REF_REWARD_DAYS} أيام Premium.\n"
            f"({done}/{REF_MAX_REWARDS} دعوات متكافأة)",
        )
    except Exception as e:
        print("REF NOTIFY ERROR:", repr(e))


def get_pref(user_id, key, default=None):
    con = db()
    row = con.execute(
        "SELECT value FROM prefs WHERE user_id = ? AND key = ?", (user_id, key)
    ).fetchone()
    con.close()
    return row[0] if row else default


def set_pref(user_id, key, value):
    con = db()
    con.execute(
        "INSERT INTO prefs (user_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
        (user_id, key, str(value)),
    )
    con.commit()
    con.close()


def clear_prefs(user_id):
    con = db()
    con.execute("DELETE FROM prefs WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()


def make_code(days, uses):
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "AMD-" + "".join(secrets.choice(alphabet) for _ in range(6))
    con = db()
    con.execute(
        "INSERT INTO codes (code, days, uses_left, created) VALUES (?, ?, ?, ?)",
        (code, days, uses, now_iso()),
    )
    con.commit()
    con.close()
    return code


def redeem_code(user_id, code):
    """يرجّع (نجح؟, رسالة أو عدد الأيام)."""
    code = code.strip().upper()
    con = db()
    row = con.execute("SELECT days, uses_left FROM codes WHERE code = ?", (code,)).fetchone()
    if not row:
        con.close()
        return False, "الكود غير صحيح."
    days, left = row
    if left <= 0:
        con.close()
        return False, "الكود ده خلص استخدامه."
    used = con.execute(
        "SELECT 1 FROM redemptions WHERE code = ? AND user_id = ?", (code, user_id)
    ).fetchone()
    if used:
        con.close()
        return False, "انت استخدمت الكود ده قبل كده."
    con.execute("INSERT INTO redemptions (code, user_id) VALUES (?, ?)", (code, user_id))
    con.execute("UPDATE codes SET uses_left = uses_left - 1 WHERE code = ?", (code,))
    con.commit()
    con.close()
    activate_premium(user_id, days)
    return True, days


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
    "vspeed": ("🎬 سرعة الفيديو", True),
    "rotate": ("🔄 تدوير الفيديو", True),
    "mute": ("🔇 كتم الصوت", True),
    "bass": ("🔈 تعزيز الباس", False),
    "pitchup": ("🐿 صوت رفيع", False),
    "pitchdown": ("🦁 صوت تخين", False),
    "silence": ("🤫 حذف الصمت", False),
    "wave": ("🌊 صوت ← فيديو", False),
    "reels": ("📱 ريلز 9:16 ⭐", True),
}
VSPEEDS = ["0.5", "1.5", "2"]


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

    if action == "bass":
        out = os.path.join(temp_dir, "bass.mp3")
        _ff(["-i", src, "-vn", "-filter:a", "bass=g=10,alimiter=limit=0.95",
             "-c:a", "libmp3lame", "-b:a", "192k", out])
        _check_out(out)
        return out, "audio"

    if action == "rotate":
        out = os.path.join(temp_dir, "rotated.mp4")
        _ff(["-i", src, "-vf", "transpose=1", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", out])
        _check_out(out)
        return out, "video"

    if action == "mute":
        out = os.path.join(temp_dir, "muted.mp4")
        _ff(["-i", src, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out])
        _check_out(out)
        return out, "video"

    if action == "vspeed":
        if param not in VSPEEDS:
            raise UserError("سرعة غير مدعومة.")
        out = os.path.join(temp_dir, "vspeed.mp4")
        common = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                  "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        try:
            _ff(["-i", src, "-filter_complex",
                 f"[0:v]setpts=PTS/{param}[v];[0:a]atempo={param}[a]",
                 "-map", "[v]", "-map", "[a]", *common, "-c:a", "aac", "-b:a", "128k", out])
        except UserError:
            # فيديو من غير صوت
            _ff(["-i", src, "-vf", f"setpts=PTS/{param}", "-an", *common, out])
        _check_out(out)
        return out, "video"

    if action in ("pitchup", "pitchdown"):
        # نظبّط التردد الأول (44100) وبعدين نغيّر الطبقة من غير ما تتغير المدة
        f = "aresample=44100,asetrate=55125,aresample=44100,atempo=0.8" if action == "pitchup" \
            else "aresample=44100,asetrate=35280,aresample=44100,atempo=1.25"
        out = os.path.join(temp_dir, f"{action}.mp3")
        _ff(["-i", src, "-vn", "-filter:a", f, "-c:a", "libmp3lame", "-b:a", "192k", out])
        _check_out(out)
        return out, "audio"

    if action == "silence":
        out = os.path.join(temp_dir, "nosilence.mp3")
        _ff(["-i", src, "-vn", "-filter:a",
             "silenceremove=start_periods=1:start_threshold=-45dB:"
             "stop_periods=-1:stop_threshold=-45dB:stop_duration=0.6",
             "-c:a", "libmp3lame", "-b:a", "192k", out])
        _check_out(out)
        return out, "audio"

    if action == "wave":
        out = os.path.join(temp_dir, "wave.mp4")
        _ff(["-i", src, "-t", "600", "-filter_complex",
             "[0:a]showwaves=s=720x720:mode=cline:colors=0x00d4ff,format=yuv420p[v]",
             "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "28", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out])
        _check_out(out)
        return out, "video"

    if action == "reels":
        out = os.path.join(temp_dir, "reels.mp4")
        vf = ("split[a][b];"
              "[a]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,boxblur=25:5[bg];"
              "[b]scale=720:1280:force_original_aspect_ratio=decrease[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p")
        _ff(["-i", src, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out])
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
    await reward_referral(context.bot, q.from_user.id)
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
    "• نص عادي أو ملف .txt ← أحوله لصوت"
)

MENU_HELP = {
    "dl": "🎥 *تحميل من رابط*\n\nابعت رابط الفيديو (تيك توك، إنستجرام، فيسبوك، يوتيوب، تويتر...) وأنا أحمله لك.",
    "mp3": "🎛 *أدوات الصوت والفيديو*\n\nابعت أي فيديو أو ملف صوت أو فويس (حتى 20MB) وهتختار:\n• 🎵 MP3 • 🎤 فويس • ⏩ سرعة الصوت • 🔊 تقوية الصوت\n• 🔈 باس • 🐿 صوت رفيع • 🦁 صوت تخين • 🤫 حذف الصمت • 🌊 صوت←فيديو\n• ✂️ قص مقطع ⭐ • 🗜 ضغط الفيديو ⭐ • 📱 ريلز 9:16 ⭐\n• للفيديو: 🖼 صورة • 🎞 مقطع متحرك • 🎬 سرعة • 🔄 تدوير • 🔇 كتم\n\nلو الفيديو أكبر، ابعت رابطه بدل الملف.",
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
    await reward_referral(context.bot, update.effective_user.id)
    await update.message.reply_text(START_TEXT, reply_markup=main_menu_kb())


async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /grant <user_id> للأدمن فقط (للتجربة)
    if not is_admin(update.effective_user.id):
        return
    try:
        uid = int(context.args[0]) if context.args else update.effective_user.id
        days = int(context.args[1]) if len(context.args) > 1 else 30
        activate_premium(uid, days)
        await update.message.reply_text(f"✅ تم تفعيل Premium للمستخدم {uid} لمدة {days} يوم")
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
    await start_tts_flow(update, context, text)


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
        log_event(q.from_user.id, "tts")
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
        if action in AUDIO_ONLY_ACTIONS and is_video:
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
        log_event(user_id, f"tool_{action}")
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
    if action in AUDIO_ONLY_ACTIONS and media["is_video"]:
        await q.answer("الأداة دي للملفات الصوتية بس.", show_alert=True)
        return
    await q.answer()

    if action == "speed" and len(parts) == 2:
        kb = [[InlineKeyboardButton(f"{sp}x", callback_data=f"m:speed:{sp}") for sp in SPEEDS]]
        await q.edit_message_text("⏩ اختار سرعة الصوت:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "vspeed" and len(parts) == 2:
        names = {"0.5": "🐢 بطيء 0.5x", "1.5": "🐇 1.5x", "2": "⚡ 2x"}
        kb = [[InlineKeyboardButton(names[v], callback_data=f"m:vspeed:{v}") for v in VSPEEDS]]
        await q.edit_message_text("🎬 اختار سرعة الفيديو:", reply_markup=InlineKeyboardMarkup(kb))
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
    "/history — آخر تحميلاتك\n"
    "/invite — ادعُ أصحابك واكسب Premium 🎁\n"
    "/redeem — تفعيل كود هدية 🎟\n"
    "/settings — إعداداتي (الصوت والسرعة الافتراضية)\n"
    "/feedback — ابعت اقتراح أو مشكلة للإدارة\n"
    "/cancel — إلغاء العملية الحالية\n\n"
    "💡 طريقة الاستخدام:\n"
    "• ابعت رابط فيديو ← تحميل (فيديو / MP3 / غلاف / ترجمة)\n"
    "• ابعت فيديو أو صوت ← أدوات (MP3، فويس، قص، سرعة، ضغط...)\n"
    "• اكتب نص عادي أو ابعت ملف .txt ← أحوله لصوت"
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
        BotCommand("history", "آخر تحميلاتك"),
        BotCommand("invite", "ادعُ أصحابك واكسب Premium 🎁"),
        BotCommand("redeem", "تفعيل كود هدية 🎟"),
        BotCommand("settings", "إعداداتي (الصوت والسرعة)"),
        BotCommand("feedback", "ابعت اقتراح أو مشكلة"),
        BotCommand("cancel", "إلغاء العملية الحالية"),
    ]
    try:
        await app.bot.set_my_commands(user_cmds)
        for admin in {OWNER_ID, ADMIN_ID}:
            await app.bot.set_my_commands(
                user_cmds + [
                    BotCommand("grant", "تفعيل Premium: /grant id أيام (أدمن)"),
                    BotCommand("stats", "إحصائيات البوت (أدمن)"),
                    BotCommand("broadcast", "رسالة لكل المستخدمين (أدمن)"),
                    BotCommand("gencode", "إنشاء كود هدية (أدمن)"),
                    BotCommand("userinfo", "بيانات مستخدم (أدمن)"),
                    BotCommand("backup", "نسخة احتياطية للداتا (أدمن)"),
                    BotCommand("ban", "حظر مستخدم (أدمن)"),
                    BotCommand("unban", "فك الحظر (أدمن)"),
                    BotCommand("maintenance", "وضع الصيانة on/off (أدمن)"),
                ],
                scope=BotCommandScopeChat(chat_id=admin),
            )
    except Exception as e:
        print("SET COMMANDS ERROR:", repr(e))
    # تنبيه قرب انتهاء الاشتراك (بيشتغل في الخلفية)
    app.bot_data["expiry_task"] = asyncio.create_task(expiry_loop(app.bot))


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
        log_event(q.from_user.id, f"dl_{media_type}", f"{quality}|{url}")
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
# TRACKING / BAN / ADMIN / USER EXTRAS
# =========================

REF_RE = re.compile(r"^/start(?:@\w+)?\s+ref_(\d+)")


async def track_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يسجّل كل مستخدم، ويمنع المحظورين."""
    user = update.effective_user
    if not user or user.is_bot:
        return
    ref = None
    msg = update.effective_message
    m = REF_RE.match((getattr(msg, "text", None) or ""))
    if m:
        ref = int(m.group(1))
    try:
        track_user(user.id, ref)
        banned = is_banned(user.id) and not is_admin(user.id)
    except Exception as e:
        print("TRACK ERROR:", repr(e))
        return
    if not banned:
        return
    try:
        if update.callback_query:
            await update.callback_query.answer("⛔ تم حظرك من استخدام البوت.", show_alert=True)
        elif update.pre_checkout_query:
            await update.pre_checkout_query.answer(ok=False, error_message="الحساب محظور.")
        elif msg:
            await msg.reply_text("⛔ تم حظرك من استخدام البوت.")
    except Exception as e:
        print("BAN NOTICE ERROR:", repr(e))
    raise ApplicationHandlerStop


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = recent_downloads(update.effective_user.id, 10)
    if not rows:
        await update.message.reply_text("📭 مفيش تحميلات لسه. ابعت رابط فيديو وابدأ!")
        return
    lines = ["🕘 آخر تحميلاتك:\n"]
    for i, (ts, kind, detail) in enumerate(rows, 1):
        quality, _, url = detail.partition("|")
        icon = "🎵" if kind == "dl_audio" else "🎥"
        q = f"{quality}{'p' if kind == 'dl_video' and quality.isdigit() else ''}"
        if kind == "dl_audio" and quality.isdigit():
            q = f"{quality}kbps"
        lines.append(f"{i}. {icon} {q} — {ts[:10]}\n{url}")
    await update.message.reply_text("\n".join(lines), disable_web_page_preview=True)


async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    link = f"https://t.me/{context.bot.username}?start=ref_{uid}"
    con = db()
    n = con.execute(
        "SELECT COUNT(*) FROM seen WHERE referred_by = ? AND ref_rewarded = 1", (uid,)
    ).fetchone()[0]
    con.close()
    await update.message.reply_text(
        f"🎁 ادعُ أصحابك واكسب Premium!\n\n"
        f"كل صديق جديد يدخل من رابطك ويشترك في القناة = {REF_REWARD_DAYS} أيام Premium ليك.\n\n"
        f"🔗 رابطك:\n{link}\n\n"
        f"دعواتك المتكافأة: {n}/{REF_MAX_REWARDS}",
        disable_web_page_preview=True,
    )


async def feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("اكتب رسالتك بعد الأمر، مثال:\n/feedback عايز ميزة كذا")
        return
    last = context.user_data.get("fb_ts", 0)
    nowt = datetime.now().timestamp()
    if nowt - last < 60:
        await update.message.reply_text("⏳ استنى دقيقة قبل ما تبعت رسالة تانية.")
        return
    context.user_data["fb_ts"] = nowt
    u = update.effective_user
    who = f"@{u.username}" if u.username else u.full_name
    try:
        await context.bot.send_message(
            OWNER_ID, f"💬 رسالة من {who} (ID: {u.id}):\n\n{text[:1500]}"
        )
        await update.message.reply_text("✅ وصلت رسالتك للإدارة، شكراً ليك!")
    except Exception as e:
        print("FEEDBACK ERROR:", repr(e))
        await update.message.reply_text("❌ معرفتش أوصّل الرسالة دلوقتي، جرب بعدين.")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    s = get_stats()
    top = "\n".join(f"  • {k.replace('tool_', '')}: {c}" for k, c in s["top"]) or "  —"
    await update.message.reply_text(
        "📊 إحصائيات البوت\n\n"
        f"👥 المستخدمين: {s['total']} (جدد النهارده: {s['new_today']})\n"
        f"🟢 نشطين النهارده: {s['active_today']}\n"
        f"⭐ Premium فعال: {s['premium']}\n"
        f"⛔ محظورين: {s['banned']}\n"
        f"📥 التحميلات: {s['dl_total']} (النهارده: {s['dl_today']})\n\n"
        f"🛠 أكتر الأدوات استخداماً:\n{top}"
    )


def _target_id(update, context):
    if context.args:
        try:
            return int(context.args[0])
        except ValueError:
            return None
    return None


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    uid = _target_id(update, context)
    if uid is None or is_admin(uid):
        await update.message.reply_text("الاستخدام: /ban <user_id>")
        return
    set_banned(uid, True)
    await update.message.reply_text(f"⛔ تم حظر {uid}")


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    uid = _target_id(update, context)
    if uid is None:
        await update.message.reply_text("الاستخدام: /unban <user_id>")
        return
    set_banned(uid, False)
    await update.message.reply_text(f"✅ تم فك الحظر عن {uid}")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text("الاستخدام: /broadcast <النص>\nهتشوف معاينة وبعدين تأكد بـ /broadcast_ok")
        return
    context.bot_data["pending_broadcast"] = text
    await update.message.reply_text(
        f"📣 معاينة الرسالة (هتتبعت لـ {len(all_user_ids())} مستخدم):\n\n{text}\n\n"
        "للتأكيد ابعت /broadcast_ok — ولإلغائها تجاهل الرسالة."
    )


async def _do_broadcast(bot, admin_chat, text):
    sent = failed = 0
    for uid in all_user_ids():
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.06)  # بعيد عن حدود تليجرام
    try:
        await bot.send_message(admin_chat, f"✅ خلص البث: اتبعت {sent} — فشل {failed}")
    except Exception:
        pass


async def broadcast_ok_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = context.bot_data.pop("pending_broadcast", None)
    if not text:
        await update.message.reply_text("مفيش رسالة معلّقة. استخدم /broadcast <النص> الأول.")
        return
    await update.message.reply_text("🚀 بدأ البث في الخلفية...")
    task = asyncio.create_task(_do_broadcast(context.bot, update.effective_chat.id, text))
    context.bot_data.setdefault("tasks", set()).add(task)
    task.add_done_callback(lambda t: context.bot_data["tasks"].discard(t))


async def remind_expiring(bot):
    """تنبيه لمشتركين اشتراكهم هينتهي خلال يومين (مرة لكل مدة)."""
    now = datetime.now(timezone.utc)
    limit = now + timedelta(days=2)
    con = db()
    rows = con.execute("SELECT user_id, premium_until FROM users").fetchall()
    con.close()
    for uid, until in rows:
        try:
            u = datetime.fromisoformat(until) if until else None
        except Exception:
            u = None
        if not u or not (now < u <= limit) or is_admin(uid):
            continue
        con = db()
        done = con.execute(
            "SELECT 1 FROM reminders WHERE user_id = ? AND until = ?", (uid, until)
        ).fetchone()
        if not done:
            con.execute("INSERT INTO reminders (user_id, until) VALUES (?, ?)", (uid, until))
            con.commit()
        con.close()
        if done:
            continue
        try:
            await bot.send_message(
                uid, f"⏰ اشتراك Premium هينتهي في {u:%Y-%m-%d}.\nجدّد من /premium وتتضاف المدة على الباقي ⭐"
            )
        except Exception as e:
            print("REMIND ERROR:", uid, repr(e))


async def expiry_loop(bot):
    await asyncio.sleep(60)
    while True:
        try:
            await remind_expiring(bot)
        except Exception as e:
            print("EXPIRY LOOP ERROR:", repr(e))
        await asyncio.sleep(6 * 3600)


# =========================
# SETTINGS / CODES / TEXT FILES / ADMIN EXTRAS
# =========================

def settings_view(user_id):
    v = get_pref(user_id, "voice")
    r = get_pref(user_id, "rate")
    vname = VOICES[v][0] if v in VOICES else "غير محدد"
    rname = next((lbl for lbl, val in TTS_RATES if val == r), "غير محدد")
    kb = [
        [InlineKeyboardButton(f"🎙 الصوت الافتراضي: {vname}", callback_data="set:voices")],
        [InlineKeyboardButton(f"⏩ السرعة الافتراضية: {rname}", callback_data="set:rates")],
        [InlineKeyboardButton("🗑 مسح إعداداتي", callback_data="set:clear")],
    ]
    text = (
        "⚙️ إعداداتي\n\n"
        "لو حددت صوت وسرعة، هيظهر لك زر «اقرأ بإعداداتي» لما تبعت نص — "
        "بدل ما تختار كل مرة."
    )
    return text, InlineKeyboardMarkup(kb)


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb = settings_view(update.effective_user.id)
    await update.message.reply_text(text, reply_markup=kb)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    parts = q.data.split(":")
    act = parts[1] if len(parts) > 1 else ""

    if act == "voices":
        kb = [[InlineKeyboardButton(label, callback_data=f"set:v:{key}")]
              for key, (label, _) in VOICES.items()]
        await q.edit_message_text("🎙 اختار الصوت الافتراضي:", reply_markup=InlineKeyboardMarkup(kb))
        return
    if act == "rates":
        kb = [[InlineKeyboardButton(label, callback_data=f"set:r:{rate}")]
              for label, rate in TTS_RATES]
        await q.edit_message_text("⏩ اختار السرعة الافتراضية:", reply_markup=InlineKeyboardMarkup(kb))
        return
    if act == "v" and len(parts) == 3 and parts[2] in VOICES:
        set_pref(uid, "voice", parts[2])
    elif act == "r" and len(parts) == 3 and parts[2] in {r for _, r in TTS_RATES}:
        set_pref(uid, "rate", parts[2])
    elif act == "clear":
        clear_prefs(uid)
    text, kb = settings_view(uid)
    await q.edit_message_text(text, reply_markup=kb)


async def start_tts_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    """نص -> اختيار صوت (مع زر سريع لو فيه إعدادات محفوظة)."""
    uid = update.effective_user.id
    limit = TTS_MAX_PREMIUM if is_premium(uid) else TTS_MAX_FREE
    if len(text) > limit:
        await update.effective_message.reply_text(
            f"❌ النص طويل ({len(text)} حرف). الحد الأقصى {limit} حرف."
            + ("" if limit == TTS_MAX_PREMIUM else "\n⭐ مشتركي Premium لهم حد أكبر.")
        )
        return

    context.user_data["tts_text"] = text
    kb = []
    v, r = get_pref(uid, "voice"), get_pref(uid, "rate")
    if v in VOICES and r in {x for _, x in TTS_RATES}:
        rname = next(lbl for lbl, val in TTS_RATES if val == r)
        kb.append([InlineKeyboardButton(
            f"▶️ اقرأ بإعداداتي ({VOICES[v][0]} • {rname})", callback_data=f"tts:{v}:{r}"
        )])
    kb += [[InlineKeyboardButton(label, callback_data=f"tts:{key}")]
           for key, (label, _) in VOICES.items()]
    await update.effective_message.reply_text(
        "🔊 اختار الصوت اللي عايز أقرأ به النص:", reply_markup=InlineKeyboardMarkup(kb)
    )


async def handle_text_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ملف نصي (.txt) -> نفس مسار تحويل النص لصوت."""
    msg = update.message
    if not await is_allowed(context.bot, update.effective_user.id):
        await msg.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return
    doc = msg.document
    if doc.file_size and doc.file_size > 200 * 1024:
        await msg.reply_text("❌ الملف النصي كبير (الحد 200KB).")
        return
    temp_dir = tempfile.mkdtemp(prefix="amf_")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        path = os.path.join(temp_dir, "in.txt")
        await tg_file.download_to_drive(path)
        raw = Path(path).read_bytes()
        for enc in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                text = raw.decode(enc).strip()
                break
            except UnicodeDecodeError:
                continue
        if not text:
            await msg.reply_text("❌ الملف فاضي.")
            return
        await start_tts_flow(update, context, text)
    except Exception as e:
        print("TEXT FILE ERROR:", repr(e))
        await msg.reply_text(MAINTENANCE_TEXT)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب الكود بعد الأمر، مثال:\n/redeem AMD-XXXXXX")
        return
    # حماية من التخمين: 5 محاولات غلط كل 10 دقايق
    nowt = datetime.now().timestamp()
    fails = [t for t in context.user_data.get("redeem_fails", []) if nowt - t < 600]
    if len(fails) >= 5:
        await update.message.reply_text("⏳ محاولات كتير غلط. جرب بعد شوية.")
        return
    ok, res = redeem_code(update.effective_user.id, context.args[0])
    if ok:
        until = premium_until(update.effective_user.id)
        await update.message.reply_text(
            f"🎉 تم تفعيل الكود! اتضاف {res} يوم Premium.\n📅 فعال لحد {until:%Y-%m-%d}."
        )
    else:
        fails.append(nowt)
        context.user_data["redeem_fails"] = fails
        await update.message.reply_text(f"❌ {res}")


async def gencode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        days = int(context.args[0])
        uses = int(context.args[1]) if len(context.args) > 1 else 1
        if days < 1 or uses < 1 or days > 3650 or uses > 10000:
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("الاستخدام: /gencode <عدد الأيام> [عدد المرات]\nمثال: /gencode 30 50")
        return
    code = make_code(days, uses)
    await update.message.reply_text(
        f"🎟 كود جديد:\n{code}\n\n{days} يوم Premium — يصلح لـ {uses} مستخدم.\nيستخدموه بـ: /redeem {code}"
    )


async def userinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    uid = _target_id(update, context)
    if uid is None:
        await update.message.reply_text("الاستخدام: /userinfo <user_id>")
        return
    con = db()
    row = con.execute(
        "SELECT first_seen, last_seen, banned, referred_by FROM seen WHERE user_id = ?", (uid,)
    ).fetchone()
    dls = con.execute("SELECT COUNT(*) FROM events WHERE user_id = ? AND kind LIKE 'dl_%'", (uid,)).fetchone()[0]
    tools = con.execute("SELECT COUNT(*) FROM events WHERE user_id = ? AND (kind LIKE 'tool_%' OR kind = 'tts')", (uid,)).fetchone()[0]
    invited = con.execute("SELECT COUNT(*) FROM seen WHERE referred_by = ? AND ref_rewarded = 1", (uid,)).fetchone()[0]
    con.close()
    if not row:
        await update.message.reply_text("المستخدم ده مش مسجل عندي.")
        return
    until = premium_until(uid)
    prem = f"فعال لحد {until:%Y-%m-%d}" if is_premium(uid) and until else ("دائم" if is_admin(uid) else "لا")
    await update.message.reply_text(
        f"👤 {uid}\n"
        f"أول ظهور: {row[0][:10]}\nآخر نشاط: {row[1][:16].replace('T', ' ')} UTC\n"
        f"⭐ Premium: {prem}\n⛔ محظور: {'نعم' if row[2] else 'لا'}\n"
        f"📥 تحميلات: {dls} — 🛠 أدوات: {tools}\n"
        f"🎁 دعوات متكافأة: {invited}" + (f"\n↪️ جه بدعوة من: {row[3]}" if row[3] else "")
    )


async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبعتلك نسخة من قاعدة البيانات (مهم لو الاستضافة بتمسح الملفات)."""
    if not is_admin(update.effective_user.id):
        return
    temp_dir = tempfile.mkdtemp(prefix="amb_")
    try:
        dst = os.path.join(temp_dir, "users_backup.db")
        src = db()
        out = sqlite3.connect(dst)
        src.backup(out)
        out.close()
        src.close()
        with open(dst, "rb") as f:
            await update.message.reply_document(
                document=f, filename=f"users_backup_{datetime.now():%Y%m%d_%H%M}.db",
                caption="💾 نسخة احتياطية من قاعدة البيانات",
            )
    except Exception as e:
        print("BACKUP ERROR:", repr(e))
        await update.message.reply_text(f"❌ فشل النسخ: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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

    app.add_handler(TypeHandler(Update, track_gate), group=-2)
    app.add_handler(TypeHandler(Update, maintenance_gate), group=-1)
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CommandHandler("gencode", gencode_cmd))
    app.add_handler(CommandHandler("userinfo", userinfo_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("invite", invite_cmd))
    app.add_handler(CommandHandler("feedback", feedback_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("broadcast_ok", broadcast_ok_cmd))
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
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^set:"))
    app.add_handler(CallbackQueryHandler(extra_callback, pattern=r"^extra:"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(media_filter, handle_file_to_mp3))
    app.add_handler(MessageHandler(filters.Document.TEXT, handle_text_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    print("FFmpeg:", FFMPEG)
    print("Ahmed Media Downloader is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
