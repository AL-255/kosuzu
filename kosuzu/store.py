"""Private SQLite state: credentials, persistent error queue and client outbox."""
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.lock = threading.RLock()
        self.path = self.directory / "state.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        if os.name != "nt":
            self.path.chmod(0o600)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, role TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS errors (id TEXT PRIMARY KEY, repo TEXT NOT NULL, number INTEGER, message TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, profile TEXT NOT NULL, repo TEXT NOT NULL, branch TEXT NOT NULL, event TEXT NOT NULL, result TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, profile TEXT NOT NULL, value TEXT NOT NULL, expires REAL NOT NULL);
        """)
        self.db.commit()
        if not self.setting("admin_key"):
            self.set_setting("admin_key", secrets.token_urlsafe(24))
            self.set_setting("client_key", secrets.token_urlsafe(24))
            self.set_setting("branch", "main")
            self.set_setting("repo", "")
            self.set_setting("llm_hosts", ["api.deepseek.com", "api.openai.com"])

    def execute(self, sql, params=()):
        with self.lock:
            cursor = self.db.execute(sql, params)
            rows = [dict(r) for r in cursor.fetchall()] if cursor.description else []
            self.db.commit()
            return rows

    def setting(self, key, default=None):
        rows = self.execute("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key, value):
        self.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def profile(self, ident):
        rows = self.execute("SELECT value FROM profiles WHERE id=?", (ident,))
        return json.loads(rows[0]["value"]) if rows else {}

    def set_profile(self, ident, value):
        self.execute("INSERT OR REPLACE INTO profiles VALUES (?,?)", (ident, json.dumps(value)))

    def error(self, repo, number, message):
        ident = f"{repo}#{number or 'sync'}"
        self.execute("INSERT INTO errors VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET message=excluded.message,status='open',updated=excluded.updated", (ident, repo, number, message, "open", time.time()))

    def resolve(self, repo, number):
        self.execute("UPDATE errors SET status='resolved',updated=? WHERE id=?", (time.time(), f"{repo}#{number or 'sync'}"))

    def errors(self, repo):
        return self.execute("SELECT * FROM errors WHERE repo=? ORDER BY status,updated DESC", (repo,))

    def queue(self, profile, repo, branch, event):
        self.execute("INSERT OR IGNORE INTO outbox VALUES (?,?,?,?,?,?,?)", (event["id"], profile, repo, branch, json.dumps(event), json.dumps({"status": "queued"}), time.time()))

    def outbox(self, profile):
        rows = self.execute("SELECT * FROM outbox WHERE profile=? ORDER BY updated DESC", (profile,))
        for row in rows:
            row["event"] = json.loads(row["event"])
            row["result"] = json.loads(row["result"])
        return rows

    def result(self, ident, result):
        self.execute("UPDATE outbox SET result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), ident))

    def draft(self, profile, part):
        ident = secrets.token_hex(16)
        self.execute("DELETE FROM drafts WHERE expires<?", (time.time(),))
        self.execute("INSERT INTO drafts VALUES (?,?,?,?)", (ident, profile, json.dumps(part), time.time() + 86400))
        return ident

    def read_draft(self, ident, profile):
        rows = self.execute("SELECT value FROM drafts WHERE id=? AND profile=? AND expires>?", (ident, profile, time.time()))
        return json.loads(rows[0]["value"]) if rows else None
