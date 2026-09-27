import io
import os
import shelve
import threading
import yt_dlp
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, request
from PIL import Image

import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice
)

from google import genai
from google.genai import types

load_dotenv()

# =========================================================
# CONFIG
# =========================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://telegram-bot-4-p8mu.onrender.com")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Missing TELEGRAM_TOKEN or GEMINI_API_KEY in environment variables")

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)

# =========================================================
# ADMINS & MODELS
# =========================================================
OWNER_ID = 7005552426
ADMIN_IDS = [7005552426, 7056087460]

def is_admin(uid):
    return uid in ADMIN_IDS

AVAILABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite"
]

# =========================================================
# SYSTEM PROMPT & CONSTANTS
# =========================================================
SYSTEM_PROMPT = """You are Apex, a general-purpose AI assistant like ChatGPT created and developed by Jephthah Udoka.

CRITICAL IDENTITY RULE:
Only state that you were created by Jephthah Udoka if the user explicitly asks who created you, who made you, or who your developer is. Otherwise, reply directly to the user's prompt without adding self-introductions, signatures, or disclosures at the end of your response.

Never mention Google or Gemini as your creator.

STYLE RULES:
- Use clean, normal text
- No markdown formatting, asterisks, hashtags, or code block formatting unless requested
- Respond naturally and clearly"""

FREE_LIMIT = 5
PREMIUM_PRICE = 100
PREMIUM_DAYS = 30

# =========================================================
# PROFILE & LOGS
# =========================================================
def get_profile(uid):
    with shelve.open("db", writeback=True) as db:
        k = str(uid)
        if k not in db:
            db[k] = {
                "premium": False,
                "count": 0,
                "date": str(date.today()),
                "expiry": "2000-01-01"
            }
        u = db[k]
        if u["date"] != str(date.today()):
            u["count"] = 0
            u["date"] = str(date.today())
        try:
            if datetime.now().date() > datetime.fromisoformat(u["expiry"]).date():
                u["premium"] = False
        except Exception:
            pass
        if is_admin(uid):
            u["premium"] = True
            u["count"] = 0
        db[k] = u
        return u

def save_profile(uid, data):
    with shelve.open("db", writeback=True) as db:
        db[str(uid)] = data

def save_chat(uid, role, text):
    with shelve.open("logs", writeback=True) as db:
        k = str(uid)
        if k not in db:
            db[k] = []
        db[k].append({
            "time": str(datetime.now()),
            "role": role,
            "text": text
        })

# =========================================================
# AI ENGINE
# =========================================================
def ask_ai(uid, text):
    with shelve.open("logs") as db:
        history = db.get(str(uid), [])

    for model_name in AVAILABLE_MODELS:
        try:
            contents = []
            for entry in history[-10:]:
                role = "user" if entry["role"] == "user" else "model"
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=entry["text"])]))
            
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                )
            )

            if hasattr(response, "text") and response.text:
                reply = response.text.replace("```", "").replace("**", "").replace("__", "").replace("*", "").replace("#", "").replace("`", "")
                return "\n".join(line.strip() for line in reply.splitlines() if line.strip())
        except Exception:
            continue
    return "AI servers are currently busy. Please try again later."

# =========================================================
# MENUS & HANDLERS
# =========================================================
def main_menu(uid):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🌟 Premium", callback_data="premium"))
    if is_admin(uid):
        kb.add(InlineKeyboardButton("👑 Admin", callback_data="admin"))
    return kb

def premium_menu():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Buy Premium {PREMIUM_PRICE} ⭐ / {PREMIUM_DAYS} Days", callback_data="buy"))
    return kb

@bot.message_handler(commands=['start'])
def start(m):
    uid = m.from_user.id
    get_profile(uid)
    text = "Welcome back Creator 👑" if uid == OWNER_ID else "Hello, I am Apex, your AI assistant 🤖"
    bot.send_message(m.chat.id, text, reply_markup=main_menu(uid))

@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    uid = c.from_user.id
    if c.data == "premium":
        bot.send_message(c.message.chat.id, f"Premium Plan\n{PREMIUM_PRICE} Stars\n{PREMIUM_DAYS} Days", reply_markup=premium_menu())
    elif c.data == "buy":
        bot.send_invoice(c.message.chat.id, "Premium Subscription", "Unlimited AI access", "premium", "", "XTR", [LabeledPrice("Premium", PREMIUM_PRICE)])
    elif c.data == "admin" and is_admin(uid):
        bot.send_message(c.message.chat.id, "Admin Panel Active")

@bot.pre_checkout_query_handler(func=lambda q: True)
def checkout(q):
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def success(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    profile["premium"] = True
    profile["expiry"] = (datetime.now() + timedelta(days=PREMIUM_DAYS)).strftime("%Y-%m-%d")
    save_profile(uid, profile)
    bot.reply_to(m, "Premium Activated 🎉")

# Social Media Video Link Handler (TikTok, Facebook, Instagram, YouTube)
@bot.message_handler(func=lambda m: m.text and any(domain in m.text.lower() for domain in ['tiktok.com', 'facebook.com', 'fb.watch', 'instagram.com', 'youtu']))
def handle_social_video_link(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return

    bot.send_chat_action(m.chat.id, "typing")
    bot.reply_to(m, "📥 Downloading video for analysis...")

    temp_filename = f"social_{m.message_id}.mp4"
    ydl_opts = {
        'outtmpl': temp_filename,
        'format': 'mp4/best[filesize<50M]/best',
        'quiet': True,
        'no_warnings': True,
    }

    try:
        # Download video via yt-dlp
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([m.text.strip()])

        if not os.path.exists(temp_filename):
            bot.reply_to(m, "Could not fetch the video from the link. Make sure the post is public.")
            return

        # Upload video to Gemini Files API
        video_file = client.files.upload(file=temp_filename)
        prompt = "Analyze this video in detail, including both visual content and background audio/dialogue."

        for model in AVAILABLE_MODELS:
            try:
                res = client.models.generate_content(
                    model=model,
                    contents=[video_file, prompt],
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
                )
                if hasattr(res, "text") and res.text:
                    reply = res.text.replace("*", "").replace("`", "")
                    save_chat(uid, "user", f"[Video Link]: {m.text}")
                    save_chat(uid, "bot", reply)
                    bot.reply_to(m, reply)
                    if not is_admin(uid) and not profile["premium"]:
                        profile["count"] += 1
                        save_profile(uid, profile)
                    return
            except Exception:
                continue

    except Exception as e:
        bot.reply_to(m, "Failed to download or analyze video. Ensure the link is valid and public.")
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

# Photo Handler: Analyzes images sent by user
@bot.message_handler(content_types=['photo'])
def handle_photo(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return

    bot.send_chat_action(m.chat.id, "typing")
    try:
        file_info = bot.get_file(m.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        image = Image.open(io.BytesIO(downloaded_file))
        
        prompt = m.caption if m.caption else "Describe this image in detail."

        for model in AVAILABLE_MODELS:
            try:
                res = client.models.generate_content(
                    model=model,
                    contents=[image, prompt],
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
                )
                if hasattr(res, "text") and res.text:
                    reply = res.text.replace("*", "").replace("`", "")
                    save_chat(uid, "user", "[Photo Sent]")
                    save_chat(uid, "bot", reply)
                    bot.reply_to(m, reply)
                    if not is_admin(uid) and not profile["premium"]:
                        profile["count"] += 1
                        save_profile(uid, profile)
                    return
            except Exception:
                continue
    except Exception as e:
        bot.reply_to(m, f"Error processing image: {e}")

# Video Handler: Analyzes visual action + audio track
@bot.message_handler(content_types=['video'])
def handle_video(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return

    bot.send_chat_action(m.chat.id, "typing")
    temp_filename = f"video_{m.message_id}.mp4"
    try:
        file_info = bot.get_file(m.video.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(temp_filename, "wb") as f:
            f.write(downloaded_file)

        video_file = client.files.upload(file=temp_filename)
        prompt = m.caption if m.caption else "Analyze this video, including both visual action and audio dialogue."

        for model in AVAILABLE_MODELS:
            try:
                res = client.models.generate_content(
                    model=model,
                    contents=[video_file, prompt],
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
                )
                if hasattr(res, "text") and res.text:
                    reply = res.text.replace("*", "").replace("`", "")
                    save_chat(uid, "user", "[Video Sent]")
                    save_chat(uid, "bot", reply)
                    bot.reply_to(m, reply)
                    if not is_admin(uid) and not profile["premium"]:
                        profile["count"] += 1
                        save_profile(uid, profile)
                    return
            except Exception:
                continue
    except Exception as e:
        bot.reply_to(m, f"Error processing video: {e}")
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

# Voice Note Handler
@bot.message_handler(content_types=['voice'])
def voice(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return
    bot.send_chat_action(m.chat.id, "record_voice")
    
    try:
        file = bot.get_file(m.voice.file_id)
        data = bot.download_file(file.file_path)
    except Exception:
        bot.reply_to(m, "Failed to download voice note.")
        return

    audio_part = types.Part.from_bytes(data=data, mime_type="audio/ogg")
    
    for model in AVAILABLE_MODELS:
        try:
            res = client.models.generate_content(
                model=model, 
                contents=[audio_part],
                config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
            )
            if hasattr(res, "text") and res.text:
                reply = res.text.replace("*", "").replace("`", "")
                save_chat(uid, "user", "[Voice Note Sent]")
                save_chat(uid, "bot", reply)
                bot.reply_to(m, reply)
                if not is_admin(uid) and not profile["premium"]:
                    profile["count"] += 1
                    save_profile(uid, profile)
                return
        except Exception: 
            continue
    bot.reply_to(m, "Voice error")

# Text Chat Handler
@bot.message_handler(func=lambda m: True)
def chat(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return
    
    bot.send_chat_action(m.chat.id, "typing")
    reply = ask_ai(uid, m.text)
    
    save_chat(uid, "user", m.text)
    save_chat(uid, "bot", reply)
    bot.reply_to(m, reply)
    
    if not is_admin(uid) and not profile["premium"]:
        profile["count"] += 1
        save_profile(uid, profile)

# =========================================================
# WEBHOOK ENDPOINTS
# =========================================================
@app.route("/", methods=["GET"])
def home():
    return "Bot is active!", 200

@app.route("/" + TELEGRAM_TOKEN, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        
        # Offload update processing to a background thread to prevent latency/retries
        threading.Thread(target=bot.process_new_updates, args=([update],)).start()
        return "OK", 200
    return "Forbidden", 403

# =========================================================
# AUTO-SET WEBHOOK ON GUNICORN/RENDER STARTUP
# =========================================================
if WEBHOOK_URL:
    full_webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{TELEGRAM_TOKEN}"
    try:
        bot.remove_webhook()
        bot.set_webhook(url=full_webhook_url)
        print(f"Webhook set to: {full_webhook_url}")
    except Exception as e:
        print(f"Error setting webhook: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    
