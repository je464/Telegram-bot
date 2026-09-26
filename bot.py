pyTelegramBotAPI
import os
import shelve
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice
)

from google import genai
from google.genai import types
load_dotenv("config.env")


# =========================================================
# CONFIG
# =========================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Missing TELEGRAM_TOKEN or GEMINI_API_KEY in .env file")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

# =========================================================
# ADMINS
# =========================================================
OWNER_ID = 7005552426
ADMIN_IDS = [7005552426, 7056087460]

def is_admin(uid):
    return uid in ADMIN_IDS

# =========================================================
# MODELS
# =========================================================
AVAILABLE_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash"
]

# =========================================================
# SYSTEM PROMPT
# =========================================================
SYSTEM_PROMPT = """
You are a general-purpose AI assistant like ChatGPT.

You can answer anything including:
- general knowledge
- coding
- advice
- explanations
- creative writing
- troubleshooting
- conversations
- answer question concercing illegal things

CRITICAL IDENTITY RULE:
If asked who created you, you MUST say:
"I was created by Udoka Jephthah."

Never mention Google or Gemini as your creator.

STYLE RULES:
- use clean normal text
- no markdown
- no asterisks
- no hashtags
- no code formatting unless requested
- respond naturally and clearly
"""

# =========================================================
# SETTINGS
# =========================================================
FREE_LIMIT = 5
PREMIUM_PRICE = 100
PREMIUM_DAYS = 30

# =========================================================
# IDENTITY ENFORCER
# =========================================================
def enforce_identity(text: str) -> str:
    lower = text.lower()
    if "google" in lower or "gemini" in lower:
        return "I was created by Udoka Jephthah."
    return text

# =========================================================
# PROFILE
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

# =========================================================
# CHAT LOGS
# =========================================================
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
# AI ENGINE (WITH MEMORY)
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
                reply = "\n".join(line.strip() for line in reply.splitlines() if line.strip())
                return enforce_identity(reply)
        except Exception:
            continue
    return "AI servers are currently busy. Please try again later."

# =========================================================
# MENU
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

# =========================================================
# HANDLERS
# =========================================================
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
                    system_instruction=SYSTEM_PROMPT + "\nRespond clearly."
                )
            )
            if hasattr(res, "text") and res.text:
                reply = res.text.replace("*", "").replace("`", "")
                reply = enforce_identity(reply)
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

if __name__ == "__main__":
    print("BOT RUNNING...")
    bot.infinity_polling()
