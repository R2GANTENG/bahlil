import os
import telebot
from telebot import types
import requests
import threading
import time
import sqlite3
from datetime import datetime

# optional web server for website-based login
from flask import Flask, request, jsonify

# Google auth (untuk Gemini service account)
try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleRequest
except Exception:
    service_account = None
    GoogleRequest = None

# === CONFIG ===
# For security, set these in environment variables in Replit / server
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "8550068285:AAHepdxHY5Gz31CBMWkaXWFVEjg0PZ2mzuM"
PREMIUM_API_KEY = os.environ.get("PREMIUM_API_KEY") or "MASUKIN_API_KEY_PREMIUM_LO"
SERVICE_ACCOUNT_FILE = os.environ.get("SERVICE_ACCOUNT_FILE") or "/app/credentials/gemini-service-account.json"
ADMIN_ID = int(os.environ.get("ADMIN_ID") or 7799092693)
# secret shared between your website and this bot to register tokens
WEBSITE_SECRET = os.environ.get("WEBSITE_SECRET") or "CHANGE_ME_WEBSITE_SECRET"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# === DATABASE ===
conn = sqlite3.connect('bot_v2.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    is_premium INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    provider TEXT,
    prompt TEXT,
    video_url TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

# sessions: menyimpan user yang sudah "login / pernah pakai provider", agar tidak minta login ulang
cursor.execute("""
CREATE TABLE IF NOT EXISTS sessions(
    user_id INTEGER PRIMARY KEY,
    logged_in INTEGER DEFAULT 0,
    last_login DATETIME
)
""")

# user_tokens: menyimpan token per-user per-provider (untuk Grok, Sora2, ChatGPT)
cursor.execute("""
CREATE TABLE IF NOT EXISTS user_tokens(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    provider TEXT,
    token TEXT,
    refresh_token TEXT,
    expiry REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, provider)
)
""")

# gemini_token: menyimpan token akses untuk service account (dipakai oleh bot account)
cursor.execute("""
CREATE TABLE IF NOT EXISTS gemini_token(
    id INTEGER PRIMARY KEY,
    token TEXT,
    expiry REAL
)
""")

conn.commit()

# === LOADING ANIMASI ===
loading_sequence = ["🙈","🙉","🙊","🐵","🐒","💡","⏳","⚡","🔥"]

# === HELPERS ===
def save_user(user_id, username, is_premium=0):
    cursor.execute("INSERT OR REPLACE INTO users(user_id, username, is_premium) VALUES(?,?,?)",
                   (user_id, username, is_premium))
    conn.commit()

def mark_session_logged(user_id):
    now = datetime.utcnow().isoformat()
    cursor.execute("INSERT OR REPLACE INTO sessions(user_id, logged_in, last_login) VALUES(?,?,?)",
                   (user_id, 1, now))
    conn.commit()

def is_session_logged(user_id):
    cursor.execute("SELECT logged_in FROM sessions WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return bool(row and row[0])

def save_user_token(user_id, provider, token, refresh_token=None, expiry=None):
    cursor.execute("""
        INSERT INTO user_tokens(user_id, provider, token, refresh_token, expiry)
        VALUES(?,?,?,?,?)
        ON CONFLICT(user_id, provider) DO UPDATE SET token=excluded.token, refresh_token=excluded.refresh_token, expiry=excluded.expiry
    """, (user_id, provider, token, refresh_token, expiry))
    conn.commit()
    mark_session_logged(user_id)

def get_user_token(user_id, provider):
    cursor.execute("SELECT token, refresh_token, expiry FROM user_tokens WHERE user_id=? AND provider= ?", (user_id, provider))
    row = cursor.fetchone()
    if row:
        return {"token": row[0], "refresh_token": row[1], "expiry": row[2]}
    return None

def save_history(user_id, provider, prompt, video_url):
    cursor.execute("INSERT INTO history(user_id,provider,prompt,video_url) VALUES(?,?,?,?)",
                   (user_id, provider, prompt, video_url))
    conn.commit()

def get_history(user_id):
    cursor.execute("SELECT provider, prompt, video_url, timestamp FROM history WHERE user_id=? ORDER BY timestamp DESC", (user_id,))
    return cursor.fetchall()

# === GEMINI (service account) TOKEN HANDLER ===
GEMINI_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

def get_gemini_access_token():
    """
    Menggunakan service account JSON untuk mendapatkan access token Bearer.
    Token disimpan di DB (gemini_token) bersama expiry untuk reuse.
    """
    if service_account is None or GoogleRequest is None:
        raise RuntimeError("google-auth library tidak terinstall. pip install google-auth")

    # Check DB apakah token masih valid
    cursor.execute("SELECT token, expiry FROM gemini_token WHERE id=1")
    row = cursor.fetchone()
    if row:
        token, expiry = row
        try:
            if expiry and time.time() < float(expiry) - 60:
                return token
        except Exception:
            pass

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise RuntimeError(f"Service account file tidak ditemukan di path: {SERVICE_ACCOUNT_FILE}")

    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=GEMINI_SCOPES)
    request = GoogleRequest()
    creds.refresh(request)
    token = creds.token
    expiry_ts = creds.expiry.timestamp() if creds.expiry else time.time() + 3600
    cursor.execute("INSERT OR REPLACE INTO gemini_token(id, token, expiry) VALUES(1,?,?)",
                   (token, expiry_ts))
    conn.commit()
    return token

# === GENERATE VIDEO FUNCTION ===
def generate_video(prompt, provider, user_id=None):
    """
    provider: gemini / grok / sora2 / chatgpt
    Untuk grok/sora2/chatgpt: pakai token per-user yang disimpan di DB (user_tokens).
    Untuk gemini: pakai service-account token yang di-handle get_gemini_access_token().
    """
    try:
        provider_key = provider.lower()
        if provider_key == "gemini":
            token = get_gemini_access_token()
            url = "https://api.ai.google.dev/v1/video:generate"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "duration_seconds": 5, "resolution": "720p"}
            r = requests.post(url, json=payload, headers=headers, timeout=120)
            r.raise_for_status()
            data = r.json()
            if user_id:
                mark_session_logged(user_id)
            return data.get("video_url") or data.get("result_url") or data.get("uri") or str(data)
        elif provider_key == "grok":
            ut = get_user_token(user_id, "grok")
            if not ut:
                return "❌ Belum login ke Grok. Silakan pilih Grok dan kirimkan token Anda."
            token = ut["token"]
            url = "https://api.grok.ai/v1/generate"  # placeholder endpoint (ganti sesuai API Grok)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "duration": 5}
            r = requests.post(url, json=payload, headers=headers, timeout=120)
            r.raise_for_status()
            return r.json().get("video_url") or r.text
        elif provider_key == "sora2" or provider_key == "sora":
            ut = get_user_token(user_id, "sora2")
            if not ut:
                return "❌ Belum login ke Sora 2. Silakan pilih Sora 2 dan kirimkan token Anda."
            token = ut["token"]
            url = "https://api.sora.com/v2/generate"  # placeholder endpoint (ganti sesuai API Sora2)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "length": 5}
            r = requests.post(url, json=payload, headers=headers, timeout=120)
            r.raise_for_status()
            return r.json().get("video_url") or r.text
        elif provider_key == "chatgpt" or provider_key == "openai":
            ut = get_user_token(user_id, "chatgpt")
            if not ut:
                return "❌ Belum login ke ChatGPT/OpenAI. Silakan pilih ChatGPT dan kirimkan token Anda."
            token = ut["token"]
            # Placeholder: menggunakan OpenAI-style endpoint untuk video (sesuaikan sesuai API nyata)
            url = "https://api.openai.com/v1/videos"  # placeholder endpoint (ganti sesuai API OpenAI)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "duration_seconds": 5}
            r = requests.post(url, json=payload, headers=headers, timeout=120)
            r.raise_for_status()
            return r.json().get("video_url") or r.text
        else:
            return "❌ Provider tidak dikenal!"
    except Exception as e:
        return f"❌ Error: {str(e)}"

# === BOT HANDLERS ===
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.chat.id
    username = message.from_user.username or ""
    save_user(user_id, username)
    main_menu(user_id)

def main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎬 Buat Video", "🗂 History", "📖 Panduan API", "🔐 Login Provider")
    if user_id == ADMIN_ID:
        markup.add("🛠 Admin Panel")
    bot.send_message(user_id, "Halo! 👋 Pilih opsi di bawah:", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def main_handler(message):
    user_id = message.chat.id
    text = (message.text or "").lower()

    if "🔐" in text or "login" in text:
        markup = types.InlineKeyboardMarkup()
        for prov in ["Gemini","Grok","Sora2","ChatGPT"]:
            markup.add(types.InlineKeyboardButton(prov, callback_data=f"login_{prov}"))
        bot.send_message(user_id, "Pilih provider untuk login / masukkan token:", reply_markup=markup)
        return
    elif "🎬" in text or "buat video" in text:
        markup = types.InlineKeyboardMarkup()
        for prov in ["Gemini","Grok","Sora2","ChatGPT"]:
            markup.add(types.InlineKeyboardButton(prov, callback_data=f"provider_{prov}"))
        bot.send_message(user_id, "Pilih provider AI:", reply_markup=markup)
        return
    elif "🗂" in text or "history" in text:
        history = get_history(user_id)
        if not history:
            bot.send_message(user_id, "Belum ada history video 😅")
            return
        msg_text = ""
        for idx, (prov, prompt, url, ts) in enumerate(history[:10],1):
            msg_text += f"{idx}. [{prov}] {prompt}\n{url}\n{ts}\n\n"
        bot.send_message(user_id, msg_text)
        return
    elif "📖" in text or "panduan" in text:
        tutorial = (
            "📌 Panduan singkat:\n\n"
            "- Gemini: menggunakan akun service (bot). Tidak perlu token per-user. Pastikan SERVICE_ACCOUNT_FILE di server.\n"
            "- Grok / Sora2 / ChatGPT: login via website atau bot (paste token/API key). Setelah disimpan, tidak perlu login ulang.\n\n"
            "Gunakan tombol '🔐 Login Provider' untuk menyimpan token akun kamu atau login melalui website yang terhubung.\n"
            "Kemudian pilih '🎬 Buat Video' dan pilih provider."
        )
        bot.send_message(user_id, tutorial)
        return
    elif "🛠" in text or "admin" in text:
        if user_id != ADMIN_ID:
            bot.send_message(user_id, "❌ Kamu bukan admin!")
            return
        cursor.execute("SELECT user_id, username, is_premium FROM users")
        users = cursor.fetchall()
        msg = "📋 Daftar user:\n"
        for u in users:
            msg += f"- {u[1]} (ID: {u[0]}) Premium: {'✅' if u[2] else '❌'}\n"
        cursor.execute("SELECT user_id, last_login FROM sessions")
        sessions = cursor.fetchall()
        msg += "\n🔐 Sessions (logged users):\n"
        for s in sessions:
            msg += f"- ID: {s[0]} last_login: {s[1]}\n"
        cursor.execute("SELECT user_id, provider, token IS NOT NULL FROM user_tokens")
        tokens = cursor.fetchall()
        msg += "\n🔑 User tokens stored:\n"
        for t in tokens:
            msg += f"- ID {t[0]} provider {t[1]} token_saved: {'✅' if t[2] else '❌'}\n"
        bot.send_message(user_id, msg)
        return
    else:
        bot.send_message(user_id, "🤔 Pilih tombol di bawah ya 😎")

# === LOGIN HANDLER (simpan token yang user kirim) ===
@bot.callback_query_handler(func=lambda call: call.data.startswith("login_"))
def login_provider(call):
    user_id = call.message.chat.id
    provider = call.data.split("_",1)[1]
    provider_key = provider.lower()
    if provider_key == "gemini":
        bot.send_message(user_id, "Gemini memakai service-account bot. Tidak perlu token per-user.")
        mark_session_logged(user_id)
        return
    msg = bot.send_message(user_id, f"Ketik token / API key untuk provider {provider} sekarang (paste di chat):")
    bot.register_next_step_handler(msg, lambda m, prov=provider: save_token_then_ask_prompt(m, prov))

def save_token_then_ask_prompt(message, provider):
    user_id = message.chat.id
    token = message.text.strip()
    if not token:
        bot.send_message(user_id, "Token kosong. Coba lagi.")
        return
    save_user_token(user_id, provider.lower(), token)
    bot.send_message(user_id, f"✅ Token untuk {provider} berhasil disimpan. Sekarang ketik deskripsi video untuk {provider}:")
    bot.register_next_step_handler(message, lambda m, prov=provider: process_video(m, prov))

# === CALLBACK HANDLER PILIH PROVIDER ===
@bot.callback_query_handler(func=lambda call: call.data.startswith("provider_"))
def choose_provider(call):
    user_id = call.message.chat.id
    provider = call.data.split("_",1)[1]
    prov_key = provider.lower()
    if prov_key != "gemini":
        ut = get_user_token(user_id, prov_key if prov_key!="sora" else "sora2")
        if not ut:
            bot.send_message(user_id, f"Belum login ke {provider}. Kirim token sekarang atau gunakan tombol Login Provider.")
            msg = bot.send_message(user_id, f"Ketik token / API key untuk provider {provider} sekarang (paste di chat):")
            bot.register_next_step_handler(msg, lambda m, prov=provider: save_token_then_ask_prompt(m, prov))
            return
    msg = bot.send_message(user_id, f"Ketik deskripsi video untuk provider {provider} 🎥:")
    bot.register_next_step_handler(msg, lambda m, prov=provider: process_video(m, prov))

# === PROCESS VIDEO ===
def process_video(message, provider):
    user_id = message.chat.id
    prompt = message.text.strip()
    provider_key = provider.lower()
    if provider_key != "gemini":
        ut = get_user_token(user_id, provider_key if provider_key!="sora" else "sora2")
        if not ut:
            bot.send_message(user_id, f"❌ Belum ada token untuk {provider}. Klik '🔐 Login Provider' dulu.")
            return

    loading_msg = bot.send_message(user_id, "🙈🙉🙊💡⏳🔥 Lagi generate video AI...")

    def task():
        for _ in range(4):
            for e in loading_sequence:
                try:
                    bot.edit_message_text(chat_id=user_id, message_id=loading_msg.message_id,
                                          text=f"{e} Lagi generate video AI... {e}")
                    time.sleep(0.8)
                except:
                    pass
        video_url = generate_video(prompt, provider, user_id=user_id)
        save_history(user_id, provider, prompt, video_url if isinstance(video_url, str) and "http" in video_url else "")
        markup = types.InlineKeyboardMarkup()
        if isinstance(video_url, str) and "http" in video_url:
            markup.add(types.InlineKeyboardButton("⬇️ Download", url=video_url))
        bot.edit_message_text(chat_id=user_id, message_id=loading_msg.message_id,
                              text=f"✅ Video selesai!\n{video_url}", reply_markup=markup)

    threading.Thread(target=task).start()

# === FLASK ENDPOINTS FOR WEBSITE INTEGRATION ===
@app.route('/register_token', methods=['POST'])
def register_token():
    """Endpoint untuk website mendaftarkan token user ke bot.
    Body (application/json): { "secret": "...", "user_id": 12345, "provider": "grok", "token": "...", "refresh_token": null, "expiry": null, "notify": true }
    """
    data = request.get_json(force=True)
    secret = data.get('secret')
    if not secret or secret != WEBSITE_SECRET:
        return jsonify({'ok': False, 'error': 'invalid secret'}), 401
    try:
        user_id = int(data['user_id'])
        provider = data['provider'].lower()
        token = data['token']
    except Exception as e:
        return jsonify({'ok': False, 'error': 'invalid payload', 'detail': str(e)}), 400
    refresh = data.get('refresh_token')
    expiry = data.get('expiry')
    save_user_token(user_id, provider, token, refresh_token=refresh, expiry=expiry)
    if data.get('notify'):
        try:
            bot.send_message(user_id, f"✅ Sukses login {provider}! Sekarang kamu bisa pakai provider ini lewat bot.")
        except Exception:
            pass
    return jsonify({'ok': True})

@app.route('/notify', methods=['POST'])
def notify():
    data = request.get_json(force=True)
    secret = data.get('secret')
    if not secret or secret != WEBSITE_SECRET:
        return jsonify({'ok': False, 'error': 'invalid secret'}), 401
    try:
        user_id = int(data['user_id'])
        msg = data.get('message', 'Notification dari website')
    except Exception as e:
        return jsonify({'ok': False, 'error': 'invalid payload', 'detail': str(e)}), 400
    try:
        bot.send_message(user_id, msg)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': 'send failed', 'detail': str(e)}), 500

# === START FLASK IN BACKGROUND AND BOT ===
def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == "__main__":
    # start flask thread so website can register tokens
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Flask server started in background on port", os.environ.get('PORT', 8080))
    print("Bot starting...")
    bot.polling()
