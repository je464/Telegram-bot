import html
import io
import os
import re
import shelve
import time
import pytz
import yt_dlp
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, request
from PIL import Image
from gtts import gTTS

import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    BotCommand
)

from google import genai
from google.genai import types

load_dotenv()

# =========================================================
# CONFIG & MULTI-KEY SETUP (UP TO 6 API KEYS)
# =========================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://telegram-bot-4-p8mu.onrender.com")

API_KEYS = [
    os.getenv("GEMINI_API_KEY"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
    os.getenv("GEMINI_API_KEY_4"),
    os.getenv("GEMINI_API_KEY_5"),
    os.getenv("GEMINI_API_KEY_6"),
]
API_KEYS = [k for k in API_KEYS if k]

if not TELEGRAM_TOKEN or not API_KEYS:
    raise ValueError("Missing TELEGRAM_TOKEN or at least one GEMINI_API_KEY in environment variables")

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
app = Flask(__name__)

# =========================================================
# ADMINS & MODELS
# =========================================================
ADMIN_IDS = [7005552426, 7056087460]

def is_admin(uid):
    return uid in ADMIN_IDS

def setup_bot_commands(uid):
    try:
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("timezone", "Set your local timezone"),
            BotCommand("talk", "Generate voice reply from text"),
            BotCommand("image", "Generate image from text prompt"),
            BotCommand("pdf", "Generate a downloadable PDF document"),
            BotCommand("email", "Send an email via secure SMTP"),
        ]
        if is_admin(uid):
            commands.extend([
                BotCommand("users", "View user statistics (Admin)"),
                BotCommand("chatlogs", "View user chat logs (Admin)")
            ])
        bot.set_my_commands(commands)
    except Exception as e:
        print(f"❌ Error setting bot commands: {e}")

if WEBHOOK_URL:
    full_webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{TELEGRAM_TOKEN}"
    try:
        bot.remove_webhook()
        bot.set_webhook(url=full_webhook_url)
        print(f"✅ Webhook successfully set to: {full_webhook_url}")
    except Exception as e:
        print(f"❌ Error setting webhook: {e}")

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
SYSTEM_PROMPT = """You are Apex, a general-purpose AI assistant developed by Jephthah Udoka.

CRITICAL IDENTITY RULE:
- ONLY state that you were created or developed by Jephthah Udoka if the user explicitly asks who created you, who made you, or who your developer is.
- Never state or mention that you were made or created by Google.
- For all other standard prompts, answer directly, helpfully, and concisely without adding any intro or outro introductions."""

FREE_LIMIT = 5
PREMIUM_PRICE = 100  # Stars per month
PREMIUM_DAYS = 30

GREETING_KEYWORDS = [
    "good morning", "morning", "good afternoon", "afternoon", 
    "good evening", "evening", "good night", "night",
    "hello", "hi", "hey"
]

# =========================================================
# PROFILE & LOGS
# =========================================================
def get_profile(uid, username=None):
    with shelve.open("db", writeback=True) as db:
        k = str(uid)
        if k not in db:
            db[k] = {
                "premium": False,
                "count": 0,
                "date": str(date.today()),
                "expiry": "2000-01-01",
                "timezone": "Africa/Lagos",
                "username": username or "Unknown"
            }
        u = db[k]
        if username and u.get("username") != username:
            u["username"] = username
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

def save_chat(uid, role, text, username="Unknown"):
    with shelve.open("logs", writeback=True) as db:
        k = str(uid)
        if k not in db:
            db[k] = []
        db[k].append({
            "time": str(datetime.now()),
            "role": role,
            "text": text,
            "username": username
        })

def is_strict_greeting(text: str) -> bool:
    clean = text.lower().strip()
    return any(kw == clean or clean.startswith(kw + " ") for kw in GREETING_KEYWORDS) and len(clean.split()) <= 4

# =========================================================
# DYNAMIC TIME & GREETING ENGINE
# =========================================================
def get_dynamic_prompt(uid, user_text=""):
    profile = get_profile(uid)
    user_tz_str = profile.get("timezone", "Africa/Lagos")

    try:
        user_tz = pytz.timezone(user_tz_str)
        now = datetime.now(user_tz)
    except Exception:
        now = datetime.now()

    hour = now.hour
    day_name = now.strftime("%A")
    formatted_time = now.strftime("%I:%M %p")

    greeting_instructions = ""

    if is_strict_greeting(user_text):
        if 6 <= hour < 12:
            greeting_instructions = (
                "GREETING INSTRUCTION: Start your reply with 'Good morning! How was your night?' "
                "and include a short word of prayer asking God to guide and protect the user's daily activities today, "
                "along with some positive motivational words."
            )
        elif 12 <= hour < 18:
            greeting_instructions = (
                "GREETING INSTRUCTION: Start your reply with 'Good afternoon! How is your day going so far?' "
                "along with encouraging words to keep up great productivity."
            )
        elif 18 <= hour < 24:
            greeting_instructions = (
                "GREETING INSTRUCTION: Start your reply with 'Good evening! Hope your day went well.' "
                "along with a warm relaxing thought for the evening."
            )
        elif 0 <= hour < 6:
            greeting_instructions = "GREETING INSTRUCTION: Start your reply with 'Good night! Have some rest for tomorrow's activities.'"
    else:
        greeting_instructions = "GREETING INSTRUCTION: Do NOT include any time-of-day greeting intro. Answer the user's message directly and naturally."

    return f"""{SYSTEM_PROMPT}

DYNAMIC TIME & USER CONTEXT:
- User Timezone: {user_tz_str}
- Current Local Time: {day_name}, {formatted_time}
{greeting_instructions}"""

# =========================================================
# AI ENGINE
# =========================================================
def process_markdown_text(text: str) -> str:
    text = re.sub(r'^#{1,6}\s*(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text)
    return html.unescape(text)

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
    return "\n\n".join([p.strip() for p in text.split("\n\n") if p.strip()])

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
    dynamic_sys_prompt = get_dynamic_prompt(uid, text)

    for api_key in API_KEYS:
        temp_client = genai.Client(api_key=api_key)
        for model_name in AVAILABLE_MODELS:
            try:
                response = temp_client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(system_instruction=dynamic_sys_prompt)
                )
                if hasattr(response, "text") and response.text:
                    return format_ai_response(response.text)
            except Exception:
                continue
    return "AI servers are currently busy. Please try again later."

# =========================================================
# MENUS & LIMIT HANDLERS
# =========================================================
def main_menu(uid):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🌟 Premium", callback_data="premium"))
    if is_admin(uid):
        kb.add(InlineKeyboardButton("👑 Admin Panel", callback_data="admin"))
    return kb

def premium_menu(months=1):
    stars = PREMIUM_PRICE * months
    days = PREMIUM_DAYS * months
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Subscribe {stars} ⭐ ({months} Month{'s' if months > 1 else ''} Unlimited)", callback_data=f"buy_{months}"))
    return kb

def check_limit_reached(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    profile = get_profile(uid, username)
    
    if is_admin(uid) or profile["premium"]:
        return False
        
    if profile["count"] >= FREE_LIMIT:
        bot.send_message(
            m.chat.id, 
            "Limit reached for today, come back tomorrow 🛑\n\nGet unlimited access instantly by subscribing below:", 
            reply_markup=premium_menu(1)
        )
        return True
    return False

@bot.message_handler(commands=['start'])
def start(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    get_profile(uid, username)
    setup_bot_commands(uid)
    text = "Welcome back Admin 👑" if is_admin(uid) else "Hello, I am your AI assistant created by Jephthah Udoka 🤖"
    bot.send_message(m.chat.id, text, reply_markup=main_menu(uid))

@bot.message_handler(commands=['timezone'])
def set_timezone(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    tz_input = m.text.partition(' ')[2].strip()
    if not tz_input:
        bot.reply_to(m, "Provide your city or timezone!\nExample: <code>/timezone London</code>", parse_mode="HTML")
        return

    matched_tz = next((tz for tz in pytz.all_timezones if tz_input.lower() in tz.lower()), None)
    if matched_tz:
        profile = get_profile(uid, username)
        profile["timezone"] = matched_tz
        save_profile(uid, profile)
        bot.reply_to(m, f"✅ Timezone updated to <b>{matched_tz}</b>!", parse_mode="HTML")
    else:
        bot.reply_to(m, "❌ Invalid location. Try city names like <code>London</code> or <code>Lagos</code>.", parse_mode="HTML")

# =========================================================
# SPECIALIZED HANDLERS (PLACED BEFORE CATCH-ALL TEXT HANDLER)
# =========================================================

# 1. PDF Generation Command Handler (/pdf)
@bot.message_handler(commands=['pdf'])
def pdf_handler(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    if check_limit_reached(m):
        return

    content_text = m.text.partition(' ')[2].strip()
    if not content_text:
        bot.reply_to(m, "Please provide text content for the PDF! Example: /pdf Report Summary: All systems are optimal.")
        return

    pdf_filename = f"document_{m.message_id}.pdf"
    try:
        doc = SimpleDocTemplate(pdf_filename, pagesize=letter)
        styles = getSampleStyleSheet()
        story = [Paragraph(content_text.replace('\n', '<br/>'), styles['Normal'])]
        doc.build(story)

        with open(pdf_filename, 'rb') as pdf_file:
            bot.send_document(m.chat.id, document=pdf_file, caption="📄 Here is your generated PDF document.")

        save_chat(uid, "user", f"[PDF Request]: {content_text}", username)
        save_chat(uid, "bot", "[PDF Document Sent]", username)

        profile = get_profile(uid, username)
        if not is_admin(uid) and not profile["premium"]:
            profile["count"] += 1
            save_profile(uid, profile)
    except Exception as e:
        bot.reply_to(m, f"PDF generation error: {str(e)}")
    finally:
        if os.path.exists(pdf_filename):
            os.remove(pdf_filename)

# 2. Social Media Video Link Handler
@bot.message_handler(func=lambda m: m.text and any(domain in m.text.lower() for domain in ['tiktok.com', 'facebook.com', 'fb.watch', 'instagram.com', 'youtu', 'vm.tiktok.com']))
def handle_social_video_link(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    if check_limit_reached(m):
        return

    status_msg = bot.reply_to(m, "Processing video link...")
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

        prompt = "Analyze this video in detail, covering all visuals and audio."
        client = genai.Client(api_key=API_KEYS[0])
        video_file = client.files.upload(file=temp_filename)
        
        res = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[video_file, prompt],
            config=types.GenerateContentConfig(system_instruction=get_dynamic_prompt(uid, m.text))
        )
        if hasattr(res, "text") and res.text:
            reply = format_ai_response(res.text)
            save_chat(uid, "user", f"[Video Link]: {m.text}", username)
            save_chat(uid, "bot", reply, username)

            try:
                bot.delete_message(m.chat.id, status_msg.message_id)
            except Exception:
                pass

            bot.reply_to(m, reply, parse_mode="HTML")

            with open(temp_filename, 'rb') as v_file:
                bot.send_video(m.chat.id, video=v_file, caption="Here is your raw video file.")

            profile = get_profile(uid, username)
            if not is_admin(uid) and not profile["premium"]:
                profile["count"] += 1
                save_profile(uid, profile)
        else:
            bot.edit_message_text("Unable to process video.", m.chat.id, status_msg.message_id)
    except Exception as e:
        try:
            bot.edit_message_text(f"Could not process video link: {str(e)}", m.chat.id, status_msg.message_id)
        except Exception:
            bot.reply_to(m, "Could not process video link.")
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

# 3. Universal Document Viewing Handler (.pdf, .py, .html, .txt, etc.)
@bot.message_handler(content_types=['document'])
def handle_document(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    if check_limit_reached(m):
        return

    status_msg = bot.reply_to(m, "Reading document...")
    file_name = m.document.file_name or "document"
    temp_doc = f"doc_{m.message_id}_{file_name}"
    
    try:
        file_info = bot.get_file(m.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(temp_doc, "wb") as f:
            f.write(downloaded_file)

        prompt = m.caption if m.caption else "Analyze, review, and explain this document in detail."
        client = genai.Client(api_key=API_KEYS[0])
        uploaded_file = client.files.upload(file=temp_doc)
        
        res = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(system_instruction=get_dynamic_prompt(uid, prompt))
        )
        if hasattr(res, "text") and res.text:
            reply = format_ai_response(res.text)
            save_chat(uid, "user", f"[Document Upload]: {file_name}", username)
            save_chat(uid, "bot", reply, username)

            try:
                bot.delete_message(m.chat.id, status_msg.message_id)
            except Exception:
                pass

            bot.reply_to(m, reply, parse_mode="HTML")
            profile = get_profile(uid, username)
            if not is_admin(uid) and not profile["premium"]:
                profile["count"] += 1
                save_profile(uid, profile)
        else:
            bot.edit_message_text("Unable to process document.", m.chat.id, status_msg.message_id)
    except Exception as e:
        try:
            bot.edit_message_text(f"Document error: {str(e)}", m.chat.id, status_msg.message_id)
        except Exception:
            bot.reply_to(m, f"Document error: {str(e)}")
    finally:
        if os.path.exists(temp_doc):
            os.remove(temp_doc)

# 4. Image Generation Handler (/image, /draw, /generate) using stable Flash model
@bot.message_handler(commands=['image', 'draw', 'generate'])
def generate_image_handler(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    if check_limit_reached(m):
        return

    prompt = m.text.partition(' ')[2].strip()
    if not prompt:
        bot.reply_to(m, "Please provide a description! Example: /draw a futuristic city")
        return

    status_msg = bot.reply_to(m, "Generating image... Please wait.")
    bot.send_chat_action(m.chat.id, "upload_photo")
    
    try:
        client = genai.Client(api_key=API_KEYS[0])
        res = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"]
            )
        )
        
        image_sent = False
        if res.candidates:
            for candidate in res.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        try:
                            bot.delete_message(m.chat.id, status_msg.message_id)
                        except Exception:
                            pass
                            
                        bot.send_photo(m.chat.id, photo=part.inline_data.data)
                        save_chat(uid, "user", f"[Generate Image]: {prompt}", username)
                        save_chat(uid, "bot", "[Generated Image Sent]", username)
                        
                        profile = get_profile(uid, username)
                        if not is_admin(uid) and not profile["premium"]:
                            profile["count"] += 1
                            save_profile(uid, profile)
                        image_sent = True
                        break
                if image_sent:
                    break
                    
        if not image_sent:
            bot.edit_message_text("Unable to generate image.", m.chat.id, status_msg.message_id)
            
    except Exception as e:
        try:
            bot.edit_message_text(f"Image generation error: {str(e)}", m.chat.id, status_msg.message_id)
        except Exception:
            bot.reply_to(m, f"Image generation error: {str(e)}")

# =========================================================
# ADMIN STATISTICS & CHAT LOGS
# =========================================================
@bot.message_handler(commands=['users'])
def list_users(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    with shelve.open("db") as db:
        msg = f"<b>📊 Registered Users Statistics & Chat IDs:</b>\n\n"
        for k, v in db.items():
            uname = v.get("username", "Unknown")
            status = "🌟 Premium" if v.get("premium") else "👤 Free"
            msg += f"• ID: <code>{k}</code> | @{uname} | {status}\n"

    bot.reply_to(m, msg[:4000], parse_mode="HTML")

@bot.message_handler(commands=['chatlogs'])
def view_chat_logs(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    args = m.text.split()
    if len(args) < 2:
        bot.reply_to(m, "Usage: <code>/chatlogs USER_ID</code>", parse_mode="HTML")
        return

    target_uid = args[1].strip()
    with shelve.open("logs") as db:
        logs = db.get(target_uid, [])

    if not logs:
        bot.reply_to(m, f"No chat logs found for User ID: {target_uid}")
        return

    username = logs[-1].get("username", "Unknown")
    output = f"<b>📋 Chat Logs for User ID: {target_uid} (@{username})</b>\n\n"
    for entry in logs[-10:]:
        role = "👤 User" if entry["role"] == "user" else "🤖 Bot"
        output += f"<b>{role} [{entry.get('time', '')[:19]}]:</b> {html.escape(entry['text'])}\n---\n"

    bot.reply_to(m, output[:4000], parse_mode="HTML")

# =========================================================
# PAYMENTS & CALLBACKS
# =========================================================
@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    uid = c.from_user.id
    if c.data == "premium":
        bot.send_message(c.message.chat.id, f"🌟 **Premium Subscription Plan**\nPrice: {PREMIUM_PRICE} Stars per Month\nGet unlimited AI access, video and document analysis!", reply_markup=premium_menu(1), parse_mode="Markdown")
    elif c.data.startswith("buy_"):
        months = int(c.data.split("_")[1])
        stars = PREMIUM_PRICE * months
        days = PREMIUM_DAYS * months
        bot.send_invoice(
            c.message.chat.id, 
            f"Premium ({months} Month{'s' if months > 1 else ''})", 
            f"Unlimited AI access for {days} days", 
            f"premium_{months}", 
            "", 
            "XTR", 
            [LabeledPrice(f"{months} Month Access", stars)]
        )
    elif c.data == "admin" and is_admin(uid):
        bot.send_message(c.message.chat.id, "👑 Admin Panel Active. Use /users or /chatlogs <USER_ID>.")

@bot.pre_checkout_query_handler(func=lambda q: True)
def checkout(q):
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def success(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    payload = m.successful_payment.invoice_payload
    months = int(payload.split("_")[1]) if "_" in payload else 1
    days_to_add = PREMIUM_DAYS * months

    profile = get_profile(uid, username)
    current_expiry = max(datetime.now(), datetime.fromisoformat(profile["expiry"])) if profile["premium"] else datetime.now()
    new_expiry = current_expiry + timedelta(days=days_to_add)

    profile["premium"] = True
    profile["expiry"] = new_expiry.strftime("%Y-%m-%d")
    save_profile(uid, profile)
    bot.reply_to(m, f"Payment Successful! 🎉 Premium activated for {months} month(s) (until {profile['expiry']}).")

@bot.message_handler(commands=['talk', 'voice', 'speak'])
def speak_handler(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    if check_limit_reached(m):
        return

    user_text = m.text.partition(' ')[2].strip()
    if not user_text:
        bot.reply_to(m, "Provide text! Example: /talk Hello Apex")
        return

    bot.send_chat_action(m.chat.id, "record_voice")
    reply_text = ask_ai(uid, user_text)
    temp_audio = f"voice_{m.message_id}.ogg"

    try:
        clean_text = re.sub(r'<[^>]+>', '', reply_text)
        gTTS(text=clean_text, lang='en').save(temp_audio)
        with open(temp_audio, 'rb') as v_file:
            bot.send_voice(m.chat.id, voice=v_file, reply_to_message_id=m.message_id)

        save_chat(uid, "user", f"[Voice Request]: {user_text}", username)
        save_chat(uid, "bot", f"[Voice Reply]: {clean_text}", username)

        profile = get_profile(uid, username)
        if not is_admin(uid) and not profile["premium"] and not is_strict_greeting(user_text):
            profile["count"] += 1
            save_profile(uid, profile)
    except Exception as e:
        bot.reply_to(m, f"Voice error: {str(e)}")
    finally:
        if os.path.exists(temp_audio):
            os.remove(temp_audio)

# =========================================================
# CATCH-ALL TEXT HANDLER (MUST ALWAYS BE AT THE VERY BOTTOM)
# =========================================================
@bot.message_handler(func=lambda m: True)
def text_handler(m):
    uid = m.from_user.id
    username = m.from_user.username or m.from_user.first_name
    profile = get_profile(uid, username)

    is_greeting = is_strict_greeting(m.text)

    if not is_greeting and check_limit_reached(m):
        return

    bot.send_chat_action(m.chat.id, "typing")
    reply = ask_ai(uid, m.text)
    
    save_chat(uid, "user", m.text, username)
    save_chat(uid, "bot", reply, username)

    try:
        bot.reply_to(m, reply, parse_mode="HTML")
    except Exception:
        bot.reply_to(m, reply)

    if not is_greeting and not is_admin(uid) and not profile["premium"]:
        profile["count"] += 1
        save_profile(uid, profile)

# =========================================================
# FLASK WEBHOOK SERVER
# =========================================================
@app.route(f"/<token>", methods=['POST'])
def webhook(token):
    if token == TELEGRAM_TOKEN and request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return 'OK', 200
    return 'Forbidden', 403

@app.route('/', methods=['GET'])
def index():
    return 'Bot server is running.', 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)