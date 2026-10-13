import os
import re
import shutil
import sqlite3
import asyncio
import tempfile
import threading
import subprocess
import json
import time
import socket
import ipaddress
import html as html_lib
import io
import base64
import urllib.error
import urllib.request
from urllib.parse import urlparse, urljoin, urlencode, quote
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

BOT_VERSION = "v17-ai-filters-perf"  # بتظهر في اللوج وفي /stats عشان تتأكد إن النسخة الجديدة هي اللي شغالة
OWNER_ID = 7219900342  # صاحب البوت: Premium دائم
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0) or OWNER_ID
BOT_USERNAME = os.environ.get("BOT_USERNAME", "AhmedMediaDL_bot").lstrip("@")  # بيتحدّث تلقائياً من تليجرام
WATERMARK_TEXT = "@" + BOT_USERNAME

# اسم البوت (عربي) — بيظهر في الرسايل، وبيتضبط تلقائياً في تليجرام (الاسم والوصف)
BOT_NAME = os.environ.get("BOT_NAME", "محمل فديوهات مجانا")
TG_NAME = os.environ.get("TG_NAME", BOT_NAME)[:64]
TG_SHORT = os.environ.get(
    "TG_SHORT",
    "حمّل فيديوهات تيك توك ويوتيوب وانستجرام وفيسبوك وتويتر، وحوّلها MP3 أو حوّل النص لصوت 🎬",
)[:120]
TG_DESC = os.environ.get(
    "TG_DESC",
    "🎬 " + BOT_NAME + "\n\n"
    "ابعت رابط الفيديو وهحمّله لك في ثواني من:\n"
    "• تيك توك • يوتيوب • انستجرام • فيسبوك • تويتر وغيرهم\n\n"
    "🎵 حوّل أي فيديو لـ MP3\n"
    "✂️ قص وضغط وتعديل الصوت والفيديو\n"
    "🔊 حوّل النص لصوت بأصوات عربية وإنجليزية\n"
    "⭐ اشتراك Premium بجودة أعلى\n\n"
    "اضغط «ابدأ» وابعت أول رابط 👇",
)[:512]
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
PREMIUM_ACTIONS = {"cut", "compress", "reels", "vfilter"}
AUDIO_ONLY_ACTIONS = {"wave"}   # أدوات للملفات الصوتية بس
CUT_MAX_SECONDS = 600     # أقصى مدة للمقطع المقصوص
SPEEDS = ["0.75", "1.25", "1.5", "2"]
TTS_RATES = [("🐢 بطيء", "-25"), ("🙂 عادي", "0"), ("🐇 سريع", "+25"), ("⚡ أسرع", "+50")]


class UserError(Exception):
    """خطأ رسالته مفهومة للمستخدم (مش عطل) — بنعرضه زي ما هو."""


URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
LIMIT = asyncio.Semaphore(int(os.environ.get("MAX_JOBS", "2") or 2))  # عمليتين تحميل كحد أقصى مع بعض


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

_DB_READY = set()
_SEEN_CACHE = {}   # user_id -> وقت آخر كتابة في الداتا (عشان مانكتبش مع كل رسالة)
_BAN_CACHE = {}    # user_id -> (محظور؟, وقت الفحص)


def db():
    con = sqlite3.connect(DB_FILE, timeout=10)
    if DB_FILE in _DB_READY:
        return con
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception as e:
        print("PRAGMA ERROR:", repr(e))
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
    con.execute(
        "CREATE TABLE IF NOT EXISTS usage ("
        "user_id INTEGER, day TEXT, kind TEXT, n INTEGER DEFAULT 0, PRIMARY KEY (user_id, day, kind))"
    )
    con.commit()
    _DB_READY.add(DB_FILE)
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
    now_t = time.time()
    last = _SEEN_CACHE.get(user_id)
    if last and now_t - last < 60 and not ref:
        return False
    _SEEN_CACHE[user_id] = now_t
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
    c = _BAN_CACHE.get(user_id)
    if c and time.time() - c[1] < 30:
        return c[0]
    con = db()
    row = con.execute("SELECT banned FROM seen WHERE user_id = ?", (user_id,)).fetchone()
    con.close()
    v = bool(row and row[0])
    _BAN_CACHE[user_id] = (v, time.time())
    return v


def set_banned(user_id, value):
    con = db()
    con.execute(
        "INSERT INTO seen (user_id, first_seen, last_seen, banned) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET banned = excluded.banned",
        (user_id, now_iso(), now_iso(), 1 if value else 0),
    )
    con.commit()
    con.close()
    _BAN_CACHE.pop(user_id, None)


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

PROXY_URL = os.environ.get("YTDLP_PROXY", "").strip()  # اختياري: http://user:pass@host:port
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def prepare_url(url):
    """يفك روابط تيك توك المختصرة (vt/vm/tiktok.com/t) ويكشف لو تيك توك حاجب السيرفر."""
    p = urlparse(url)
    host = p.netloc.lower()
    is_short = host in ("vt.tiktok.com", "vm.tiktok.com") or (
        host.endswith("tiktok.com") and p.path.startswith("/t/")
    )
    if not is_short:
        return url
    try:
        handlers = []
        if PROXY_URL:
            handlers.append(urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL}))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA})
        with opener.open(req, timeout=15) as r:
            final = r.geturl()
    except Exception as e:
        print("RESOLVE ERROR:", url, repr(e))
        return url
    print("RESOLVED:", url, "->", final)
    low = final.lower()
    if "/login" in low or "captcha" in low or "/verify" in low:
        print("TIKTOK BLOCKED THIS SERVER (login/captcha redirect)")
        return url
    m = re.match(r"(https?://[^?#]+/(?:video|photo)/\d+)", final)
    return m.group(1) if m else final


def base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "geo_bypass": True,
        "concurrent_fragment_downloads": 4,
    }
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG
    if PROXY_URL:
        opts["proxy"] = PROXY_URL

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
    orig = url
    FB_DIAG.pop(orig, None)
    url = prepare_url(url)
    opts = {**base_opts(), "skip_download": True}
    try:
        info = ytdlp_run(url, opts, False, orig)
    except Exception as e:
        print("YTDLP FORMATS ERROR (trying fallback):", repr(e)[:200])
        if fallback_resolve(url, orig):
            return [], True   # هنحمّل بالبديل: جودة واحدة "أفضل متاحة"
        raise

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

# =========================
# FALLBACKS (لو yt-dlp مقدرش يقرأ الرابط)
# =========================

DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
FB_MAX_BYTES = 250 * 1024 * 1024
VIDEO_EXTS = r"(?:mp4|m3u8|webm|mov|m4v)"


def _is_public_host(host):
    """حماية: مانطلبش عناوين داخلية (localhost / شبكة السيرفر)."""
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


def _http_open(url, referer=None, timeout=20):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not _is_public_host(p.hostname or ""):
        raise ValueError("blocked url")
    handlers = []
    if PROXY_URL:
        handlers.append(urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL}))
    opener = urllib.request.build_opener(*handlers)
    headers = {"User-Agent": DESKTOP_UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    return opener.open(urllib.request.Request(url, headers=headers), timeout=timeout)


def _abs(base, u):
    u = html_lib.unescape(u.replace("\\/", "/").replace("\\u0026", "&")).strip()
    return urljoin(base, u)


def _cffi_get(url, referer=None, timeout=25, max_bytes=8_000_000):
    """GET ببصمة متصفح حقيقي (curl_cffi) — بيعدّي حجب المواقع للـ urllib. يرجّع (status, bytes, final_url)."""
    from curl_cffi import requests as creq
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not _is_public_host(p.hostname or ""):
        raise ValueError("blocked url")
    headers = {"Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    kw = {"headers": headers, "timeout": timeout, "allow_redirects": True}
    if PROXY_URL:
        kw["proxies"] = {"http": PROXY_URL, "https": PROXY_URL}
    r = creq.get(url, impersonate="chrome", **kw)
    data = r.content
    if len(data) > max_bytes:
        raise ValueError("response too large")
    return r.status_code, data, str(r.url)


def _get_bytes(url, referer=None, timeout=25, max_bytes=8_000_000):
    """يجرّب curl_cffi الأول وبعدين urllib. لو الاتنين فشلوا بيرفع RuntimeError فيه السببين."""
    errs = []
    try:
        status, data, _ = _cffi_get(url, referer, timeout, max_bytes)
        if status < 400:
            return data
        errs.append(f"cffi HTTP {status}")
    except Exception as e:
        errs.append(f"cffi {type(e).__name__}: {str(e)[:60]}")
    try:
        with _http_open(url, referer=referer, timeout=timeout) as r:
            return r.read(max_bytes)
    except Exception as e:
        errs.append(f"urllib {type(e).__name__}: {str(e)[:60]}")
    raise RuntimeError(" | ".join(errs))


def _find_key(obj, key):
    """بحث عميق عن مفتاح جوه JSON متداخل."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if key in cur:
                yield cur[key]
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _iter_strings(obj):
    """كل النصوص جوه JSON بالترتيب."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _parse_tiktok_page(page, key=None, label=""):
    m = re.search(r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        _note(key, f"tiktok-page{label}: الصفحة مفيهاش بيانات المنشور (غالباً captcha أو حجب)")
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        _note(key, f"tiktok-page{label}: بيانات الصفحة غير مفهومة")
        return None

    music = None
    for mu in _find_key(data, "music"):
        if isinstance(mu, dict) and mu.get("playUrl"):
            music = mu["playUrl"]
            break

    # 1) الطريقة المعتادة: imagePost
    post = next((p for p in _find_key(data, "imagePost") if isinstance(p, dict)), None)
    images = []
    for im in (post or {}).get("images", []):
        urls = (im.get("imageURL") or {}).get("urlList") or []
        if urls:
            images.append(urls[0])
    if images:
        _note(key, f"tiktok-page{label} OK: images={len(images)} music={'نعم' if music else 'لا'}")
        return {"images": images[:35], "music": music}

    # 2) احتياطي: أي رابط صورة من النوع photomode في بيانات الصفحة
    seen, found = set(), []
    for t in _iter_strings(data):
        if t.startswith("http") and "photomode" in t:
            path = urlparse(t).path
            if path not in seen and re.search(r"(image|\.(jpe?g|webp|heic|png))", path + t, re.I):
                seen.add(path)
                found.append(t)
    if found:
        _note(key, f"tiktok-page{label} OK (photomode): images={len(found)} music={'نعم' if music else 'لا'}")
        return {"images": found[:35], "music": music}

    # تشخيص: إيه اللي جوه الصفحة؟
    scope = data.get("__DEFAULT_SCOPE__", {}) if isinstance(data, dict) else {}
    detail = scope.get("webapp.video-detail", {}) if isinstance(scope, dict) else {}
    item = (detail.get("itemInfo") or {}).get("itemStruct") if isinstance(detail, dict) else None
    _note(key, (
        f"tiktok-page{label}: مفيش صور | status={detail.get('statusCode') if isinstance(detail, dict) else '?'} "
        f"msg={str(detail.get('statusMsg') if isinstance(detail, dict) else '')[:40]} | "
        f"scope={list(scope.keys())[:5] if isinstance(scope, dict) else '?'} | "
        f"item={'نعم' if item else 'لا'} keys={list(item.keys())[:12] if isinstance(item, dict) else '-'}"
    ))
    return None


def _tiktok_page(url, key=None):
    """منشور صور تيك توك: بيقرأ بيانات الصفحة نفسها (من غير خدمة خارجية)."""
    variants = [url]
    if "/photo/" in url:
        variants.append(url.replace("/photo/", "/video/"))
    for i, u in enumerate(variants):
        label = "" if i == 0 else " (/video/)"
        try:
            page = _get_bytes(u, referer="https://www.tiktok.com/", timeout=25,
                              max_bytes=5_000_000).decode("utf-8", "ignore")
        except Exception as e:
            _note(key, f"tiktok-page{label}: {str(e)[:170]}")
            continue
        res = _parse_tiktok_page(page, key, label)
        if res:
            return res
    return None


def _scrape_video_url(url):
    """بيدوّر في صفحة الموقع على رابط فيديو مباشر (og:video / video / source / mp4 / m3u8)."""
    with _http_open(url) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "json" not in ctype and "text" not in ctype:
            return None
        page = r.read(2_000_000).decode("utf-8", "ignore")
        base = r.geturl()
    patterns = [
        r'<meta[^>]+(?:property|name)=["\']og:video(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:video(?::secure_url|:url)?["\']',
        r'<(?:source|video)[^>]+src=["\']([^"\']+)["\']',
        r'"contentUrl"\s*:\s*"([^"]+)"',
        r'(https?:(?:\\?/){2}[^"\'\s<>\\]+?\.' + VIDEO_EXTS + r'(?:\?[^"\'\s<>\\]*)?)',
    ]
    for pat in patterns:
        for m in re.finditer(pat, page, re.IGNORECASE):
            cand = _abs(base, m.group(1))
            path = urlparse(cand).path.lower()
            if re.search(r"\." + VIDEO_EXTS + r"$", path) or "og:video" in pat or "contentUrl" in pat:
                if cand.startswith("http") and not re.search(r"\.(jpg|jpeg|png|gif|webp|svg)$", path):
                    return cand
    return None


# ---- تشخيص + بدائل إضافية ----
COBALT_API_URL = os.environ.get("COBALT_API_URL", "").strip()   # اختياري: سيرفر cobalt (بتاعك أو عام)
COBALT_API_KEY = os.environ.get("COBALT_API_KEY", "").strip()
FB_DIAG = {}


def _note(key, text):
    """ملاحظات تشخيص بنعرضها للأدمن بس لو التحميل فشل."""
    if not key:
        return
    if len(FB_DIAG) > 100:
        FB_DIAG.pop(next(iter(FB_DIAG)))
    FB_DIAG.setdefault(key, [])
    if len(FB_DIAG[key]) < 12:
        FB_DIAG[key].append(text[:240])
    print("NOTE:", text[:240])


def diag_notes(key):
    return "\n".join(FB_DIAG.get(key, [])[-8:])


def _clean_err(e):
    return re.sub(r"\x1b\[[0-9;]*m", "", str(e)).strip()


def _candidate_urls(url):
    """الرابط نفسه + بدائل ليه (تيك توك: صفحة الـ embed بتشتغل أحياناً لما الصفحة العادية تتحجب)."""
    urls = [url]
    m = re.search(r"tiktok\.com/(?:@[\w.\-]+/)?video/(\d+)", url)
    if m:
        urls.append(f"https://www.tiktok.com/embed/{m.group(1)}")
    return urls


def ytdlp_run(url, opts, download, key=None):
    # منشورات الصور (السلايد) yt-dlp مابيدعمهاش — وصفحة الـ embed بترجّع منها صوت بصورة سودا.
    # فنروح للبديل (tikwm) على طول بدل ما نجرّب yt-dlp.
    if re.search(r"tiktok\.com/.*/photo/\d+", url):
        _note(key, "منشور صور: yt-dlp مابيدعمهوش، هنستخدم البديل")
        raise Exception("Unsupported URL (photo post): " + url)
    err = None
    for u in _candidate_urls(url):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(u, download=download)
        except Exception as e:
            if err is None:
                err = e
            _note(key, f"yt-dlp ({urlparse(u).path[:30]}): {_clean_err(e)[:200]}")
    raise err


def _cobalt(url, key=None):
    """بديل اختياري عن طريق سيرفر cobalt (لو COBALT_API_URL متضبط)."""
    if not COBALT_API_URL:
        return None
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": DESKTOP_UA}
    if COBALT_API_KEY:
        headers["Authorization"] = f"Api-Key {COBALT_API_KEY}"
    body = json.dumps({"url": url, "videoQuality": "1080", "downloadMode": "auto"}).encode()
    try:
        req = urllib.request.Request(COBALT_API_URL, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.loads(r.read(1_000_000).decode("utf-8", "ignore"))
    except Exception as e:
        err_body = ""
        if hasattr(e, "read"):
            try:
                err_body = e.read(300).decode("utf-8", "ignore")
            except Exception:
                pass
        _note(key, f"cobalt: {type(e).__name__} {err_body[:120]}")
        return None
    st = data.get("status")
    if st in ("tunnel", "redirect") and data.get("url"):
        return {"video": data["url"]}
    if st == "picker":
        vids = [p["url"] for p in data.get("picker", []) if p.get("type") == "video" and p.get("url")]
        if vids:
            return {"video": vids[0]}
        imgs = [p["url"] for p in data.get("picker", []) if p.get("type") == "photo" and p.get("url")]
        if imgs:
            return {"images": imgs[:35], "music": data.get("audio")}
    _note(key, f"cobalt: status={st} {str(data.get('error'))[:120]}")
    return None


def _tikwm_raw(url):
    """نداء tikwm (مع إعادة المحاولة لو الخدمة طلبت نستنى بين الطلبات)."""
    api = "https://www.tikwm.com/api/?" + urlencode({"url": url, "hd": 1})
    data = {}
    for _ in range(3):
        raw = _get_bytes(api, referer="https://www.tikwm.com/", timeout=25, max_bytes=1_000_000)
        data = json.loads(raw.decode("utf-8", "ignore"))
        if data.get("code") == 0 or "limit" not in str(data.get("msg", "")).lower():
            break
        time.sleep(1.5)
    return data


def _tikwm(url, key=None):
    """TikTok (بما فيه السلايد شو) عن طريق خدمة tikwm."""
    try:
        data = _tikwm_raw(url)
    except Exception as e:
        _note(key, f"tikwm: {type(e).__name__}: {str(e)[:170]}")
        return None
    if data.get("code") != 0 or not data.get("data"):
        _note(key, f"tikwm: code={data.get('code')} msg={str(data.get('msg'))[:100]}")
        return None
    d = data["data"]
    base = "https://www.tikwm.com"
    images = [_abs(base, i) for i in (d.get("images") or [])][:35]
    video = d.get("hdplay") or d.get("play")
    music = d.get("music")
    _note(key, f"tikwm OK: images={len(images)} video={'نعم' if video else 'لا'} music={'نعم' if music else 'لا'}")
    if images:
        return {"images": images, "music": _abs(base, music) if music else None}
    if "/photo/" in url:
        # منشور صور لكن tikwm مرجعش الصور: الفيديو اللي عنده صوت بس (صورة سودا) فمانبعتوش
        _note(key, "tikwm: منشور صور من غير روابط صور")
        return {"music_only": _abs(base, music or video)} if (music or video) else None
    if video:
        return {"video": _abs(base, video)}
    return None


def fallback_resolve(url, key=None):
    """يرجّع dict فيه video أو images(+music)، أو None."""
    key = key or url
    try:
        host = urlparse(url).netloc.lower()
        tries = list(dict.fromkeys([url, key]))
        if host.endswith("tiktok.com") or urlparse(key).netloc.lower().endswith("tiktok.com"):
            is_photo = "/photo/" in url
            for u in tries:
                # منشور صور: نقرا الصفحة نفسها الأول، وبعدين tikwm
                for fn in ((_tiktok_page, _tikwm) if is_photo else (_tikwm,)):
                    res = fn(u, key)
                    if res:
                        return res
        for u in tries:
            res = _cobalt(u, key)
            if res:
                return res
        v = _scrape_video_url(url)
        if v:
            return {"video": v}
        _note(key, "scrape: مفيش رابط فيديو في الصفحة")
    except Exception as e:
        _note(key, f"fallback: {type(e).__name__}: {str(e)[:100]}")
    return None


def _fetch_direct(u, dest, referer=None):
    """ينزّل ملف مباشر (mp4 / m3u8 ...) لمسار محلي. يرجّع المسار أو None."""
    try:
        if ".m3u8" in urlparse(u).path.lower():
            if not _is_public_host(urlparse(u).hostname or ""):
                return None
            _ff(["-user_agent", DESKTOP_UA, "-i", u, "-c", "copy",
                 "-bsf:a", "aac_adtstoasc", dest], timeout=900)
        else:
            if dest.endswith((".jpg", ".mp3")):
                try:
                    data = _get_bytes(u, referer=referer, timeout=30, max_bytes=25_000_000)
                    if data:
                        with open(dest, "wb") as f:
                            f.write(data)
                        return dest
                except Exception as e:
                    print("FETCH SMALL FAILED:", repr(e)[:160])
            with _http_open(u, referer=referer, timeout=30) as r:
                total = int(r.headers.get("Content-Length") or 0)
                if total > FB_MAX_BYTES:
                    raise UserError("الملف كبير جداً.")
                size = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = r.read(1024 * 256)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > FB_MAX_BYTES:
                            raise UserError("الملف كبير جداً.")
                        f.write(chunk)
        return dest if os.path.exists(dest) and os.path.getsize(dest) > 0 else None
    except UserError:
        raise
    except Exception as e:
        print("FETCH DIRECT ERROR:", repr(e))
        return None


def _normalize_image(src, dst):
    """يحوّل أي صورة (jpg/png/webp...) لـ JPEG 720x1280 بخلفية سودا. يرجّع True لو نجح."""
    try:
        _ff(["-i", src, "-frames:v", "1",
             "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,"
                    "pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,format=yuvj420p",
             "-q:v", "3", dst], timeout=60)
        if os.path.exists(dst) and os.path.getsize(dst) > 2000:
            return True
    except Exception as e:
        print("NORMALIZE (ffmpeg) FAILED:", repr(e)[:120])
    try:
        from PIL import Image
        im = Image.open(src)
        im.load()
        im = im.convert("RGB")
        w, h = im.size
        if w < 50 or h < 50:
            return False
        k = min(720 / w, 1280 / h)
        im = im.resize((max(1, int(w * k)), max(1, int(h * k))))
        canvas = Image.new("RGB", (720, 1280), (0, 0, 0))
        canvas.paste(im, ((720 - im.size[0]) // 2, (1280 - im.size[1]) // 2))
        canvas.save(dst, "JPEG", quality=90)
        return os.path.getsize(dst) > 2000
    except Exception as e:
        print("NORMALIZE (PIL) FAILED:", repr(e)[:120])
        return False


def slideshow_to_video(images, music, out, temp_dir):
    """صور (+موسيقى) -> فيديو، لمنشورات الصور/السلايد. كل صورة 3 ثواني."""
    slides = []
    for p in images:
        dst = os.path.join(temp_dir, f"slide_{len(slides):03d}.jpg")
        if _normalize_image(p, dst):
            slides.append(dst)
    if not slides:
        raise UserError("معرفتش أقرأ صور المنشور ده.")
    args = ["-framerate", "1/3", "-i", os.path.join(temp_dir, "slide_%03d.jpg")]
    if music:
        args += ["-stream_loop", "-1", "-i", music, "-shortest"]
    args += ["-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
             "-pix_fmt", "yuv420p"]
    if music:
        args += ["-c:a", "aac", "-b:a", "128k"]
    args += ["-movflags", "+faststart", out]
    _ff(args, timeout=600)
    return out


def fallback_download(url, temp_dir, media_type, quality, key=None):
    """بديل التحميل لما yt-dlp يفشل. يرجّع (مسار الملف, العرض) أو None."""
    fb = fallback_resolve(url, key)
    if not fb:
        return None
    referer = url
    if fb.get("music_only"):
        m = _fetch_direct(fb["music_only"], os.path.join(temp_dir, "music_only.mp3"), referer)
        if media_type == "audio" and m:
            return _to_mp3_quality(m, temp_dir, quality), 720
        raise UserError("المنشور ده صور ومقدرتش أجيب الصور منه حالياً. جرّب تحميله MP3.")
    if fb.get("images"):
        imgs = []
        for i, u in enumerate(fb["images"]):
            p = _fetch_direct(u, os.path.join(temp_dir, f"img_{i:02d}.jpg"), referer)
            if p:
                imgs.append(p)
        if not imgs:
            return None
        music = None
        if fb.get("music"):
            music = _fetch_direct(fb["music"], os.path.join(temp_dir, "music.mp3"), referer)
        if media_type == "audio":
            if not music:
                raise UserError("المنشور ده صور من غير موسيقى.")
            return music if quality is None else _to_mp3_quality(music, temp_dir, quality), 720
        vid = slideshow_to_video(imgs, music, os.path.join(temp_dir, "slideshow.mp4"), temp_dir)
        return vid, 720

    vid = _fetch_direct(fb["video"], os.path.join(temp_dir, "fb_video.mp4"), referer)
    if not vid:
        return None
    if media_type == "audio":
        return _to_mp3_quality(vid, temp_dir, quality), 720
    return vid, 720


def _to_mp3_quality(src, temp_dir, quality):
    out = os.path.join(temp_dir, "fb_audio.mp3")
    _ff(["-i", src, "-vn", "-c:a", "libmp3lame", "-b:a", f"{quality}k", out])
    return out


def diagnose_link(url):
    """تقرير تشخيص خطوة بخطوة لرابط (للأدمن)."""
    out = []
    try:
        import yt_dlp.version as _v
        out.append(f"yt-dlp: {_v.__version__}")
    except Exception:
        out.append("yt-dlp: ?")
    for h in ("www.tiktok.com", "www.tikwm.com"):
        try:
            with _http_open(f"https://{h}/", timeout=10) as r:
                out.append(f"🌐 {h}: HTTP {r.status}")
        except Exception as e:
            out.append(f"🌐 {h}: ❌ {type(e).__name__}: {str(e)[:80]}")

    final = url
    try:
        final = prepare_url(url)
        out.append(f"↪️ بعد فك الرابط: {final[:150]}")
    except Exception as e:
        out.append(f"↪️ فك الرابط فشل: {e}")

    try:
        with yt_dlp.YoutubeDL({**base_opts(), "skip_download": True}) as ydl:
            info = ydl.extract_info(final, download=False)
        out.append(f"✅ yt-dlp قرأ الرابط: {(info.get('title') or '')[:60]} | formats={len(info.get('formats') or [])}")
    except Exception as e:
        out.append("❌ yt-dlp: " + re.sub(r"\x1b\[[0-9;]*m", "", str(e))[:300])

    if urlparse(final).netloc.lower().endswith("tiktok.com"):
        try:
            raw = _tikwm_raw(final)
            d = raw.get("data") or {}
            out.append(
                f"tikwm: code={raw.get('code')} msg={str(raw.get('msg'))[:80]} | "
                f"video={'نعم' if (d.get('hdplay') or d.get('play')) else 'لا'} | صور={len(d.get('images') or [])}"
            )
        except Exception as e:
            out.append(f"❌ tikwm: {type(e).__name__}: {str(e)[:100]}")

    try:
        v = _scrape_video_url(final)
        out.append("scrape: " + (v[:120] if v else "مفيش رابط فيديو في الصفحة"))
    except Exception as e:
        out.append(f"❌ scrape: {type(e).__name__}: {str(e)[:100]}")
    return "\n".join(out)


def download_media(url, media_type, quality, premium):
    orig = url
    FB_DIAG.pop(orig, None)
    url = prepare_url(url)
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

        ytdlp_error = None
        info = {}
        try:
            info = ytdlp_run(url, opts, True, orig)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next((e for e in info["entries"] if e), info)
        except Exception as e:
            ytdlp_error = e
            print("YTDLP DOWNLOAD ERROR (trying fallback):", repr(e)[:200])

        if ytdlp_error is None:
            files = [p for p in Path(temp_dir).glob("*") if p.is_file()]
            if media_type == "audio":
                files = [p for p in files if p.suffix == ".mp3"] or files
            if not files:
                raise UserError("لم يتم العثور على الملف بعد التحميل.")
            filename = str(max(files, key=lambda p: p.stat().st_size))
            width = info.get("width") or 720
        else:
            fb = fallback_download(
                url, temp_dir, media_type, quality if media_type == "audio" else None, orig
            )
            if not fb:
                raise ytdlp_error
            filename, width = fb

        if media_type == "video" and not premium:
            filename = add_watermark(filename, temp_dir, width)

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
    "vfilter": ("🎨 فلتر للفيديو ⭐", True),
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

    if action == "vfilter":
        if param not in FILTERS:
            raise UserError("فلتر غير مدعوم.")
        out = os.path.join(temp_dir, "filtered.mp4")
        vf = FILTERS[param][1] + ",scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
        _ff(["-i", src, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out], timeout=900)
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
    url = prepare_url(url)
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
        print("UNSUPPORTED URL ERROR:", text[:300])
        return "معرفتش أحمّل من الرابط ده دلوقتي.\nتأكد إن الفيديو عام (مش خاص)، أو جرب رابط تاني."
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
    "🎬 " + BOT_NAME + "\n\n"
    "اختار اللي عايزه من القايمة، أو ابعت مباشرة:\n"
    "• رابط فيديو ← أحمله لك\n"
    "• فيديو أو ملف صوت ← أدوات (MP3، فويس، قص، سرعة، ضغط...)\n"
    "• نص عادي أو ملف .txt ← أحوله لصوت\n"
    "• صورة ← فلاتر • /imagine ← صورة بالذكاء الاصطناعي 🎨"
)

MENU_HELP = {
    "imagine": "🎨 *صورة بالذكاء الاصطناعي*\n\nاكتب وصف الصورة (عربي أو إنجليزي) وابعته دلوقتي.\n\nمجاناً: عدد محدود في اليوم (بعلامة صغيرة).\n⭐ Premium: عدد أكبر، جودة HD، بدون علامة، وأشكال مختلفة (طولي/عرضي).",
    "video": "🎬 *فيديو بالذكاء الاصطناعي* ⭐\n\nاكتب وصف الفيديو وابعته دلوقتي، أو اعمل صورة بـ /imagine وحوّلها لفيديو.\nالميزة دي لمشتركي Premium.",
    "filters": "🖼 *فلاتر الصور*\n\nابعت أي صورة وهتظهر لك الفلاتر: أبيض وأسود، سيبيا، قديم، دافئ، بارد...\n⭐ Premium: فلاتر متقدمة (رسم بالقلم، كرتوني، توهج، سينمائي...) وللفيديو كمان.",
    "dl": "🎥 *تحميل من رابط*\n\nابعت رابط الفيديو (تيك توك، إنستجرام، فيسبوك، يوتيوب، تويتر...) وأنا أحمله لك.",
    "mp3": "🎛 *أدوات الصوت والفيديو*\n\nابعت أي فيديو أو ملف صوت أو فويس (حتى 20MB) وهتختار:\n• 🎵 MP3 • 🎤 فويس • ⏩ سرعة الصوت • 🔊 تقوية الصوت\n• 🔈 باس • 🐿 صوت رفيع • 🦁 صوت تخين • 🤫 حذف الصمت • 🌊 صوت←فيديو\n• ✂️ قص مقطع ⭐ • 🗜 ضغط الفيديو ⭐ • 📱 ريلز 9:16 ⭐\n• للفيديو: 🖼 صورة • 🎞 مقطع متحرك • 🎬 سرعة • 🔄 تدوير • 🔇 كتم\n\nلو الفيديو أكبر، ابعت رابطه بدل الملف.",
    "tts": "🔊 *نص ← صوت*\n\nاكتب أو ابعت أي نص (حتى 500 حرف) واختار الصوت والسرعة وأرجعه لك MP3.",
}


def share_button():
    """زر مشاركة البوت (بيفتح قايمة تليجرام لاختيار الأصدقاء)."""
    link = f"https://t.me/{BOT_USERNAME}"
    text = "🎬 بوت تحميل فيديوهات من تيك توك ويوتيوب وانستجرام وغيرهم — ومجاني!"
    url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(text, safe='')}"
    return InlineKeyboardButton("📤 شارك البوت مع أصحابك", url=url)


def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 تحميل من رابط", callback_data="menu:dl")],
        [InlineKeyboardButton("🎛 أدوات الصوت والفيديو", callback_data="menu:mp3")],
        [InlineKeyboardButton("🔊 نص ← صوت", callback_data="menu:tts")],
        [InlineKeyboardButton("🎨 صورة بالذكاء الاصطناعي", callback_data="menu:imagine")],
        [InlineKeyboardButton("🎬 فيديو بالذكاء الاصطناعي ⭐", callback_data="menu:video")],
        [InlineKeyboardButton("🖼 فلاتر الصور", callback_data="menu:filters")],
        [InlineKeyboardButton("⭐ Premium", callback_data="premium")],
        [share_button()],
    ])


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data.split(":", 1)[1]
    if key == "home":
        await q.edit_message_text(START_TEXT, reply_markup=main_menu_kb())
        return
    if key == "imagine":
        context.user_data["await_ai"] = "image"
    elif key == "video":
        context.user_data["await_ai"] = "video"
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
    mode = context.user_data.get("await_ai")
    if mode and not match:
        context.user_data["await_ai"] = None
        if mode == "image":
            await run_imagine(update.message, context, update.effective_user.id, text)
        else:
            await run_video(update.message, context, update.effective_user.id, text)
        return
    if match:
        context.user_data["await_ai"] = None
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
                caption="🔊 @" + BOT_USERNAME,
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
        cap = "@" + BOT_USERNAME
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

    if action == "vfilter" and len(parts) == 2:
        await q.edit_message_text("🎨 اختار الفلتر:", reply_markup=filters_kb("m:vfilter:", True))
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
                        await q.message.reply_photo(photo=f, caption="🖼 @" + BOT_USERNAME,
                                                    write_timeout=120, read_timeout=120)
                    except Exception as send_error:
                        print("PHOTO SEND ERROR:", repr(send_error))
                        f.seek(0)
                        await q.message.reply_document(document=f, write_timeout=120, read_timeout=120)
                else:
                    await q.message.reply_document(document=f, caption="📝 @" + BOT_USERNAME,
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
        "⭐ " + BOT_NAME + " Premium\n",
        "• بدون علامة مائية",
        "• جودات 1080p وأعلى",
        f"• صور بالذكاء الاصطناعي: {PREMIUM_IMG_DAILY} في اليوم (بدل {FREE_IMG_DAILY}) HD وبدون علامة",
        f"• فيديو بالذكاء الاصطناعي: {PREMIUM_VIDEO_DAILY} في اليوم + تحويل الصورة لفيديو",
        "• فلاتر متقدمة للصور والفيديو",
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
    if is_admin(uid):
        head = "👑 حسابك: صاحب البوت — Premium دائم."
    elif is_premium(uid):
        head = f"⭐ اشتراكك Premium فعال لحد {premium_until(uid):%Y-%m-%d}."
    else:
        head = "حسابك مجاني (حتى 720p وبعلامة مائية).\nللاشتراك: /premium"
    await update.message.reply_text(
        f"{head}\n\n🎨 صور AI المتبقية اليوم: {fmt_left(remaining(uid, 'img'))}"
        f"\n🎬 فيديو AI المتبقي اليوم: {fmt_left(remaining(uid, 'video'))}"
    )


HELP_TEXT = (
    "📖 أوامر البوت:\n\n"
    "/start — القائمة الرئيسية\n"
    "/help — الأوامر وشرح الاستخدام\n"
    "/premium — الاشتراك والعروض ⭐\n"
    "/status — حالة اشتراكي\n"
    "/history — آخر تحميلاتك\n"
    "/imagine — صورة بالذكاء الاصطناعي 🎨\n"
    "/video — فيديو بالذكاء الاصطناعي ⭐\n"
    "/invite — ادعُ أصحابك واكسب Premium 🎁\n"
    "/redeem — تفعيل كود هدية 🎟\n"
    "/settings — إعداداتي (الصوت والسرعة الافتراضية)\n"
    "/feedback — ابعت اقتراح أو مشكلة للإدارة\n"
    "/cancel — إلغاء العملية الحالية\n\n"
    "💡 طريقة الاستخدام:\n"
    "• ابعت رابط فيديو ← تحميل (فيديو / MP3 / غلاف / ترجمة)\n"
    "• ابعت فيديو أو صوت ← أدوات (MP3، فويس، قص، سرعة، ضغط...)\n"
    "• اكتب نص عادي أو ابعت ملف .txt ← أحوله لصوت\n"
    "• ابعت صورة ← فلاتر 🎨"
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["await_cut"] = False
    await update.message.reply_text("✅ تم الإلغاء. ابعت رابط أو ملف أو نص عشان نبدأ من جديد.")


async def apply_profile(bot):
    """يضبط اسم ووصف البوت بالعربي في تليجرام (مرة واحدة لكل تغيير)."""
    import hashlib
    if os.environ.get("AUTO_PROFILE", "1") == "0":
        return  # إنت ضابط الاسم والوصف يدوي من BotFather
    h = hashlib.md5((TG_NAME + TG_SHORT + TG_DESC).encode("utf-8")).hexdigest()
    if get_pref(0, "profile_hash") == h:
        return
    try:
        for lang in (None, "ar"):
            kw = {"language_code": lang} if lang else {}
            await bot.set_my_name(name=TG_NAME, **kw)
            await bot.set_my_short_description(short_description=TG_SHORT, **kw)
            await bot.set_my_description(description=TG_DESC, **kw)
        set_pref(0, "profile_hash", h)
        print("PROFILE UPDATED:", TG_NAME)
    except Exception as e:
        # تغيير الاسم له حد في تليجرام؛ هنحاول تاني في التشغيل الجاي
        print("PROFILE UPDATE ERROR:", repr(e))


async def _post_init_inner(app):
    """قايمة الاختصارات اللي بتظهر في زر (Menu) جنب خانة الكتابة."""
    global BOT_USERNAME, WATERMARK_TEXT
    cleanup_temp(max_age=600)
    if getattr(app.bot, "username", None):
        BOT_USERNAME = app.bot.username
        WATERMARK_TEXT = "@" + BOT_USERNAME
    await apply_profile(app.bot)
    user_cmds = [
        BotCommand("start", "القائمة الرئيسية"),
        BotCommand("help", "الأوامر وشرح الاستخدام"),
        BotCommand("premium", "الاشتراك والعروض ⭐"),
        BotCommand("status", "حالة اشتراكي"),
        BotCommand("history", "آخر تحميلاتك"),
        BotCommand("imagine", "صورة بالذكاء الاصطناعي 🎨"),
        BotCommand("video", "فيديو بالذكاء الاصطناعي ⭐"),
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
                    BotCommand("aicheck", "فحص خدمات الذكاء الاصطناعي (أدمن)"),
                    BotCommand("testlink", "تشخيص رابط مش بيتحمل (أدمن)"),
                    BotCommand("broadcast", "رسالة لكل المستخدمين (أدمن)"),
                    BotCommand("gencode", "إنشاء كود هدية (أدمن)"),
                    BotCommand("dropcode", "كود هدية وينزل في الجروب (أدمن)"),
                    BotCommand("dropchat", "تحديد جروب الأكواد (أدمن)"),
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


async def post_init(app):
    """إعدادات إضافية بعد التشغيل — لو فشلت، البوت يكمل شغل عادي."""
    try:
        await _post_init_inner(app)
    except Exception as e:
        print("POST_INIT ERROR (مش مؤثر على التشغيل):", repr(e))
    print(f"✅ البوت اشتغل: @{getattr(app.bot, 'username', '?')} (النسخة: {BOT_VERSION})")


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
        text = msg if msg == MAINTENANCE_TEXT else f"❌ مقدرتش أقرأ الرابط.\n\n{msg}"
        if is_admin(q.from_user.id) and diag_notes(url):
            text += "\n\n🔧 تشخيص (ظاهر للأدمن بس):\n" + diag_notes(url)
        await q.edit_message_text(text[:3800])
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

        caption = "🎬 @" + BOT_USERNAME
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
        await q.edit_message_text("✅ تم التحميل بنجاح.", reply_markup=InlineKeyboardMarkup([[share_button()]]))

    except Exception as e:
        print("DOWNLOAD ERROR:", repr(e))
        msg = short_error(e)
        text = msg if msg == MAINTENANCE_TEXT else f"❌ حصل خطأ أثناء التحميل.\n\n{msg}"
        if is_admin(q.from_user.id) and diag_notes(url):
            text += "\n\n🔧 تشخيص (ظاهر للأدمن بس):\n" + diag_notes(url)
        await q.edit_message_text(text[:3800])
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


async def error_handler(update, context):
    print("BOT ERROR:", repr(context.error))
    # أخطاء الشبكة المؤقتة: متبعتش رسالة صيانة
    if isinstance(context.error, (NetworkError, TimedOut)):
        return
    # مانبعتش رسالة صيانة إلا في الشات الخاص (مش في القنوات/الجروبات)
    chat = update.effective_chat if isinstance(update, Update) else None
    if not chat or chat.type != "private":
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
    # البوت بيشتغل في الشات الخاص بس: نتجاهل القنوات والجروبات بدون أي رد
    chat = update.effective_chat
    if update.channel_post or update.edited_channel_post or (chat and chat.type != "private"):
        # استثناء: الأدمن يكتب /chatid في الجروب ياخد رقمه (محتاجه لـ /dropchat)
        m = update.effective_message
        u = update.effective_user
        if (m and u and is_admin(u.id) and chat
                and (getattr(m, "text", None) or "").split("@")[0].strip() == "/chatid"):
            try:
                await m.reply_text(f"🆔 رقم الشات: {chat.id}")
            except Exception as e:
                print("CHATID ERROR:", repr(e))
        raise ApplicationHandlerStop
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


def drop_chat_id():
    v = get_pref(0, "drop_chat") or os.environ.get("DROP_CHAT_ID", "").strip()
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def drop_message(code, days, uses, username):
    link = f"https://t.me/{username}"
    text = (
        "🎁 <b>هدية Premium!</b>\n\n"
        f"⭐ <b>{days} يوم Premium</b> — لأول <b>{uses}</b> "
        + ("شخص" if uses == 1 else "أشخاص") + " بس!\n\n"
        "📋 اضغط على الأمر ده ينتسخ لوحده:\n"
        f"<code>/redeem {code}</code>\n\n"
        f"وابعته للبوت في الخاص 👉 @{username}\n\n"
        "⚡ الأسرع ياخدها!"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🤖 افتح البوت", url=link)]])
    return text, kb


async def dropchat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dropchat <رقم الجروب> — الجروب اللي هتتنزل فيه الأكواد."""
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        cur = drop_chat_id()
        await update.message.reply_text(
            f"الجروب الحالي: {cur if cur else 'غير محدد'}\n\n"
            "عشان تحدده: ضيف البوت للجروب (يفضل أدمن)، واكتب في الجروب /chatid ياخد رقمه، "
            "وبعدين هنا: /dropchat <الرقم>"
        )
        return
    try:
        cid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("الرقم غير صحيح. مثال: /dropchat -1001234567890")
        return
    try:
        chat = await context.bot.get_chat(cid)
    except Exception as e:
        await update.message.reply_text(f"❌ مقدرتش أوصل للشات ده: {e}\nتأكد إن البوت مضاف فيه.")
        return
    set_pref(0, "drop_chat", cid)
    await update.message.reply_text(f"✅ تم. الأكواد هتتنزل في: {chat.title or cid}")


async def dropcode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dropcode <أيام> [عدد الأشخاص] — يعمل كود وينزّله في الجروب برسالة جاهزة."""
    if not is_admin(update.effective_user.id):
        return
    try:
        days = int(context.args[0])
        uses = int(context.args[1]) if len(context.args) > 1 else 1
        if days < 1 or uses < 1 or days > 3650 or uses > 10000:
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text(
            "الاستخدام: /dropcode <الأيام> [عدد الأشخاص]\nمثال: /dropcode 1 5"
        )
        return
    code = make_code(days, uses)
    text, kb = drop_message(code, days, uses, context.bot.username)
    cid = drop_chat_id()
    if not cid:
        await update.message.reply_text(
            f"🎟 الكود: {code}\n(مفيش جروب محدد لسه، حدده بـ /dropchat وانسخ الرسالة دي مؤقتاً)\n\n"
            + re.sub(r"<[^>]+>", "", text)
        )
        return
    try:
        await context.bot.send_message(
            cid, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True
        )
        await update.message.reply_text(f"✅ اتنزل الكود {code} في الجروب ({days} يوم — {uses} أشخاص).")
    except Exception as e:
        print("DROPCODE ERROR:", repr(e))
        await update.message.reply_text(
            f"❌ معرفتش أنزّل في الجروب: {e}\n\n🎟 الكود اتعمل: {code}\n\n" + re.sub(r"<[^>]+>", "", text)
        )


async def testlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testlink <رابط> — تشخيص ليه رابط مش بيتحمل (للأدمن)."""
    if not is_admin(update.effective_user.id):
        return
    m = URL_RE.search(" ".join(context.args or []))
    if not m:
        await update.message.reply_text("الاستخدام: /testlink <الرابط>")
        return
    status = await update.message.reply_text("🔎 جاري التشخيص...")
    try:
        text = await asyncio.to_thread(diagnose_link, m.group(0))
    except Exception as e:
        text = f"فشل التشخيص: {e}"
    await status.edit_text(text[:3800], disable_web_page_preview=True)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    s = get_stats()
    top = "\n".join(f"  • {k.replace('tool_', '')}: {c}" for k, c in s["top"]) or "  —"
    await update.message.reply_text(
        f"📊 إحصائيات البوت (النسخة: {BOT_VERSION})\n\n"
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


TEMP_PREFIXES = {"amd_", "amx_", "amc_", "amt_", "amf_", "amb_", "ami_", "amv_", "amp_"}


def cleanup_temp(max_age=7200):
    """يمسح مجلدات مؤقتة قديمة اتساب من عمليات اتقطعت (بيوفر مساحة)."""
    removed, now_t = 0, time.time()
    try:
        for p in Path(tempfile.gettempdir()).glob("am?_*"):
            if p.name[:4] in TEMP_PREFIXES and p.is_dir() and now_t - p.stat().st_mtime > max_age:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
    except Exception as e:
        print("CLEANUP ERROR:", repr(e))
    if removed:
        print(f"CLEANUP: اتمسح {removed} مجلد مؤقت قديم")


async def expiry_loop(bot):
    await asyncio.sleep(60)
    while True:
        try:
            await remind_expiring(bot)
            cleanup_temp()
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
# AI: IMAGES / VIDEO / FILTERS
# =========================

def _envi(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# الحصص اليومية (تتجدد كل يوم UTC). عدّلها من الـ Variables لو عايز.
FREE_IMG_DAILY = _envi("FREE_IMG_DAILY", 3)
PREMIUM_IMG_DAILY = _envi("PREMIUM_IMG_DAILY", 30)
FREE_VIDEO_DAILY = _envi("FREE_VIDEO_DAILY", 0)
PREMIUM_VIDEO_DAILY = _envi("PREMIUM_VIDEO_DAILY", 2)

# مزوّد الصور: pollinations (بدون مفتاح) أو hf (Hugging Face، محتاج HF_TOKEN)
IMG_PROVIDER = os.environ.get("IMG_PROVIDER", "pollinations").strip().lower()
POLLINATIONS_KEY = os.environ.get("POLLINATIONS_KEY", "").strip()
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
HF_IMAGE_URL = os.environ.get(
    "HF_IMAGE_URL",
    "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
).strip()
# الفيديو: fal.ai (محتاج FAL_KEY، وبيتحاسب بالطلب). أسماء الموديلات بتتغير — راجعها في fal.ai
FAL_KEY = os.environ.get("FAL_KEY", "").strip()
FAL_BASE = os.environ.get("FAL_BASE", "https://queue.fal.run").rstrip("/")
FAL_VIDEO_MODEL = os.environ.get("FAL_VIDEO_MODEL", "fal-ai/ltx-video").strip()
FAL_I2V_MODEL = os.environ.get("FAL_I2V_MODEL", "fal-ai/ltx-video/image-to-video").strip()

IMG_AI_LIMIT = asyncio.Semaphore(3)
VIDEO_AI_LIMIT = asyncio.Semaphore(2)

IMG_SHAPES = {          # الشكل: (العرض, الارتفاع)
    "sq": (1024, 1024),
    "pt": (832, 1216),
    "ls": (1216, 832),
}
FREE_IMG_SIZE = (768, 768)

BLOCKED_RE = re.compile(
    r"(nude|naked|nsfw|porn|\bsex|erotic|lingerie|topless|hentai|fetish|boobs?|nipple|genital|"
    r"عاري|عارية|عريان|إباحي|اباحي|سكس|جنس|نيك|إغراء|اغراء|ثدي|مؤخرة)",
    re.IGNORECASE,
)


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def used_today(user_id, kind):
    con = db()
    row = con.execute(
        "SELECT n FROM usage WHERE user_id = ? AND day = ? AND kind = ?",
        (user_id, _today(), kind),
    ).fetchone()
    con.close()
    return row[0] if row else 0


def add_usage(user_id, kind):
    con = db()
    con.execute(
        "INSERT INTO usage (user_id, day, kind, n) VALUES (?, ?, ?, 1) "
        "ON CONFLICT(user_id, day, kind) DO UPDATE SET n = n + 1",
        (user_id, _today(), kind),
    )
    con.commit()
    con.close()


def quota_for(user_id, kind):
    if is_admin(user_id):
        return 10 ** 9
    if kind == "img":
        return PREMIUM_IMG_DAILY if is_premium(user_id) else FREE_IMG_DAILY
    return PREMIUM_VIDEO_DAILY if is_premium(user_id) else FREE_VIDEO_DAILY


def remaining(user_id, kind):
    return max(0, quota_for(user_id, kind) - used_today(user_id, kind))


def fmt_left(n):
    return "∞" if n > 10 ** 6 else str(n)


# ---------- شبكة ----------

def _post(url, body, headers, timeout=120, max_bytes=15_000_000):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(max_bytes)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(300).decode("utf-8", "ignore")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} {detail[:150]}")


def _image_kind(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


# ---------- الصور ----------

def translate_prompt(p):
    """الوصف العربي -> إنجليزي (الموديلات بتفهم الإنجليزي أحسن). لو فشل بنكمّل بالأصلي."""
    try:
        instr = ("Translate this image description to English. "
                 "Output only the English prompt, no quotes, no explanation: " + p)
        data = _get_bytes("https://text.pollinations.ai/" + quote(instr, safe=""),
                          timeout=30, max_bytes=20_000)
        t = data.decode("utf-8", "ignore").strip().strip('"').strip()
        if 3 <= len(t) <= 600 and not t.lstrip().startswith("<"):
            return t
    except Exception as e:
        print("TRANSLATE FAILED:", repr(e)[:120])
    return p


def prepare_prompt(prompt):
    p = re.sub(r"\s+", " ", prompt or "").strip()[:400]
    if len(p) < 3:
        raise UserError("اكتب وصف أطول للصورة.")
    if BLOCKED_RE.search(p):
        raise UserError("الوصف ده مخالف لسياسة الاستخدام. جرّب وصف تاني.")
    if re.search(r"[\u0600-\u06FF]", p):
        p = translate_prompt(p)
        if BLOCKED_RE.search(p):
            raise UserError("الوصف ده مخالف لسياسة الاستخدام. جرّب وصف تاني.")
    return p


def generate_image_bytes(prompt, w, h, seed):
    if IMG_PROVIDER == "hf":
        if not HF_TOKEN:
            raise UserError("خدمة الصور مش متفعلة من الإدارة.")
        body = json.dumps({"inputs": prompt, "parameters": {"width": w, "height": h}}).encode()
        data = _post(HF_IMAGE_URL, body, {
            "Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json",
            "Accept": "image/png"}, timeout=120)
    else:
        params = {"width": w, "height": h, "seed": seed, "model": "flux",
                  "nologo": "true", "safe": "true"}
        if POLLINATIONS_KEY:
            params["token"] = POLLINATIONS_KEY
        url = f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}?{urlencode(params)}"
        data = _get_bytes(url, timeout=90, max_bytes=15_000_000)
    if not _image_kind(data):
        raise UserError("الخدمة مرجّعتش صورة (ممكن تكون مشغولة). جرّب بعد شوية.")
    return data


def watermark_photo(img):
    """علامة البوت في ركن الصورة (للمجاني)."""
    tmp = tempfile.mktemp(suffix=".png")
    try:
        make_watermark_png(tmp, img.width)
        wm = Image.open(tmp).convert("RGBA")
        img = img.convert("RGBA")
        img.alpha_composite(wm, (max(0, img.width - wm.width - 14), max(0, img.height - wm.height - 14)))
        return img.convert("RGB")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def make_ai_image(prompt, shape, premium, temp_dir, prepared=False, seed=None):
    """يرجّع (مسار الصورة JPEG, الوصف الإنجليزي)."""
    p_en = prompt if prepared else prepare_prompt(prompt)
    if premium:
        w, h = IMG_SHAPES.get(shape, IMG_SHAPES["sq"])
    else:
        w, h = FREE_IMG_SIZE
    seed = seed if seed is not None else int(time.time() * 1000) % 1_000_000
    data = generate_image_bytes(p_en, w, h, seed)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if not premium:
        img = watermark_photo(img)
    out = os.path.join(temp_dir, "ai.jpg")
    img.save(out, "JPEG", quality=92)
    return out, p_en


# ---------- الفيديو (fal.ai) ----------

def _fal_request(method, url, payload=None):
    headers = {"Authorization": f"Key {FAL_KEY}", "Accept": "application/json",
               "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read(5_000_000).decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(300).decode("utf-8", "ignore")
        except Exception:
            pass
        _note("ai_video", f"fal HTTP {e.code}: {body[:200]}")
        if e.code in (401, 403):
            raise UserError("ميزة الفيديو مش متاحة دلوقتي (مشكلة في إعدادات الخدمة).")
        if e.code == 402:
            raise UserError("ميزة الفيديو متوقفة مؤقتاً. جرّب بعدين.")
        if e.code == 422:
            raise UserError("الوصف مرفوض من الخدمة. جرّب وصف تاني.")
        raise UserError("خدمة الفيديو مشغولة دلوقتي. جرّب بعد شوية.")


def fal_generate(model, payload, timeout=480):
    sub = _fal_request("POST", f"{FAL_BASE}/{model}", payload)
    status_url, resp_url = sub.get("status_url"), sub.get("response_url")
    if not status_url or not resp_url:
        _note("ai_video", f"fal: رد غير متوقع {str(sub)[:150]}")
        raise UserError("رد الخدمة غير متوقع. جرّب بعد شوية.")
    t0 = time.time()
    while True:
        st = _fal_request("GET", status_url)
        s = st.get("status")
        if s == "COMPLETED":
            break
        if s in ("FAILED", "ERROR", "CANCELLED"):
            _note("ai_video", f"fal status={s} {str(st)[:150]}")
            raise UserError("الخدمة فشلت في إنشاء الفيديو. جرّب وصف تاني.")
        if time.time() - t0 > timeout:
            raise UserError("الفيديو أخد وقت طويل. جرّب بعد شوية.")
        time.sleep(4)
    res = _fal_request("GET", resp_url)
    video = res.get("video")
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        for t in _iter_strings(res):
            if t.startswith("http") and re.search(r"\.(mp4|webm|mov)(\?|$)", t):
                url = t
                break
    if not url:
        _note("ai_video", f"fal: مفيش فيديو في الرد {str(res)[:150]}")
        raise UserError("الخدمة مرجّعتش فيديو. جرّب بعد شوية.")
    return url


def make_ai_video(prompt, temp_dir, image_bytes=None):
    p_en = prepare_prompt(prompt) if prompt else "animate this image naturally, subtle cinematic motion"
    if image_bytes:
        im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        im.thumbnail((1280, 1280))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        url = fal_generate(FAL_I2V_MODEL, {"prompt": p_en, "image_url": uri})
    else:
        url = fal_generate(FAL_VIDEO_MODEL, {"prompt": p_en})
    out = _fetch_direct(url, os.path.join(temp_dir, "ai_video.mp4"))
    if not out:
        raise UserError("معرفتش أنزّل الفيديو الناتج. جرّب تاني.")
    return out


# ---------- الفلاتر (ffmpeg: بتشتغل على الصور والفيديو) ----------

FILTERS = {
    # key: (الاسم, فلتر ffmpeg, Premium؟)
    "bw": ("⚫ أبيض وأسود", "hue=s=0,eq=contrast=1.1", False),
    "sepia": ("🟤 سيبيا", "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131", False),
    "vintage": ("📷 قديم", "curves=preset=vintage,vignette=PI/5", False),
    "warm": ("🔥 دافئ", "colorbalance=rs=.12:gs=.02:bs=-.12:rm=.1:bm=-.1,eq=saturation=1.1", False),
    "cool": ("❄️ بارد", "colorbalance=rs=-.1:bs=.15:rm=-.08:bm=.12,eq=saturation=1.05", False),
    "vivid": ("🌈 زاهي", "eq=saturation=1.6:contrast=1.1", False),
    "cinema": ("🎬 سينمائي", "eq=contrast=1.15:saturation=1.2,colorbalance=rs=.1:bs=-.1:rh=.08:bh=-.08,vignette", True),
    "sketch": ("✏️ رسم بالقلم", "edgedetect=low=0.1:high=0.3,negate", True),
    "poster": ("🖍 كرتوني", "lutyuv=y='trunc(val/32)*32':u='trunc(val/32)*32':v='trunc(val/32)*32',eq=saturation=1.4", True),
    "glow": ("✨ توهج", "split[a][b];[b]gblur=sigma=20,eq=brightness=0.05[g];[a][g]blend=all_mode=screen:all_opacity=0.6", True),
    "blur": ("🌫 ضبابي", "gblur=sigma=6", True),
    "sharp": ("🔍 حدة عالية", "unsharp=5:5:1.5:5:5:0.0", True),
    "pixel": ("🟦 بيكسل", "scale=trunc(iw/14/2)*2:trunc(ih/14/2)*2:flags=neighbor,scale=iw*14:ih*14:flags=neighbor", True),
    "neg": ("🔄 نيجاتيف", "negate", True),
    "mirror": ("🪞 مرآة", "hflip", True),
}


def filters_kb(prefix, premium):
    rows, row = [], []
    for key, (label, _vf, prem) in FILTERS.items():
        row.append(InlineKeyboardButton(label + (" ⭐" if prem else ""), callback_data=f"{prefix}{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def apply_filter_image(src, dst, key):
    _ff(["-i", src, "-vf", FILTERS[key][1], "-frames:v", "1", "-q:v", "2", dst], timeout=120)
    if not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise UserError("معرفتش أطبّق الفلتر على الصورة دي.")
    return dst


# ---------- handlers ----------

def ai_image_kb(premium):
    lock = "" if premium else " ⭐"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 صورة تانية", callback_data="ai:again"),
         InlineKeyboardButton("🎨 فلاتر", callback_data="ai:filters")],
        [InlineKeyboardButton("⬜ مربع" + lock, callback_data="ai:shape:sq"),
         InlineKeyboardButton("📱 طولي" + lock, callback_data="ai:shape:pt"),
         InlineKeyboardButton("🖥 عرضي" + lock, callback_data="ai:shape:ls")],
        [InlineKeyboardButton("🎬 حوّلها لفيديو ⭐", callback_data="ai:i2v")],
    ])


def _admin_diag(uid, key):
    return ("\n\n🔧 " + diag_notes(key)) if is_admin(uid) and diag_notes(key) else ""


async def run_imagine(msg, context, uid, prompt, shape="sq", prepared=False):
    premium = is_premium(uid)
    if remaining(uid, "img") <= 0:
        if premium:
            await msg.reply_text(f"⛔ خلّصت حصتك النهارده ({PREMIUM_IMG_DAILY} صورة). بتتجدد بكرة.")
        else:
            await msg.reply_text(
                f"⛔ خلّصت صورك المجانية النهارده ({FREE_IMG_DAILY}).\n"
                f"⭐ مع Premium: {PREMIUM_IMG_DAILY} صورة في اليوم بجودة HD وبدون علامة — /premium"
            )
        return
    if context.user_data.get("ai_busy"):
        await msg.reply_text("⏳ استنى لحد ما الطلب الحالي يخلص.")
        return
    context.user_data["ai_busy"] = True
    status = await msg.reply_text("🎨 جاري إنشاء الصورة... (حوالي 20-40 ثانية)")
    temp_dir = tempfile.mkdtemp(prefix="ami_")
    FB_DIAG.pop("ai_image", None)
    try:
        async with IMG_AI_LIMIT:
            path, p_en = await asyncio.to_thread(
                make_ai_image, prompt, shape, premium, temp_dir, prepared)
        add_usage(uid, "img")
        log_event(uid, "tool_ai_image", prompt[:80])
        left = fmt_left(remaining(uid, "img"))
        orig = context.user_data.get("last_ai", {}).get("orig") if prepared else prompt
        with open(path, "rb") as f:
            sent = await msg.reply_photo(
                photo=f, caption=f"🎨 {(orig or prompt)[:200]}\n\nالمتبقي اليوم: {left}",
                reply_markup=ai_image_kb(premium), write_timeout=120, read_timeout=120)
        context.user_data["photo"] = {"file_id": sent.photo[-1].file_id}
        context.user_data["last_ai"] = {"prompt": p_en, "orig": orig or prompt, "shape": shape}
        try:
            await status.delete()
        except Exception:
            pass
    except UserError as e:
        await status.edit_text(f"❌ {e}")
    except Exception as e:
        print("AI IMAGE ERROR:", repr(e)[:300])
        await status.edit_text(MAINTENANCE_TEXT + (f"\n\n🔧 {repr(e)[:300]}" if is_admin(uid) else ""))
    finally:
        context.user_data["ai_busy"] = False
        shutil.rmtree(temp_dir, ignore_errors=True)


async def run_video(msg, context, uid, prompt, image_file_id=None):
    if not FAL_KEY:
        await msg.reply_text(
            "🎬 ميزة الفيديو بالذكاء الاصطناعي لسه مش متفعلة."
            + ("\n(للأدمن: ضيف FAL_KEY في الـ Variables)" if is_admin(uid) else "")
        )
        return
    if remaining(uid, "video") <= 0:
        if is_premium(uid) or is_admin(uid):
            await msg.reply_text(f"⛔ خلّصت حصة الفيديو النهارده ({PREMIUM_VIDEO_DAILY}). بتتجدد بكرة.")
        else:
            await msg.reply_text(
                f"🎬 الفيديو بالذكاء الاصطناعي لمشتركي Premium ⭐ ({PREMIUM_VIDEO_DAILY} في اليوم).\n/premium"
            )
        return
    if context.user_data.get("ai_busy"):
        await msg.reply_text("⏳ استنى لحد ما الطلب الحالي يخلص.")
        return
    context.user_data["ai_busy"] = True
    status = await msg.reply_text("🎬 جاري إنشاء الفيديو... (ممكن ياخد 1-3 دقايق)")
    temp_dir = tempfile.mkdtemp(prefix="amv_")
    FB_DIAG.pop("ai_video", None)
    try:
        image_bytes = None
        if image_file_id:
            tg_file = await context.bot.get_file(image_file_id)
            image_bytes = bytes(await tg_file.download_as_bytearray())
        async with VIDEO_AI_LIMIT:
            task = asyncio.create_task(asyncio.to_thread(make_ai_video, prompt, temp_dir, image_bytes))
            waited = 0
            while not task.done():
                await asyncio.sleep(10)
                waited += 10
                if not task.done():
                    try:
                        await status.edit_text(f"🎬 لسه بيتعمل الفيديو... ({waited} ثانية)")
                    except Exception:
                        pass
            out = task.result()
        add_usage(uid, "video")
        log_event(uid, "tool_ai_video", (prompt or "i2v")[:80])
        with open(out, "rb") as f:
            await msg.reply_video(
                video=f, supports_streaming=True,
                caption=f"🎬 {(prompt or '')[:150]}\n\nالمتبقي اليوم: {fmt_left(remaining(uid, 'video'))}",
                write_timeout=300, read_timeout=300)
        try:
            await status.delete()
        except Exception:
            pass
    except UserError as e:
        await status.edit_text(f"❌ {e}" + _admin_diag(uid, "ai_video"))
    except Exception as e:
        print("AI VIDEO ERROR:", repr(e)[:300])
        await status.edit_text(MAINTENANCE_TEXT + _admin_diag(uid, "ai_video"))
    finally:
        context.user_data["ai_busy"] = False
        shutil.rmtree(temp_dir, ignore_errors=True)


async def imagine_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await is_allowed(context.bot, uid):
        await update.message.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return
    prompt = " ".join(context.args or []).strip()
    if not prompt:
        context.user_data["await_ai"] = "image"
        await update.message.reply_text(
            f"🎨 اكتب وصف الصورة اللي عايزها (عربي أو إنجليزي) وابعته دلوقتي.\n"
            f"المتبقي لك اليوم: {fmt_left(remaining(uid, 'img'))}"
        )
        return
    await run_imagine(update.message, context, uid, prompt)


async def video_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await is_allowed(context.bot, uid):
        await update.message.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return
    prompt = " ".join(context.args or []).strip()
    if not prompt:
        context.user_data["await_ai"] = "video"
        await update.message.reply_text("🎬 اكتب وصف الفيديو اللي عايزه وابعته دلوقتي.")
        return
    await run_video(update.message, context, uid, prompt)


async def ai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    act = parts[1] if len(parts) > 1 else ""
    uid = q.from_user.id
    last = context.user_data.get("last_ai")
    premium = is_premium(uid)

    if act == "filters":
        await q.answer()
        if not context.user_data.get("photo"):
            await q.message.reply_text("❌ انتهت الجلسة. ابعت صورة تاني.")
            return
        await q.message.reply_text("🎨 اختار الفلتر:", reply_markup=filters_kb("f:", premium))
        return

    if act == "i2v":
        await q.answer()
        photo = context.user_data.get("photo")
        if not photo:
            await q.message.reply_text("❌ انتهت الجلسة. ابعت الصورة تاني.")
            return
        if not premium and not is_admin(uid):
            await q.message.reply_text("🎬 تحويل الصورة لفيديو لمشتركي Premium ⭐ — /premium")
            return
        prompt = (last or {}).get("prompt") if last else ""
        await run_video(q.message, context, uid, prompt, image_file_id=photo["file_id"])
        return

    if act in ("again", "shape"):
        if not last:
            await q.answer("انتهت الجلسة. اكتب الوصف تاني.", show_alert=True)
            return
        shape = last.get("shape", "sq")
        if act == "shape":
            if not premium and not is_admin(uid):
                await q.answer("تغيير الشكل لمشتركي Premium ⭐", show_alert=True)
                return
            shape = parts[2] if len(parts) > 2 and parts[2] in IMG_SHAPES else "sq"
        await q.answer()
        await run_imagine(q.message, context, uid, last["prompt"], shape=shape, prepared=True)
        return
    await q.answer()


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """صورة اتبعتت -> قايمة فلاتر."""
    msg = update.message
    uid = update.effective_user.id
    if not await is_allowed(context.bot, uid):
        await msg.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return
    if msg.photo:
        file_id = msg.photo[-1].file_id
    else:
        doc = msg.document
        if doc.file_size and doc.file_size > TG_DOWNLOAD_LIMIT:
            await msg.reply_text("❌ الصورة أكبر من 20MB.")
            return
        file_id = doc.file_id
    context.user_data["photo"] = {"file_id": file_id}
    context.user_data["await_ai"] = None
    await msg.reply_text(
        "🎨 اختار الفلتر:\n(⭐ = لمشتركي Premium)",
        reply_markup=filters_kb("f:", is_premium(uid)),
    )


async def filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    key = q.data.split(":", 1)[1]
    uid = q.from_user.id
    if key not in FILTERS:
        await q.answer()
        return
    if FILTERS[key][2] and not is_premium(uid):
        await q.answer("الفلتر ده لمشتركي Premium ⭐", show_alert=True)
        return
    photo = context.user_data.get("photo")
    if not photo:
        await q.answer("انتهت الجلسة. ابعت الصورة تاني.", show_alert=True)
        return
    if context.user_data.get("ai_busy"):
        await q.answer("استنى لحد ما الطلب الحالي يخلص.", show_alert=True)
        return
    await q.answer("⏳")
    context.user_data["ai_busy"] = True
    temp_dir = tempfile.mkdtemp(prefix="amp_")
    try:
        tg_file = await context.bot.get_file(photo["file_id"])
        src = os.path.join(temp_dir, "in")
        await tg_file.download_to_drive(src)
        dst = os.path.join(temp_dir, "out.jpg")
        async with LIMIT:
            await asyncio.to_thread(apply_filter_image, src, dst, key)
        log_event(uid, "tool_filter", key)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎨 فلتر تاني", callback_data="ai:filters")]])
        with open(dst, "rb") as f:
            await q.message.reply_photo(photo=f, caption=f"{FILTERS[key][0]} — @" + BOT_USERNAME,
                                        reply_markup=kb, write_timeout=120, read_timeout=120)
    except UserError as e:
        await q.message.reply_text(f"❌ {e}")
    except Exception as e:
        print("FILTER ERROR:", repr(e)[:200])
        await q.message.reply_text(MAINTENANCE_TEXT)
    finally:
        context.user_data["ai_busy"] = False
        shutil.rmtree(temp_dir, ignore_errors=True)


async def aicheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/aicheck — فحص إعدادات وخدمات الذكاء الاصطناعي (للأدمن)."""
    if not is_admin(update.effective_user.id):
        return
    status = await update.message.reply_text("🔎 جاري الفحص...")
    lines = [
        f"مزوّد الصور: {IMG_PROVIDER}" + (" (HF_TOKEN ✅)" if IMG_PROVIDER == "hf" and HF_TOKEN else ""),
        f"الفيديو (fal): {'مفتاح موجود ✅' if FAL_KEY else 'مفيش FAL_KEY ❌'}",
        f"موديل الفيديو: {FAL_VIDEO_MODEL}", f"موديل صورة←فيديو: {FAL_I2V_MODEL}",
        f"الحصص: صور {FREE_IMG_DAILY}/{PREMIUM_IMG_DAILY} — فيديو {FREE_VIDEO_DAILY}/{PREMIUM_VIDEO_DAILY}",
    ]

    def probe():
        t0 = time.time()
        try:
            data = generate_image_bytes("a red apple on a table", 256, 256, 1)
            return f"✅ توليد صورة تجريبي نجح ({len(data) // 1024}KB في {time.time() - t0:.0f}ث)"
        except Exception as e:
            return f"❌ توليد الصورة فشل: {str(e)[:200]}"

    lines.append(await asyncio.to_thread(probe))
    await status.edit_text("\n".join(lines))


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
        .concurrent_updates(True)   # مستخدمين كتير مع بعض من غير ما حد يستنى التاني
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
    app.add_handler(CommandHandler("imagine", imagine_cmd))
    app.add_handler(CommandHandler("video", video_cmd))
    app.add_handler(CommandHandler("aicheck", aicheck_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CommandHandler("gencode", gencode_cmd))
    app.add_handler(CommandHandler("userinfo", userinfo_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("invite", invite_cmd))
    app.add_handler(CommandHandler("feedback", feedback_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("testlink", testlink_cmd))
    app.add_handler(CommandHandler("dropcode", dropcode_cmd))
    app.add_handler(CommandHandler("dropchat", dropchat_cmd))
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
    app.add_handler(CallbackQueryHandler(ai_callback, pattern=r"^ai:"))
    app.add_handler(CallbackQueryHandler(filter_callback, pattern=r"^f:"))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^set:"))
    app.add_handler(CallbackQueryHandler(extra_callback, pattern=r"^extra:"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(media_filter, handle_file_to_mp3))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
    app.add_handler(MessageHandler(filters.Document.TEXT, handle_text_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    print("FFmpeg:", FFMPEG)
    print("Ahmed Media Downloader is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
