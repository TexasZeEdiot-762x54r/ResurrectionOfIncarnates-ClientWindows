"""Bounded profile exchange, authenticated with the native per-player token over WireGuard."""
import hmac
import ipaddress
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request

import nrt_security as security

STORE = {}
LOCK = threading.Lock()
MAX_AGE = 3600
MAX_BODY = 512 * 1024
MAX_PROFILES = 128
CONDITION = threading.Condition()
PENDING = None
WORKER_STARTED = False


def valid_profile(data, uid):
    if not isinstance(data, dict) or str(data.get('_id')) != str(uid):
        return False
    if any(str(data.get(key, uid)) != str(uid) for key in ('sv_uid', 'sv_onlineId')):
        return False
    pending = [(data, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 30000:
            return False
        if isinstance(value, str) and len(value.encode('utf-8')) > 255:
            return False
        if isinstance(value, dict):
            if any(key.lower() in {'password', 'ticket', 'lobby_token', 'oauth_token', 'oauth_token_secret'} for key in value):
                return False
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return True


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def reply(self, status, data=None):
        out = json.dumps(data, separators=(',', ':')).encode() if data is not None else b''
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if out:
            self.wfile.write(out)

    def authorize(self, write=False):
        match = re.fullmatch(r'/profile/([1-9][0-9]{0,19})', self.path)
        if not match or int(match[1]) >= 2 ** 64:
            self.reply(404)
            return None
        uid = int(match[1])
        if security.AUTH_TOKENS:
            bearer = self.headers.get('Authorization', '')
            token = bearer[7:] if bearer.startswith('Bearer ') else ''
            if not re.fullmatch(r'[0-9a-fA-F]{32}', token):
                self.reply(401)
                return None
            token = token.lower()
            caller = next((identity for identity, key in security.AUTH_TOKENS.items()
                           if hmac.compare_digest(key.hex(), token)), None)
            if caller is None:
                self.reply(401)
                return None
            if uid not in security.AUTH_TOKENS or (write and caller != uid):
                self.reply(403)
                return None
        return uid

    def do_PUT(self):
        uid = self.authorize(write=True)
        if uid is None:
            return
        try:
            size = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            self.reply(400)
            return
        if self.headers.get('Transfer-Encoding') or not 0 < size <= MAX_BODY:
            self.reply(413)
            return
        try:
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError()
            data = json.loads(body)
            if not valid_profile(data, uid):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            self.reply(400)
            return
        with LOCK:
            now = time.monotonic()
            for key, (_, timestamp) in list(STORE.items()):
                if now - timestamp >= MAX_AGE:
                    STORE.pop(key, None)
            if uid not in STORE and len(STORE) >= MAX_PROFILES:
                self.reply(503)
                return
            STORE[uid] = (data, now)
        self.reply(204)

    def do_GET(self):
        uid = self.authorize()
        if uid is None:
            return
        with LOCK:
            entry = STORE.get(uid)
            if entry and time.monotonic() - entry[1] >= MAX_AGE:
                STORE.pop(uid, None)
                entry = None
        self.reply(200, entry[0]) if entry else self.reply(404)


def start_in_background(host, port, log=print):
    if not ipaddress.ip_address(host).is_loopback and not security.AUTH_TOKENS:
        raise ValueError('External profile relay requires credentials')
    server = RelayServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log('authenticated profile relay listening on', f'{host}:{port}')
    return server


def request(url, token, data=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    body = json.dumps(data, separators=(',', ':')).encode() if data is not None else None
    if body is not None and len(body) > MAX_BODY:
        raise ValueError('Profile too large')
    req = urllib.request.Request(url, data=body, headers=headers, method='PUT' if body is not None else 'GET')
    # Never send a private WireGuard credential through a machine's configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=3 if body is not None else 1.5) as response:
        if body is not None:
            return None
        out = response.read(MAX_BODY + 1)
        if len(out) > MAX_BODY:
            raise ValueError('Relay response too large')
        return json.loads(out)


def _publisher():
    global PENDING
    while True:
        with CONDITION:
            while PENDING is None:
                CONDITION.wait()
            args = PENDING
            PENDING = None
        try:
            request(*args)
        except Exception:
            pass


def publish(url, token, data):
    global PENDING, WORKER_STARTED
    with CONDITION:
        PENDING = (url, token, data)
        if not WORKER_STARTED:
            threading.Thread(target=_publisher, daemon=True).start()
            WORKER_STARTED = True
        CONDITION.notify()
