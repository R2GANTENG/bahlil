import telebot
from telebot import types
import requests
import threading
import time
import sqlite3

# === CONFIG ===
BOT_TOKEN = "8550068285:AAHepdxHY5Gz31CBMWkaXWFVEjg0PZ2mzuM"
PREMIUM_API_KEY = "MASUKIN_API_KEY_PREMIUM_LO"
ADMIN_ID = 7799092693  # ganti dengan Telegram ID lo
bot = telebot.TeleBot(BOT_TOKEN)

# === DATABASE ===
conn = sqlite3.connect('bot_v2.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    api_key TEXT,
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
conn.commit()

# === LOADING ANIMASI ===
loading_sequence = ["🙈","🙉","🙊","🐵","🐒","💡","⏳","⚡","🔥"]

# === HELPERS ===
def save_user(user_id, username, api_key=None, is_premium=0):
    cursor.execute("INSERT OR REPLACE INTO users(user_id, username, api_key, is_premium) VALUES(?,?,?,?)",
                   (user_id, username, api_key, is_premium))
    conn.commit()

def get_user_api(user_id):
    cursor.execute("SELECT api_key, is_premium FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row:
        api_key, is_premium = row
        if is_premium:
            return PREMIUM_API_KEY
        return api_key
    return None

def save_history(user_id, provider, prompt, video_url):
    cursor.execute("INSERT INTO history(user_id,provider,prompt,video_url) VALUES(?,?,?,?)",
                   (user_id, provider, prompt, video_url))
    conn.commit()

def get_history(user_id):
    cursor.execute("SELECT provider, prompt, video_url, timestamp FROM history WHERE user_id=? ORDER BY timestamp DESC", (user_id,))
    return cursor.fetchall()

# === GENERATE VIDEO FUNCTION ===
def generate_video(prompt, api_key, provider):
    """
    provider: Gemini / Kaiber / RunwayML / PikaLabs
    """
    try:
        if provider.lower() == "gemini":
            url = "https://api.ai.google.dev/v1/video:generate"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "duration_seconds": 5, "resolution": "720p"}
            r = requests.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            return data.get("video_url") or data.get("result_url")
        elif provider.lower() == "kaiber":
            url = "https://api.kaiber.ai/v1/generate"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "duration": 5}
            r = requests.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return r.json().get("video_url")
        elif provider.lower() == "runwayml":
            url = "https://api.runwayml.com/v1/videos"
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {"prompt": prompt, "length": 5}
            r = requests.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return r.json().get("video_url")
        elif provider.lower() == "pikalabs":
            url = "https://api.pikalabs.com/v1/generate"
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {"prompt": prompt, "duration": 5}
            r = requests.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return r.json().get("video_url")
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
    markup.add("🔑 Masukin API Key", "🎬 Buat Video", "🗂 History", "📖 Panduan API")
    if user_id == ADMIN_ID:
        markup.add("🛠 Admin Panel")
    bot.send_message(user_id, "Halo! 👋 Pilih opsi di bawah:", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def main_handler(message):
    user_id = message.chat.id
    text = message.text.lower()

    if "🔑" in text:
        msg = bot.send_message(user_id, "Ketik API Key kamu sekarang 🔑:")
        bot.register_next_step_handler(msg, save_api_key)
        return
    elif "🎬" in text:
        # pilih provider dulu
        markup = types.InlineKeyboardMarkup()
        for prov in ["Gemini","Kaiber","RunwayML","PikaLabs"]:
            markup.add(types.InlineKeyboardButton(prov, callback_data=f"provider_{prov}"))
        bot.send_message(user_id, "Pilih provider AI:", reply_markup=markup)
        return
    elif "🗂" in text:
        history = get_history(user_id)
        if not history:
            bot.send_message(user_id, "Belum ada history video 😅")
            return
        msg_text = ""
        for idx, (prov, prompt, url, ts) in enumerate(history[-5:],1):
            msg_text += f"{idx}. [{prov}] {prompt}\n{url}\n{ts}\n\n"
        bot.send_message(user_id, msg_text)
        return
    elif "📖" in text:
        tutorial = (
            "📌 **Panduan Dapetin API Key**:\n\n"
            "1️⃣ Gemini: https://console.cloud.google.com/ → buat project → aktifkan Vertex AI → buat API Key\n"
            "2️⃣ Kaiber: https://www.kaiber.ai/ → daftar → API Key\n"
            "3️⃣ RunwayML: https://runwayml.com/ → daftar → API Key\n"
            "4️⃣ PikaLabs: https://pikalabs.com/ → daftar → API Key\n\n"
            "Masukin API Key di bot dengan tombol 🔑 Masukin API Key"
        )
        bot.send_message(user_id, tutorial)
        return
    elif "🛠" in text:
        if user_id != ADMIN_ID:
            bot.send_message(user_id, "❌ Kamu bukan admin!")
            return
        cursor.execute("SELECT user_id, username, is_premium FROM users")
        users = cursor.fetchall()
        msg = "📋 Daftar user:\n"
        for u in users:
            msg += f"- {u[1]} (ID: {u[0]}) Premium: {'✅' if u[2] else '❌'}\n"
        bot.send_message(user_id, msg)
        return
    else:
        bot.send_message(user_id, "🤔 Pilih tombol di bawah ya 😎")

# === SIMPAN API KEY ===
def save_api_key(message):
    user_id = message.chat.id
    api_key = message.text.strip()
    save_user(user_id, message.from_user.username, api_key)
    bot.send_message(user_id, "✅ API Key berhasil disimpan!")

# === CALLBACK HANDLER PILIH PROVIDER ===
@bot.callback_query_handler(func=lambda call: call.data.startswith("provider_"))
def choose_provider(call):
    user_id = call.message.chat.id
    provider = call.data.split("_")[1]
    msg = bot.send_message(user_id, f"Ketik deskripsi video untuk provider {provider} 🎥:")
    bot.register_next_step_handler(msg, lambda m: process_video(m, provider))

# === PROCESS VIDEO ===
def process_video(message, provider):
    user_id = message.chat.id
    prompt = message.text.strip()
    api_key = get_user_api(user_id)
    if not api_key:
        bot.send_message(user_id, "❌ Belum ada API Key, klik 🔑 dulu!")
        return

    loading_msg = bot.send_message(user_id, "🙈🙉🙊💡⏳🔥 Lagi generate video AI...")

    def task():
        for _ in range(4):  # loop animasi lebih lama
            for e in loading_sequence:
                try:
                    bot.edit_message_text(chat_id=user_id, message_id=loading_msg.message_id,
                                          text=f"{e} Lagi generate video AI... {e}")
                    time.sleep(0.8)
                except:
                    pass
        video_url = generate_video(prompt, api_key, provider)
        save_history(user_id, provider, prompt, video_url if "http" in video_url else "")
        markup = types.InlineKeyboardMarkup()
        if "http" in video_url:
            markup.add(types.InlineKeyboardButton("⬇️ Download", url=video_url))
        bot.edit_message_text(chat_id=user_id, message_id=loading_msg.message_id,
                              text=f"✅ Video selesai!\n{video_url if 'http' in video_url else video_url}", reply_markup=markup)

    threading.Thread(target=task).start()

# === START BOT ===
bot.polling()
