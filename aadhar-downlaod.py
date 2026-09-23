import sys
import os
import re
import base64
import requests
import uuid
import ddddocr
import time
import random
from io import BytesIO

# Configuration
BASE_URL = "https://tathya.uidai.gov.in"
CAPTCHA_URL = f"{BASE_URL}/audioCaptchaService/api/captcha/v3/generation"
OTP_URL = f"{BASE_URL}/unifiedAppAuthService/api/v2/generate/aadhaar/otp"
DOWNLOAD_URL = f"{BASE_URL}/downloadAadhaarService/api/aadhaar/download"


def _detect_file_type(b):
    if b[:4] == b'%PDF' or b[:5] == b'%PDF-':
        return 'pdf'
    if b[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if b[:2] == b'\xff\xd8':
        return 'jpg'
    return 'unknown'


def _is_base64_str(s):
    if not isinstance(s, str) or len(s) < 100:
        return False
    if s.startswith('data:'):
        s = s.split(',', 1)[1] if ',' in s else s
    if len(s) % 4 != 0:
        return False
    try:
        base64.b64decode(s, validate=False)
        return True
    except Exception:
        return False


def _extract_file_from_json(data, field_path="root", found=None):
    """Recursively search JSON for base64-encoded PDF/PNG/JPG."""
    if found is None:
        found = []
    if isinstance(data, dict):
        for key, value in list(data.items()):
            if isinstance(value, str) and _is_base64_str(value):
                try:
                    clean = value.split(',', 1)[1] if value.startswith('data:') and ',' in value else value
                    dec = base64.b64decode(clean, validate=False)
                    ftype = _detect_file_type(dec)
                    if ftype != 'unknown':
                        found.append({
                            'field': f"{field_path}.{key}",
                            'type': ftype, 'data': dec, 'size': len(dec),
                        })
                except Exception:
                    pass
            if isinstance(value, (dict, list)):
                _extract_file_from_json(value, f"{field_path}.{key}", found)
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            if isinstance(item, (dict, list)):
                _extract_file_from_json(item, f"{field_path}[{idx}]", found)
    return found


def _solve_captcha(img_bytes):
    """Solve captcha with beta OCR + PIL preprocessing (same as aad.py)."""
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
        ocr_beta = ddddocr.DdddOcr(beta=True, show_ad=False)
        r = ocr_beta.classification(buf.getvalue())
        if r and len(r) >= 4:
            r = ''.join(c for c in r if c.isalnum())
            if len(r) >= 4:
                return r[:6]
    except:
        pass
    try:
        ocr = ddddocr.DdddOcr(show_ad=False)
        return ocr.classification(img_bytes)
    except:
        return ""


def run_download(eid, chat_id, unique_suffix="", auto_mode=False):
    # Session to keep cookies/session state
    session = requests.Session()
    from requests.adapters import HTTPAdapter
    from urllib3.util import Retry
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))

    # Attach a random proxy
    try:
        import proxy_loader
        proxy = proxy_loader.apply_proxy(session)
        if proxy:
            print(f"🌐 Using proxy: {proxy.split('@')[-1]}")
        else:
            print("🌐 No proxy configured — connecting direct.")
        sys.stdout.flush()
    except Exception as _pe:
        print(f"⚠️ Proxy loader failed: {_pe}")
        sys.stdout.flush()

    request_id = str(uuid.uuid4())

    # Browser-like headers (same as aad.py)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en_IN",
        "Content-Type": "application/json",
        "appid": "MYAADHAAR",
        "x-request-id": request_id,
        "Origin": "https://myaadhaar.uidai.gov.in",
        "Referer": "https://myaadhaar.uidai.gov.in/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Connection": "keep-alive"
    }

    print("--- STEP 1: Fetching Captcha ---")
    sys.stdout.flush()

    captcha_payload = {
        "captchaLength": "6",
        "captchaType": "2",
        "audioCaptchaRequired": True
    }

    # Send PDF OTP (up to 5 attempts)
    cap_txn_id = None
    captcha_val = None
    otp_txn_id = None
    last_server_msg = "Failed to send PDF OTP after maximum attempts."
    sent = False

    for attempt in range(1, 6):
        try:
            res_cap = session.post(CAPTCHA_URL, json=captcha_payload, headers=headers, timeout=30)
            if res_cap.status_code != 200:
                print(f"⚠️ Captcha fetch HTTP {res_cap.status_code}")
                sys.stdout.flush()
                continue

            try:
                cap_data = res_cap.json()
            except ValueError:
                raise Exception("Aadhaar Portal returned non-JSON during captcha load.")

            if not cap_data or 'imageBase64' not in cap_data or 'transactionId' not in cap_data:
                raise Exception("UIDAI captcha generation failed. Invalid response.")

            img_b64 = cap_data['imageBase64']
            cap_txn_id = cap_data['transactionId']

            img_bytes = base64.b64decode(img_b64)

            # After 3 failed attempts, prompt for manual captcha (manual mode only)
            if attempt >= 3 and not auto_mode:
                print(f"🔑 MANUAL CAPTCHA REQUIRED | {img_b64}")
                sys.stdout.flush()
                captcha_val = sys.stdin.readline().strip()
                if not captcha_val:
                    raise Exception("No manual captcha entered.")
            else:
                captcha_val = _solve_captcha(img_bytes)
                captcha_val = re.sub(r'[^a-zA-Z0-9]', '', captcha_val)
                if len(captcha_val) < 4:
                    print(f"⚠️ [OCR] Rejected noisy read '{captcha_val}' (len {len(captcha_val)}). Retrying...")
                    sys.stdout.flush()
                    continue

            print(f"Decoded Captcha: {captcha_val}")
            sys.stdout.flush()

            # Send OTP request
            otp_payload = {
                "eidNumber": eid,
                "idType": "eid",
                "captchaTxnId": cap_txn_id,
                "captchaValue": captcha_val,
                "resendOTP": False,
                "transactionId": request_id
            }

            res_otp = session.post(OTP_URL, json=otp_payload, headers=headers, timeout=45)
            try:
                otp_data = res_otp.json()
            except ValueError:
                raise Exception("Aadhaar Portal returned non-JSON during OTP request.")

            if otp_data.get('status') == "Success":
                print("✅ OTP Sent Successfully!")
                sys.stdout.flush()
                otp_txn_id = otp_data['txnId']
                sent = True
                break
            else:
                msg = otp_data.get('message') or (otp_data.get('responseData') or {}).get('message') or 'OTP generation failed'
                print(f"Server Response Attempt {attempt}: {msg}")
                sys.stdout.flush()
                if msg:
                    last_server_msg = msg
                if msg and "technical difficulties" in msg.lower():
                    raise Exception(f"⚠️ UIDAI portal is temporarily facing technical difficulties with this number. Please try again after 1 hour or try with another number. ||| Real Server Response: {msg}")
                # Captcha issue → loop retries
        except Exception as ex:
            print(f"⚠️ Error on attempt {attempt}: {ex}")
            sys.stdout.flush()
            if attempt >= 5:
                break

    if not sent or not otp_txn_id:
        raise Exception(last_server_msg)

    # Prompt for OTP
    print("\n" + "=" * 60)
    print("🔑 ENTER THE OTP RECEIVED ON YOUR REGISTERED MOBILE")
    sys.stdout.flush()
    otp_code = sys.stdin.readline().strip()
    print("=" * 60)

    if not otp_code:
        raise Exception("No OTP entered.")

    # Download PDF
    download_payload = {
        "eid": eid,
        "mask": False,
        "otp": otp_code,
        "otpTxnId": otp_txn_id
    }

    download_headers = headers.copy()
    download_headers["transactionId"] = request_id

    print("Downloading Aadhaar PDF from UIDAI secure server...")
    sys.stdout.flush()

    res_dl = session.post(DOWNLOAD_URL, json=download_payload, headers=download_headers, timeout=90)

    # ---------- Direct PDF response ----------
    if res_dl.status_code == 200 and (res_dl.content[:4] == b'%PDF' or res_dl.content[:5] == b'%PDF-'):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cracked_dir = os.path.join(script_dir, "cracked_aadhar")
        os.makedirs(cracked_dir, exist_ok=True)
        file_path = os.path.join(cracked_dir, f"Aadhaar_{chat_id}{unique_suffix}.pdf")
        with open(file_path, "wb") as f:
            f.write(res_dl.content)
        print(f"\n========================================")
        print(f"🎉 SUCCESS! Aadhaar PDF Downloaded Successfully!")
        print(f"📁 Saved as: {file_path}")
        print(f"========================================")
        sys.stdout.flush()
        return

    if res_dl.status_code != 200:
        raise Exception(f"Download failed: HTTP {res_dl.status_code}")

    # ---------- JSON response — extract base64 PDF ----------
    try:
        dl_json = res_dl.json()
    except ValueError:
        raise Exception("Download failed: non-JSON response")

    # Status check
    if dl_json.get('status') == "Success":
        # Try to find base64-encoded PDF recursively
        found = _extract_file_from_json(dl_json)
        chosen = None
        for item in found:
            if item['type'] == 'pdf':
                chosen = item
                break
        if chosen is None and found:
            chosen = found[0]

        if chosen and chosen['type'] == 'pdf':
            script_dir = os.path.dirname(os.path.abspath(__file__))
            cracked_dir = os.path.join(script_dir, "cracked_aadhar")
            os.makedirs(cracked_dir, exist_ok=True)
            file_path = os.path.join(cracked_dir, f"Aadhaar_{chat_id}{unique_suffix}.pdf")
            with open(file_path, "wb") as f:
                f.write(chosen['data'])
            print(f"\n========================================")
            print(f"🎉 SUCCESS! PDF extracted from {chosen['field']} ({chosen['size']} bytes)")
            print(f"📁 Saved as: {file_path}")
            print(f"========================================")
            sys.stdout.flush()
            return

    # Fallback: legacy data.aadhaarPdf field
    if dl_json.get('status') == "Success" and 'data' in dl_json:
        try:
            pdf_b64 = dl_json['data']['aadhaarPdf']
            pdf_bytes = base64.b64decode(pdf_b64)
            script_dir = os.path.dirname(os.path.abspath(__file__))
            cracked_dir = os.path.join(script_dir, "cracked_aadhar")
            os.makedirs(cracked_dir, exist_ok=True)
            file_path = os.path.join(cracked_dir, f"Aadhaar_{chat_id}{unique_suffix}.pdf")
            with open(file_path, "wb") as f:
                f.write(pdf_bytes)
            print(f"\n========================================")
            print(f"🎉 SUCCESS! Aadhaar PDF Downloaded Successfully!")
            print(f"📁 Saved as: {file_path}")
            print(f"========================================")
            sys.stdout.flush()
            return
        except Exception as e:
            print(f"⚠️ Legacy extraction failed: {e}")
            sys.stdout.flush()

    # Report error
    err = dl_json.get('errorDetails') or dl_json.get('message') or dl_json.get('statusMessage') or 'no PDF in response'
    if isinstance(err, dict):
        err = err.get('messageEnglish') or err.get('messageLocal') or str(err)[:200]
    raise Exception(f"Download failed: {err}")


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        EID = sys.argv[1]
        CHAT_ID = sys.argv[2]
        UNIQUE_SUFFIX = sys.argv[3] if len(sys.argv) >= 4 else ""
        AUTO_MODE = (len(sys.argv) >= 5 and sys.argv[4].lower() == "auto")
    else:
        print("Usage: python aadhar-downlaod.py <EID> <CHAT_ID> [UNIQUE_SUFFIX] [auto]")
        sys.exit(1)

    try:
        run_download(EID, CHAT_ID, unique_suffix=UNIQUE_SUFFIX, auto_mode=AUTO_MODE)
    except Exception as e:
        print(f"An error occurred: {e}", file=sys.stderr)
        sys.exit(1)
