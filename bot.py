import html
import io
import os
import re
import shelve
import smtplib
import sqlite3
import threading
import time
import pytz
import yt_dlp
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

# PDF Generation library imports
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

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

# Register command menu bar shortcuts
try:
    bot.set_my_commands([
        BotCommand("start", "Start the bot & open menu"),
        BotCommand("pdf", "Generate a downloadable PDF"),
        BotCommand("email", "Send an email message"),
        BotCommand("image", "Generate an AI image"),
        BotCommand("timezone", "Set your local timezone"),
        BotCommand("talk", "Send a voice note reply")
    ])
except Exception as e:
    print(f"Error setting commands menu: {e}")

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
PREMIUM_PRICE = 100
PREMIUM_DAYS = 30

GREETING_KEYWORDS = [
    "good morning", "morning", "good afternoon", "afternoon", 
    "good evening", "evening", "good night", "night",
    "hello", "hi", "hey", "amen", "thank you", "thanks"
]

# =========================================================
# DATABASE INITIALIZATION (SQLITE FOR USERS & LOGS)
# =========================================================
def init_db():
    conn = sqlite3.connect('bot_logs.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_logs (
            user_id INTEGER,
            username TEXT,
            message_text TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def log_user_message(user_id, username, text):
    try:
        conn = sqlite3.connect('bot_logs.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_logs (user_id, username, message_text, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, username or "No Username", text, str(datetime.now()))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Log Error: {e}")

# =========================================================
# PROFILE & SHELVE STORAGE
# =========================================================
def get_profile(uid, username=""):
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
        if username:
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

def is_greeting_reply(text: str) -> bool:
    clean = text.lower().strip()
    return any(kw in clean for kw in GREETING_KEYWORDS) and len(clean.split()) <= 6

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

    if is_greeting_reply(user_text):
        if 6 <= hour < 12:
            greeting_instructions = (
                "GREETING INSTRUCTION: Start your reply with 'Good morning! How was your night?' "
                "and include a short word of prayer asking God to guide and protect the user's daily activities today, "
                "adding thoughtful encouragement for great success."
            )
        elif 12 <= hour < 18:
            if day_name == "Saturday":
                greeting_instructions = "GREETING INSTRUCTION: Start your reply with 'Good afternoon! Hope you are enjoying your weekend! Keep up your wonderful energy!'"
            else:
                greeting_instructions = "GREETING INSTRUCTION: Start your reply with 'Good afternoon! How is your day going so far? Remember that small steps lead to big progress today!'"
        elif 0 <= hour < 6:
            greeting_instructions = "GREETING INSTRUCTION: Start your reply with 'Good night! Have some rest for tomorrow's activities. You did amazing today!'"
    else:
        greeting_instructions = "GREETING INSTRUCTION: Do NOT include any time-of-day greeting intro unless explicitly asked. Answer the user's question directly and helpfully."

    return f"""{SYSTEM_PROMPT}

DYNAMIC TIME & USER CONTEXT:
- User Timezone: {user_tz_str}
- Current Local Time: {day_name}, {formatted_time}
{greeting_instructions}"""

# =========================================================
# AI ENGINE (MULTI-KEY + MULTI-MODEL FALLBACK)
# =========================================================
def process_markdown_text(text: str) -> str:
    text = re.sub(r'^#{1,6}\s*(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text)
    text = html.unescape(text)
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
    dynamic_sys_prompt = get_dynamic_prompt(uid, text)

    for api_key in API_KEYS:
        temp_client = genai.Client(api_key=api_key)
        for model_name in AVAILABLE_MODELS:
            try:
                response = temp_client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=dynamic_sys_prompt
                    )
                )

                if hasattr(response, "text") and response.text:
                    return format_ai_response(response.text)
            except Exception as e:
                print(f"⚠️ Key (...{api_key[-4:]}) | Model {model_name} error: {e}")
                continue

    return "AI servers are currently busy. Please try again later."

# =========================================================
# MENUS & HANDLERS
# =========================================================
def main_menu(uid):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🌟 Premium", callback_data="premium"))
    if is_admin(uid):
        kb.add(InlineKeyboardButton("👑 Admin Panel", callback_data="admin"))
    return kb

def premium_menu(stars=100):
    months = max(1, stars // 100)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Subscribe {stars} ⭐ ({months} Month{'s' if months > 1 else ''} Access)", callback_data=f"buy_{stars}"))
    return kb

@bot.message_handler(commands=['start'])
def start(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    get_profile(uid, username)
    text = "Welcome back Creator 👑" if is_admin(uid) else "Hello, I am your AI assistant created by Udoka Jephthah 🤖"
    bot.send_message(m.chat.id, text, reply_markup=main_menu(uid))

@bot.message_handler(commands=['timezone', 'setlocation', 'resettime'])
def set_timezone(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    tz_input = m.text.partition(' ')[2].strip()

    if not tz_input:
        bot.reply_to(m, "Provide your city or timezone!\nExample: <code>/timezone London</code> or <code>/timezone Africa/Lagos</code>", parse_mode="HTML")
        return

    matched_tz = None
    for tz in pytz.all_timezones:
        if tz_input.lower() in tz.lower():
            matched_tz = tz
            break

    if matched_tz:
        profile = get_profile(uid, username)
        profile["timezone"] = matched_tz
        save_profile(uid, profile)

        user_now = datetime.now(pytz.timezone(matched_tz)).strftime("%I:%M %p")
        bot.reply_to(m, f"✅ Timezone updated to <b>{matched_tz}</b>!\nLocal time: <b>{user_now}</b>.", parse_mode="HTML")
    else:
        bot.reply_to(m, "❌ Invalid location. Try city names like <code>London</code>, <code>Lagos</code>, <code>Tokyo</code>, or <code>New_York</code>.", parse_mode="HTML")

@bot.message_handler(commands=['users'])
def list_users(m):
    uid = m.from_user.id
    if not is_admin(uid):
        bot.reply_to(m, "❌ Unauthorized command.")
        return

    with shelve.open("db") as db:
        total_users = len(db.keys())
        premium_users = sum(1 for k, v in db.items() if v.get("premium"))
        
        msg = f"<b>📊 Bot User Statistics & Database</b>\n\n"
        msg += f"• <b>Total Registered Users:</b> {total_users}\n"
        msg += f"• <b>Premium Users:</b> {premium_users}\n\n"
        msg += "<b>Active Users List:</b>\n"
        
        for k, v in list(db.items())[:20]: # Show up to 20 users
            uname = v.get("username", "No Username")
            msg += f"• ID: <code>{k}</code> | @{uname} | Premium: {v.get('premium')}\n"

    bot.reply_to(m, msg, parse_mode="HTML")

@bot.message_handler(commands=['chatlogs'])
def view_chat_logs(m):
    uid = m.from_user.id
    if not is_admin(uid):
        bot.reply_to(m, "❌ Unauthorized command.")
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

    output = f"<b>📋 Recent Chat Logs for {target_uid}:</b>\n\n"
    for entry in logs[-10:]:
        role = "👤 User" if entry["role"] == "user" else "🤖 Bot"
        output += f"<b>{role}:</b> {html.escape(entry['text'])}\n---\n"

    if len(output) > 4000:
        output = output[:4000]

    bot.reply_to(m, output, parse_mode="HTML")

@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    uid = c.from_user.id
    if c.data == "premium":
        bot.send_message(c.message.chat.id, f"🌟 <b>Premium Subscription Plan</b>\n\nSubscribe <b>{PREMIUM_PRICE} Stars</b> to get 1 month of unlimited access!", reply_markup=premium_menu(PREMIUM_PRICE), parse_mode="HTML")
    elif c.data.startswith("buy_"):
        stars = int(c.data.split("_")[1])
        bot.send_invoice(c.message.chat.id, "Premium Subscription", f"Unlimited AI access for {max(1, stars // 100)} month(s)", f"premium_{stars}", "", "XTR", [LabeledPrice("Premium Access", stars)])
    elif c.data == "admin" and is_admin(uid):
        bot.send_message(c.message.chat.id, "👑 <b>Admin Panel Active</b>\n\nCommands available:\n• <code>/users</code> - View user stats & IDs\n• <code>/chatlogs USER_ID</code> - Inspect user chat logs", parse_mode="HTML")

@bot.pre_checkout_query_handler(func=lambda q: True)
def checkout(q):
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def success(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)
    profile["premium"] = True
    
    # Calculate months based on stars paid (100 stars = 30 days per month)
    payload = m.successful_payment.invoice_payload
    stars_paid = int(payload.split("_")[1]) if "_" in payload else 100
    months = max(1, stars_paid // 100)
    
    profile["expiry"] = (datetime.now() + timedelta(days=PREMIUM_DAYS * months)).strftime("%Y-%m-%d")
    save_profile(uid, profile)
    bot.reply_to(m, f"Premium Activated Successfully for {months} month(s)! 🎉")

# =========================================================
# VOICE COMMAND HANDLER (/talk, /voice, /speak)
# =========================================================
@bot.message_handler(commands=['talk', 'voice', 'speak'])
def speak_handler(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)

    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
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
        tts = gTTS(text=clean_text, lang='en')
        tts.save(temp_audio)

        with open(temp_audio, 'rb') as v_file:
            bot.send_voice(m.chat.id, voice=v_file, reply_to_message_id=m.message_id)

        save_chat(uid, "user", f"[Voice Request]: {user_text}")
        save_chat(uid, "bot", f"[Voice Reply]: {clean_text}")

        if not is_admin(uid) and not profile["premium"] and not is_greeting_reply(user_text):
            profile["count"] += 1
            save_profile(uid, profile)
    except Exception as e:
        bot.reply_to(m, f"Voice error: {str(e)}")
    finally:
        if os.path.exists(temp_audio):
            os.remove(temp_audio)

# =========================================================
# PDF DOCUMENT CREATION TOOL (/pdf)
# =========================================================
@bot.message_handler(commands=['pdf'])
def create_pdf_handler(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
        return

    content_text = m.text.partition(' ')[2].strip()
    if not content_text:
        bot.reply_to(m, "Please provide content for your PDF!\nExample: <code>/pdf My Business Plan Proposal</code>", parse_mode="HTML")
        return

    status_msg = bot.reply_to(m, "📄 Generating PDF document...")
    pdf_filename = f"document_{m.message_id}.pdf"

    try:
        doc = SimpleDocTemplate(pdf_filename, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []

        title_style = ParagraphStyle('PDFTitle', parent=styles['Heading1'], fontSize=18, spaceAfter=15)
        body_style = ParagraphStyle('PDFBody', parent=styles['Normal'], fontSize=12, spaceAfter=10, leading=16)

        story.append(Paragraph("Apex Document Export", title_style))
        story.append(Spacer(1, 10))

        for paragraph in content_text.split('\n'):
            if paragraph.strip():
                story.append(Paragraph(html.escape(paragraph), body_style))

        doc.build(story)

        with open(pdf_filename, 'rb') as pdf_file:
            bot.send_document(m.chat.id, document=pdf_file, caption="Here is your generated PDF document! 📄")

        try:
            bot.delete_message(m.chat.id, status_msg.message_id)
        except Exception:
            pass

        save_chat(uid, "user", f"[PDF Request]: {content_text}")
        save_chat(uid, "bot", "[PDF Document Sent]")

        if not is_admin(uid) and not profile["premium"]:
            profile["count"] += 1
            save_profile(uid, profile)

    except Exception as e:
        bot.edit_message_text(f"❌ Could not generate PDF: {str(e)}", m.chat.id, status_msg.message_id)
    finally:
        if os.path.exists(pdf_filename):
            os.remove(pdf_filename)

# =========================================================
# EMAIL SENDING TOOL (/email)
# =========================================================
@bot.message_handler(commands=['email'])
def email_handler(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
        return

    parts = m.text.partition(' ')[2].split('|')
    if len(parts) < 3:
        bot.reply_to(
            m,
            "<b>✉️ Email Formatting Error:</b>\n"
            "Please use this exact format:\n"
            "<code>/email recipient@gmail.com | Subject Here | Your message text here</code>",
            parse_mode="HTML"
        )
        return

    to_email = parts[0].strip()
    subject = parts[1].strip()
    body = parts[2].strip()

    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_PASSWORD")

    if not sender_email or not sender_password:
        bot.reply_to(m, "❌ Email sender credentials are not configured on the server environment.")
        return

    status_msg = bot.reply_to(m, "✉️ Sending email...")

    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, msg.as_string())
        server.quit()

        bot.edit_message_text(f"✅ Email successfully sent to <b>{to_email}</b>!", m.chat.id, status_msg.message_id, parse_mode="HTML")
        save_chat(uid, "user", f"[Email to {to_email}]: {subject}")
        save_chat(uid, "bot", "[Email Sent Successfully]")

    except Exception as e:
        bot.edit_message_text(f"❌ Failed to send email: {str(e)}", m.chat.id, status_msg.message_id)

# =========================================================
# SOCIAL MEDIA VIDEO LINK HANDLER
# =========================================================
@bot.message_handler(func=lambda m: m.text and any(domain in m.text.lower() for domain in ['tiktok.com', 'facebook.com', 'fb.watch', 'instagram.com', 'youtu', 'vm.tiktok.com', 'vt.tiktok.com']))
def handle_social_video_link(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
        return

    status_msg = bot.reply_to(m, "📥 Downloading and analyzing video...")
    temp_filename = f"social_{m.message_id}.mp4"
    
    ydl_opts = {
        'outtmpl': temp_filename,
        'format': 'mp4/best[filesize<45M]/best',
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    try:
        url = [word for word in m.text.split() if "http" in word][0]
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(temp_filename) or os.path.getsize(temp_filename) == 0:
            bot.edit_message_text("❌ Could not download video link. The link might be private or restricted.", m.chat.id, status_msg.message_id)
            return

        prompt = "Analyze this video in detail, including visual content, background audio, speech, and overall context."
        analysis_reply = None

        for api_key in API_KEYS:
            temp_client = genai.Client(api_key=api_key)
            try:
                video_file = temp_client.files.upload(file=temp_filename)
                for model in AVAILABLE_MODELS:
                    try:
                        res = temp_client.models.generate_content(
                            model=model,
                            contents=[video_file, prompt],
                            config=types.GenerateContentConfig(system_instruction=get_dynamic_prompt(uid, m.text))
                        )
                        if hasattr(res, "text") and res.text:
                            analysis_reply = format_ai_response(res.text)
                            break
                    except Exception:
                        continue
                if analysis_reply:
                    break
            except Exception:
                continue

        try:
            bot.delete_message(m.chat.id, status_msg.message_id)
        except Exception:
            pass

        if analysis_reply:
            try:
                bot.reply_to(m, analysis_reply, parse_mode="HTML")
            except Exception:
                bot.reply_to(m, analysis_reply)
        else:
            bot.send_message(m.chat.id, "⚠️ Video downloaded successfully, but AI analysis could not be completed.")

        with open(temp_filename, 'rb') as v_file:
            bot.send_video(m.chat.id, video=v_file, caption="Here is the downloaded video file! 📥")

        save_chat(uid, "user", f"[Video Link]: {m.text}")
        save_chat(uid, "bot", analysis_reply if analysis_reply else "[Video Downloaded & Sent]")

        if not is_admin(uid) and not profile["premium"]:
            profile["count"] += 1
            save_profile(uid, profile)

    except Exception as e:
        bot.edit_message_text(f"❌ Could not process video link: {str(e)}", m.chat.id, status_msg.message_id)
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

# =========================================================
# IMAGE GENERATION HANDLER (/image, /draw, /generate)
# =========================================================
@bot.message_handler(commands=['image', 'draw', 'generate'])
def generate_image_handler(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
        return

    prompt = m.text.partition(' ')[2].strip()
    if not prompt:
        bot.reply_to(m, "Please provide a description! Example: /draw a cute robot surfing")
        return

    bot.send_chat_action(m.chat.id, "upload_photo")
    try:
        image_models = ["gemini-2.5-flash", "gemini-2.5-pro"]
        for api_key in API_KEYS:
            temp_client = genai.Client(api_key=api_key)
            for model_name in image_models:
                try:
                    res = temp_client.models.generate_content(
                        model=model_name,
                        contents=[prompt],
                        config=types.GenerateContentConfig(response_modalities=["IMAGE"])
                    )
                    for part in res.parts:
                        if hasattr(part, "inline_data") and part.inline_data:
                            bot.send_photo(m.chat.id, photo=part.inline_data.data)
                            save_chat(uid, "user", f"[Generate Image]: {prompt}")
                            save_chat(uid, "bot", "[Generated Image Sent]")
                            if not is_admin(uid) and not profile["premium"]:
                                profile["count"] += 1
                                save_profile(uid, profile)
                            return
                except Exception:
                    continue
        bot.reply_to(m, "Unable to generate image.")
    except Exception as e:
        bot.reply_to(m, f"Image generation error: {e}")

# =========================================================
# DOCUMENT & FILE VIEWER HANDLER (PDF, TXT, DOCX, PY, HTML, ETC.)
# =========================================================
@bot.message_handler(content_types=['document'])
def handle_document(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
        return

    status_msg = bot.reply_to(m, "📂 Reading document...")
    temp_filename = f"doc_{m.message_id}_{m.document.file_name}"

    try:
        file_info = bot.get_file(m.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(temp_filename, "wb") as f:
            f.write(downloaded_file)

        prompt = m.caption if m.caption else "Summarize and analyze this document in detail."

        for api_key in API_KEYS:
            temp_client = genai.Client(api_key=api_key)
            try:
                doc_file = temp_client.files.upload(file=temp_filename)
                for model in AVAILABLE_MODELS:
                    try:
                        res = temp_client.models.generate_content(
                            model=model,
                            contents=[doc_file, prompt],
                            config=types.GenerateContentConfig(system_instruction=get_dynamic_prompt(uid, prompt))
                        )
                        if hasattr(res, "text") and res.text:
                            reply = format_ai_response(res.text)
                            save_chat(uid, "user", f"[Document Sent]: {m.document.file_name}")
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
            except Exception:
                continue

        bot.edit_message_text("Unable to analyze document.", m.chat.id, status_msg.message_id)

    except Exception as e:
        bot.reply_to(m, f"Document error: {str(e)}")
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

# =========================================================
# PHOTO HANDLER
# =========================================================
@bot.message_handler(content_types=['photo'])
def handle_photo(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
        return

    bot.send_chat_action(m.chat.id, "typing")
    try:
        file_info = bot.get_file(m.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        image = Image.open(io.BytesIO(downloaded_file))

        prompt = m.caption if m.caption else "Describe this image in detail."
        for api_key in API_KEYS:
            temp_client = genai.Client(api_key=api_key)
            for model in AVAILABLE_MODELS:
                try:
                    res = temp_client.models.generate_content(
                        model=model,
                        contents=[image, prompt],
                        config=types.GenerateContentConfig(system_instruction=get_dynamic_prompt(uid, prompt))
                    )
                    if hasattr(res, "text") and res.text:
                        reply = format_ai_response(res.text)
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

# =========================================================
# DIRECT VIDEO FILE HANDLER
# =========================================================
@bot.message_handler(content_types=['video'])
def handle_video(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
        return

    status_msg = bot.reply_to(m, "Analyzing video...")
    temp_filename = f"video_{m.message_id}.mp4"
    try:
        file_info = bot.get_file(m.video.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(temp_filename, "wb") as f:
            f.write(downloaded_file)

        prompt = m.caption if m.caption else "Analyze this video."

        for api_key in API_KEYS:
            temp_client = genai.Client(api_key=api_key)
            try:
                video_file = temp_client.files.upload(file=temp_filename)
                for model in AVAILABLE_MODELS:
                    try:
                        res = temp_client.models.generate_content(
                            model=model,
                            contents=[video_file, prompt],
                            config=types.GenerateContentConfig(system_instruction=get_dynamic_prompt(uid, prompt))
                        )
                        if hasattr(res, "text") and res.text:
                            reply = format_ai_response(res.text)
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
            except Exception:
                continue

        bot.edit_message_text("Failed to analyze video.", m.chat.id, status_msg.message_id)

    except Exception as e:
        bot.reply_to(m, f"Video error: {str(e)}")
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

# =========================================================
# VOICE NOTE HANDLER
# =========================================================
@bot.message_handler(content_types=['voice'])
def voice(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)
    if not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
        return
    bot.send_chat_action(m.chat.id, "record_voice")
    
    try:
        file = bot.get_file(m.voice.file_id)
        data = bot.download_file(file.file_path)
    except Exception:
        bot.reply_to(m, "Failed to download voice note.")
        return

    audio_part = types.Part.from_bytes(data=data, mime_type="audio/ogg")
    
    for api_key in API_KEYS:
        temp_client = genai.Client(api_key=api_key)
        for model in AVAILABLE_MODELS:
            temp_audio = None
            try:
                res = temp_client.models.generate_content(
                    model=model, 
                    contents=[audio_part],
                    config=types.GenerateContentConfig(
                        system_instruction=get_dynamic_prompt(uid, "voice note") + "\nRespond concisely."
                    )
                )
                if hasattr(res, "text") and res.text:
                    reply = format_ai_response(res.text)
                    clean_text = re.sub(r'<[^>]+>', '', reply)
                    temp_audio = f"voice_reply_{m.message_id}.ogg"
                    
                    tts = gTTS(text=clean_text, lang='en')
                    tts.save(temp_audio)

                    with open(temp_audio, 'rb') as v_file:
                        bot.send_voice(m.chat.id, voice=v_file, reply_to_message_id=m.message_id)

                    save_chat(uid, "user", "[Voice Note Sent]")
                    save_chat(uid, "bot", reply)

                    if not is_admin(uid) and not profile["premium"]:
                        profile["count"] += 1
                        save_profile(uid, profile)
                    return
            except Exception:
                continue
            finally:
                if temp_audio and os.path.exists(temp_audio):
                    os.remove(temp_audio)

    bot.reply_to(m, "Failed to process voice note.")

# =========================================================
# TEXT MESSAGE HANDLER
# =========================================================
@bot.message_handler(func=lambda m: True)
def text_handler(m):
    uid = m.from_user.id
    username = m.from_user.username or ""
    profile = get_profile(uid, username)

    # Log user message into SQLite DB as requested
    log_user_message(uid, username, m.text)

    is_free_greeting = is_greeting_reply(m.text)

    # If limit reached and NOT a greeting / greeting reply
    if not is_free_greeting and not is_admin(uid) and not profile["premium"] and profile["count"] >= FREE_LIMIT:
        bot.reply_to(m, "Limit reached for today, come back tomorrow", reply_markup=premium_menu(PREMIUM_PRICE))
        return

    bot.send_chat_action(m.chat.id, "typing")
    reply = ask_ai(uid, m.text)
    
    save_chat(uid, "user", m.text)
    save_chat(uid, "bot", reply)

    try:
        bot.reply_to(m, reply, parse_mode="HTML")
    except Exception:
        bot.reply_to(m, reply)

    # Greetings and greeting replies DO NOT count towards the user chat limit
    if not is_free_greeting and not is_admin(uid) and not profile["premium"]:
        profile["count"] += 1
        save_profile(uid, profile)

# =========================================================
# FLASK WEBHOOK SERVER
# =========================================================
@app.route(f"/{TELEGRAM_TOKEN}", methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    return 'Forbidden', 403

@app.route('/', methods=['GET'])
def index():
    return 'Bot server is running.', 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)