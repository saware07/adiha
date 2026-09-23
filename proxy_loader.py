"""
proxy_loader.py — Shared proxy loader for UIDAI requests.

Reads proxies.txt from the project root.

Supported formats (one per line, # for comments):
    p1.arealproxy.com:9000:user:pass        ← ArealProxy format
    http://user:pass@host:port
    socks5://user:pass@host:port
    host:port
    user:pass@host:port
"""

import os
import random
import logging

logger = logging.getLogger("ProxyLoader")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_FILE = os.path.join(BASE_DIR, "proxies.txt")

_PROXY_POOL = []
_LOADED = False


def _parse_line(line):
    """Return a full proxy URL string or None if the line is invalid."""
    line = line.strip().strip('\ufeff')
    if not line or line.startswith('#'):
        return None

    if '://' in line:
        return line
    if '@' in line:
        return f"http://{line}"

    parts = line.split(':')
    if len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    return None


def load_proxies(force=False):
    """Load proxies into memory. Cached after first call unless force=True."""
    global _PROXY_POOL, _LOADED
    if _LOADED and not force:
        return _PROXY_POOL

    _PROXY_POOL = []

    if not os.path.exists(PROXY_FILE):
        logger.warning(f"[PROXY] {PROXY_FILE} not found — running without proxies.")
        _LOADED = True
        return _PROXY_POOL

    try:
        with open(PROXY_FILE, 'r', encoding='utf-8-sig', errors='ignore') as f:
            for i, raw in enumerate(f, 1):
                proxy = _parse_line(raw)
                if proxy:
                    _PROXY_POOL.append(proxy)
                else:
                    logger.warning(f"[PROXY] line {i}: skipped (invalid) — {raw.strip()[:60]}")
    except Exception as e:
        logger.error(f"[PROXY] read error: {e}")

    logger.info(f"[PROXY] loaded {len(_PROXY_POOL)} proxies")
    _LOADED = True
    return _PROXY_POOL


def get_random_proxy():
    """Return a random proxy URL from the pool, or None if pool is empty."""
    pool = load_proxies()
    if not pool:
        return None
    return random.choice(pool)


def apply_proxy(session):
    """Attach a random proxy to the given requests.Session.
    Returns the proxy URL that was attached, or None.
    """
    proxy = get_random_proxy()
    if proxy:
        session.proxies = {'http': proxy, 'https': proxy}
    return proxy


def get_session_proxies():
    """Return the dict to pass as `proxies=` to requests calls.
    Returns None if no proxies are loaded.
    """
    proxy = get_random_proxy()
    if proxy:
        return {'http': proxy, 'https': proxy}
    return None
