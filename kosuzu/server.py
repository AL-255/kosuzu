"""Authenticated HTTP API and static PWA, without runtime dependencies."""
import json
import mimetypes
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from . import __version__
from .http import RemoteError
from .model import ValidationError

WEB = Path(__file__).parent / "web"


def make_server(service, host, port, public_url=""):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.service = service
    httpd.public_url = public_url.rstrip("/") or f"http://{host}:{httpd.server_port}"
    parsed = urlsplit(httpd.public_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        httpd.server_close()
        raise ValidationError("Public URL must be an HTTP(S) origin without a path")
    if parsed.scheme != "https" and host not in {"127.0.0.1", "localhost", "::1"}:
        httpd.server_close()
        raise ValidationError("Network deployments require an HTTPS public URL and reverse proxy")
    httpd.allowed_host = parsed.netloc
    httpd.login_attempts = {}
    httpd.login_lock = threading.Lock()
    return httpd


class Handler(BaseHTTPRequestHandler):
    server_version = "Kosuzu/" + __version__

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        # Do not log cookies, request bodies, or query strings.
        pass

    def response(self, status, value, cookie=None, mime="application/json"):
        data = json.dumps(value).encode() if mime == "application/json" else value
        self.send_response(status)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https: data:; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for item in ([cookie] if isinstance(cookie, str) else cookie or []):
            self.send_header("Set-Cookie", item)
        self.end_headers()
        self.wfile.write(data)

    def cookie_token(self, name="kosuzu_session"):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            return cookies[name].value if name in cookies else ""
        except Exception:
            return ""

    def user(self):
        return self.server.service.session(self.cookie_token())

    def trusted_request(self):
        if self.headers.get("Host") != self.server.allowed_host:
            self.response(403, {"error": "Unrecognized host"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.public_url:
            self.response(403, {"error": "Unrecognized origin"})
            return False
        return True

    def do_GET(self):
        if not self.trusted_request():
            return
        path = urlsplit(self.path).path
        try:
            service = self.server.service
            if path == "/api/health":
                return self.response(200, {"version": __version__, "mode": service.mode})
            if path.startswith("/api/"):
                user = self.user()
                if not user:
                    return self.response(401, {"error": "Sign in with your access key"})
                if path == "/api/settings":
                    return self.response(200, service.public_settings(user))
                if path == "/api/inventory":
                    return self.response(200, service.inventory(user))
                if path == "/api/outbox":
                    return self.response(200, service.store.outbox(user["id"]))
                if path == "/api/queue":
                    if service.mode != "server":
                        return self.response(403, {"error": "Error queue is available in server mode"})
                    return self.response(200, {"errors": service.store.errors(service.store.setting("repo", "")), "last_sync": service.store.setting("last_sync")})
                return self.response(404, {"error": "Unknown API endpoint"})
            names = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/manifest.webmanifest": "manifest.webmanifest", "/sw.js": "sw.js", "/icon.svg": "icon.svg", "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png"}
            if path not in names or not (WEB / names[path]).is_file():
                return self.response(404, {"error": "Not found"})
            mime = mimetypes.guess_type(names[path])[0] or "application/octet-stream"
            if path.endswith(".webmanifest"):
                mime = "application/manifest+json"
            return self.response(200, (WEB / names[path]).read_bytes(), mime=mime)
        except (ValidationError, RemoteError) as exc:
            self.response(400 if isinstance(exc, ValidationError) else 502, {"error": str(exc)})
        except Exception:
            self.response(500, {"error": "Internal error; check server state and retry"})

    def do_POST(self):
        if not self.trusted_request():
            return
        if self.headers.get("X-Kosuzu") != "1" or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.response(403, {"error": "JSON and X-Kosuzu header required"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                return self.response(413, {"error": "Body must be between 1 byte and 1 MB"})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValidationError("JSON body must be an object")
            service = self.server.service
            path = urlsplit(self.path).path
            if path == "/api/login":
                address = self.client_address[0]
                with self.server.login_lock:
                    attempts = self.server.login_attempts.get(address, [])
                    attempts = [t for t in attempts if t > time.time() - 60]
                    if len(attempts) >= 10:
                        return self.response(429, {"error": "Too many sign-in attempts; wait one minute"})
                    attempts.append(time.time())
                    self.server.login_attempts[address] = attempts
                token = service.login(data.get("key", ""), self.cookie_token("kosuzu_identity"))
                secure = "; Secure" if self.server.public_url.startswith("https:") else ""
                return self.response(200, {"ok": True}, cookie=[f"kosuzu_identity={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000{secure}", f"kosuzu_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000{secure}"])
            user = self.user()
            if not user:
                return self.response(401, {"error": "Sign in with your access key"})
            if path == "/api/logout":
                service.store.execute("DELETE FROM sessions WHERE id=?", (user["id"],))
                service.store.execute("DELETE FROM profiles WHERE id=?", (user["id"],))
                return self.response(200, {"ok": True}, cookie="kosuzu_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
            if path == "/api/settings":
                return self.response(200, service.save_settings(user, data))
            if path == "/api/import":
                return self.response(200, service.import_part(user, data))
            if path == "/api/create":
                return self.response(200, service.create(user, data))
            if path == "/api/adjust":
                return self.response(200, service.adjust(user, data))
            if path == "/api/flush":
                return self.response(200, service.flush(user))
            if path in {"/api/initialize", "/api/sync", "/api/queue"}:
                if user["role"] != "admin":
                    return self.response(403, {"error": "Administrator access required"})
                if path == "/api/initialize":
                    gh = service.github() if service.mode == "server" else service.github(user["id"])
                    return self.response(200, {"created": gh.initialize()})
                if path == "/api/sync":
                    return self.response(200, service.sync())
                return self.response(200, service.queue_action(user, data))
            self.response(404, {"error": "Unknown API endpoint"})
        except (ValueError, TypeError, RemoteError) as exc:
            message = str(exc) if isinstance(exc, (ValidationError, RemoteError)) else "Invalid request data"
            self.response(502 if isinstance(exc, RemoteError) else 400, {"error": message})
        except Exception:
            self.response(500, {"error": "Internal error; check server state and retry"})


def periodic(service, stopped, interval):
    while not stopped.wait(interval):
        if service.mode == "server" and service.store.setting("repo") and service.store.setting("github_token"):
            try:
                service.sync()
            except Exception as exc:
                service.store.error(service.store.setting("repo", ""), None, str(exc) if isinstance(exc, (ValidationError, RemoteError)) else "Unexpected sync failure; inspect local database and retry")
        # Retry each saved client outbox even when its browser is closed.
        for profile in service.store.execute("SELECT id FROM profiles"):
            try:
                if service.store.profile(profile["id"]).get("github_token") and service.store.setting("repo"):
                    service.flush({"id": profile["id"], "role": "client"})
            except (ValidationError, RemoteError):
                pass
