import html
import io
import os
import re
import shelve
import threading
import time
import yt_dlp
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, request
from PIL import Image
from gtts import gTTS

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

if WEBHOOK_URL:
    full_webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{TELEGRAM_TOKEN}"
    try:
        bot.remove_webhook()
        bot.set_webhook(url=full_webhook_url)
        print(f"✅ Webhook successfully set to: {full_webhook_url}")
    except Exception as e:
        print(f"❌ Error setting webhook: {e}")

# =========================================================
# ADMINS & MODELS
# =========================================================
OWNER_ID = 7005552426
ADMIN_IDS = [7005552426, 7056087460]

def is_admin(uid):
    return uid in ADMIN_IDS

# Fast models listed first for maximum execution speed
AVAILABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-3.5-flash",
    "gemini-3.1-pro"
]

# =========================================================
# SYSTEM PROMPT & CONSTANTS
# =========================================================
SYSTEM_PROMPT = """You are Apex, a general-purpose AI assistant like ChatGPT created and developed by Jephthah Udoka.

CRITICAL IDENTITY RULE:
Only state that you were created by Jephthah Udoka if the user explicitly asks who created you, who made you, or who your developer is. Otherwise, reply directly to the user's prompt without adding self-introductions, signatures, or disclosures at the end of your response.

Never mention Google or Gemini as your creator.

FORMATTING RULES:
- Never use markdown headings like #, ##, or ###.
- Use bold text for emphasis or section headings.
- Use clean, naturally spaced paragraphs."""

FREE_LIMIT = 5
PREMIUM_PRICE = 100
PREMIUM_DAYS = 30

def enforce_identity(text: str) -> str:
    lower = text.lower()
    if "google" in lower or "gemini" in lower:
        return "I was created by Udoka Jephthah."
    return text

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
def process_markdown_text(text: str) -> str:
    text = re.sub(r'^#{1,6}\s*(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = html.escape(text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text)
    text = text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    return text

def format_ai_response(text: str) -> str:
    if "```" in text:
        parts = text.split("```")
        formatted = ""
        for i, part in enumerate(parts):
            if i % 2 == 1:
                lines = part.split("\n", 1)
                code_text = lines[1] if len(lines) > 1 and lines[0].strip().isalnum() else part
                formatted += f"<pre><code>{html.escape(code_text.strip())}</code></pre>"
            else:
                formatted += process_markdown_text(part)
        text = formatted
    else:
        text = process_markdown_text(text)

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n\n".join(paragraphs)

def ask_ai(uid, text):
    with shelve.open("logs") as db:
        history = db.get(str(uid), [])

    contents = []
    last_role = None
    
    for entry in history[-10:]:
        role = "user" if entry["role"] == "user" else "model"
        if role != last_role:
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=str(entry["text"]))]))
            last_role = role

    if contents and contents[-1].role == "user":
        contents.pop()

    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=str(text))]))

    for model_name in AVAILABLE_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                )
            )

            if hasattr(response, "text") and response.text:
                formatted = format_ai_response(response.text)
                return enforce_identity(formatted)
        except Exception:
            continue

    return "AI servers are currently busy. Please try again later."

def ask_ai_stream(uid, text, chat_id, message_id):
    """Ultra-fast streaming without loading animations or emojis."""
    with shelve.open("logs") as db:
        history = db.get(str(uid), [])

    contents = []
    last_role = None
    
    for entry in history[-10:]:
        role = "user" if entry["role"] == "user" else "model"
        if role != last_role:
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=str(entry["text"]))]))
            last_role = role

    if contents and contents[-1].role == "user":
        contents.pop()

    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=str(text))]))

    accumulated_text = ""
    last_update_time = time.time()

    for model_name in AVAILABLE_MODELS:
        try:
            response_stream = client.models.generate_content_stream(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                )
            )

            for chunk in response_stream:
                if chunk.text:
                    accumulated_text += chunk.text
                    
                    # Direct edit without intermediate loading text
                    if time.time() - last_update_time > 1.0:
                        formatted = format_ai_response(accumulated_text)
                        formatted = enforce_identity(formatted)
                        try:
                            bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=formatted,
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
                        last_update_time = time.time()

            final_formatted = format_ai_response(accumulated_text)
            final_formatted = enforce_identity(final_formatted)
            try:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=final_formatted,
                    parse_mode="HTML"
                )
            except Exception:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=final_formatted
                )
            return final_formatted

        except Exception:
            continue

    return ask_ai(uid, text)

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
    text = "Welcome back Creator 👑" if uid == OWNER_ID else "Hello, I am your AI assistant created by Udoka Jephthah 🤖"
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

# Voice / Talk Command: AI speaks response back as an audio file
@bot.message_handler(commands=['talk', 'voice', 'speak'])
def speak_handler(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return

    user_text = m.text.partition(' ')[2].strip()
    if not user_text:
        bot.reply_to(m, "Provide text! Example: /talk Hello Apex")
        return

    bot.send_chat_action(m.chat.id, "record_voice")

    reply_text = ask_ai(uid, user_text)
    temp_audio = f"voice_{m.message_id}.ogg"

    try:
        # Convert AI response text into speech audio
        clean_text = re.sub(r'<[^>]+>', '', reply_text)
        tts = gTTS(text=clean_text, lang='en')
        tts.save(temp_audio)

        with open(temp_audio, 'rb') as v_file:
            bot.send_voice(m.chat.id, voice=v_file, reply_to_message_id=m.message_id)

        save_chat(uid, "user", f"[Voice Request]: {user_text}")
        save_chat(uid, "bot", f"[Voice Reply]: {clean_text}")

        if not is_admin(uid) and not profile["premium"]:
            profile["count"] += 1
            save_profile(uid, profile)
    except Exception as e:
        bot.reply_to(m, f"Voice error: {str(e)}")
    finally:
        if os.path.exists(temp_audio):
            os.remove(temp_audio)

# Social Media Video Link Handler
@bot.message_handler(func=lambda m: m.text and any(domain in m.text.lower() for domain in ['tiktok.com', 'facebook.com', 'fb.watch', 'instagram.com', 'youtu', 'vm.tiktok.com']))
def handle_social_video_link(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return

    status_msg = bot.reply_to(m, "Processing video...")
    temp_filename = f"social_{m.message_id}.mp4"
    
    ydl_opts = {
        'outtmpl': temp_filename,
        'format': 'mp4/best[filesize<30M]/best',
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    }

    try:
        url = [word for word in m.text.split() if "http" in word][0]
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(temp_filename):
            bot.edit_message_text("Unable to download video.", m.chat.id, status_msg.message_id)
            return

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
                    reply = format_ai_response(res.text)
                    reply = enforce_identity(reply)
                    save_chat(uid, "user", f"[Video Link]: {m.text}")
                    save_chat(uid, "bot", reply)

                    try:
                        bot.delete_message(m.chat.id, status_msg.message_id)
                    except Exception:
                        pass

                    try:
                        bot.reply_to(m, reply, parse_mode="HTML")
                    except Exception:
                        bot.reply_to(m, reply)
                    if not is_admin(uid) and not profile["premium"]:
                        profile["count"] += 1
                        save_profile(uid, profile)
                    return
            except Exception:
                continue

        bot.edit_message_text("Unable to process video.", m.chat.id, status_msg.message_id)

    except Exception:
        bot.edit_message_text("Could not process video link.", m.chat.id, status_msg.message_id)
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

# Image Generation Handler (/image, /draw, /generate)
@bot.message_handler(commands=['image', 'draw', 'generate'])
def generate_image_handler(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return

    prompt = m.text.partition(' ')[2].strip()
    if not prompt:
        bot.reply_to(m, "Please provide a description! Example: /draw a cute robot surfing")
        return

    bot.send_chat_action(m.chat.id, "upload_photo")
    try:
        result = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1, 
                aspect_ratio="1:1"
            )
        )
        if result and hasattr(result, 'generated_images') and result.generated_images:
            image_bytes = result.generated_images[0].image.image_bytes
            bot.send_photo(m.chat.id, photo=image_bytes)
            save_chat(uid, "user", f"[Generate Image]: {prompt}")
            save_chat(uid, "bot", "[Generated Image Sent]")
            if not is_admin(uid) and not profile["premium"]:
                profile["count"] += 1
                save_profile(uid, profile)
        else:
            bot.reply_to(m, "Unable to generate image.")
    except Exception:
        bot.reply_to(m, "Image generation error.")

# Photo Handler
@bot.message_handler(content_types=['photo'])
def handle_photo(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return

    bot.send_chat_action(m.chat.id, "upload_photo" if (m.caption and m.caption.lower().startswith('/edit')) else "typing")
    try:
        file_info = bot.get_file(m.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        image = Image.open(io.BytesIO(downloaded_file))
        
        if m.caption and m.caption.lower().startswith('/edit'):
            edit_prompt = m.caption.partition(' ')[2].strip()
            if not edit_prompt:
                bot.reply_to(m, "Provide editing instructions in caption.")
                return
            
            res = client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=[image, edit_prompt],
                config=types.GenerateContentConfig(response_modalities=["IMAGE"])
            )
            for part in res.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    bot.send_photo(m.chat.id, photo=part.inline_data.data)
                    save_chat(uid, "user", f"[Edit Photo]: {edit_prompt}")
                    save_chat(uid, "bot", "[Edited Image Sent]")
                    if not is_admin(uid) and not profile["premium"]:
                        profile["count"] += 1
                        save_profile(uid, profile)
                    return
            bot.reply_to(m, "Could not edit this image.")
            return

        prompt = m.caption if m.caption else "Describe this image in detail."
        for model in AVAILABLE_MODELS:
            try:
                res = client.models.generate_content(
                    model=model,
                    contents=[image, prompt],
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
                )
                if hasattr(res, "text") and res.text:
                    reply = format_ai_response(res.text)
                    reply = enforce_identity(reply)
                    save_chat(uid, "user", "[Photo Sent]")
                    save_chat(uid, "bot", reply)
                    try:
                        bot.reply_to(m, reply, parse_mode="HTML")
                    except Exception:
                        bot.reply_to(m, reply)
                    if not is_admin(uid) and not profile["premium"]:
                        profile["count"] += 1
                        save_profile(uid, profile)
                    return
            except Exception:
                continue
    except Exception as e:
        bot.reply_to(m, f"Error processing image: {str(e)}")

# Direct Video File Handler
@bot.message_handler(content_types=['video'])
def handle_video(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return

    status_msg = bot.reply_to(m, "Downloading video...")
    temp_filename = f"video_{m.message_id}.mp4"
    try:
        file_info = bot.get_file(m.video.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(temp_filename, "wb") as f:
            f.write(downloaded_file)

        video_file = client.files.upload(file=temp_filename)
        prompt = m.caption if m.caption else "Analyze this video."

        for model in AVAILABLE_MODELS:
            try:
                res = client.models.generate_content(
                    model=model,
                    contents=[video_file, prompt],
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
                )
                if hasattr(res, "text") and res.text:
                    reply = format_ai_response(res.text)
                    reply = enforce_identity(reply)
                    save_chat(uid, "user", "[Video Sent]")
                    save_chat(uid, "bot", reply)

                    try:
                        bot.delete_message(m.chat.id, status_msg.message_id)
                    except Exception:
                        pass

                    try:
                        bot.reply_to(m, reply, parse_mode="HTML")
                    except Exception:
                        bot.reply_to(m, reply)
                    if not is_admin(uid) and not profile["premium"]:
                        profile["count"] += 1
                        save_profile(uid, profile)
                    return
            except Exception:
                continue

        bot.reply_to(m, "Failed to analyze video.")

    except Exception as e:
        bot.reply_to(m, f"Video error: {str(e)}")
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

# Voice Note Handler: User sends voice, AI responds with Voice back
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
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT + "\nRespond concisely."
                )
            )
            if hasattr(res, "text") and res.text:
                reply = format_ai_response(res.text)
                reply = enforce_identity(reply)
                
                # Convert AI response text into audio voice reply
                clean_text = re.sub(r'<[^>]+>', '', reply)
                temp_audio = f"voice_reply_{m.message_id}.ogg"
                tts = gTTS(text=clean_text, lang='en')
                tts.save(temp_audio)

                with open(temp_audio, 'rb') as v_file:
                    bot.send_voice(m.chat.id, voice=v_file, reply_to_message_id=m.message_id)

                if os.path.exists(temp_audio):
                    os.remove(temp_audio)

                save_chat(uid, "user", "[Voice Note Sent]")
                save_chat(uid, "bot", reply)

                if not is_admin(uid) and not profile["premium"]:
                    profile["count"] += 1
                    save_profile(uid, profile)
                return
        except Exception: 
            continue
    bot.reply_to(m, "Voice processing error")

# Main Chat Handler: Fast streaming
@bot.message_handler(func=lambda m: True)
def chat(m):
    uid = m.from_user.id
    profile = get_profile(uid)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached")
        return
    
    bot.send_chat_action(m.chat.id, "typing")
    status_msg = bot.reply_to(m, ".")
    
    reply = ask_ai_stream(uid, m.text, m.chat.id, status_msg.message_id)
    
    save_chat(uid, "user", m.text)
    save_chat(uid, "bot", reply)
    
    if not is_admin(uid) and not profile["premium"]:
        profile["count"] += 1
        save_profile(uid, profile)

# =========================================================
# WEBHOOK ENDPOINTS & THREADING
# =========================================================
@app.route("/", methods=["GET"])
def home():
    return "Bot is active!", 200

@app.route("/" + TELEGRAM_TOKEN, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        threading.Thread(target=bot.process_new_updates, args=([update],)).start()
        return "OK", 200
    return "Forbidden", 403

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
