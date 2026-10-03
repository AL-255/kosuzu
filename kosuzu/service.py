"""Application service shared by the server, desktop client, and mobile web UI."""
import hashlib
import hmac
import json
import secrets
import threading
import time
from urllib.parse import urlsplit
from .github import GitHub
from .http import RemoteError
from .llm import refine
from .model import ValidationError, component, new_event, text
from .suppliers import REGISTRY, lookup


class Service:
    def __init__(self, store, mode="server", github_factory=GitHub):
        self.store, self.mode, self.github_factory = store, mode, github_factory
        self.sync_lock = threading.Lock()

    def login(self, key):
        key = text(key, "access key", 200, True)
        role = "admin" if hmac.compare_digest(key, self.store.setting("admin_key")) else "client" if hmac.compare_digest(key, self.store.setting("client_key")) else None
        if not role:
            raise ValidationError("Incorrect access key")
        token = secrets.token_urlsafe(32)
        ident = hashlib.sha256(token.encode()).hexdigest()
        self.store.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        self.store.execute("INSERT INTO sessions VALUES (?,?,?)", (ident, role, time.time() + 30 * 86400))
        return token

    def session(self, token):
        ident = hashlib.sha256(token.encode()).hexdigest()
        rows = self.store.execute("SELECT role FROM sessions WHERE id=? AND expires>?", (ident, time.time()))
        if not rows:
            return None
        self.store.execute("UPDATE sessions SET expires=? WHERE id=?", (time.time() + 30 * 86400, ident))
        return {"id": ident, "role": rows[0]["role"]}

    def github(self, profile=None):
        token = self.store.profile(profile).get("github_token", "") if profile else self.store.setting("github_token", "")
        return self.github_factory(token, self.store.setting("repo", ""), self.store.setting("branch", "main"))

    def public_settings(self, user):
        profile = self.store.profile(user["id"])
        return {"repo": self.store.setting("repo", ""), "branch": self.store.setting("branch", "main"), "mode": self.mode, "role": user["role"], "client_token_saved": bool(profile.get("github_token")), "server_token_saved": bool(self.store.setting("github_token")), "llm": {k: v for k, v in profile.get("llm", {}).items() if k != "api_key"}, "llm_key_saved": bool(profile.get("llm", {}).get("api_key")), "supplier_credentials_saved": {k: bool(v) for k, v in profile.get("suppliers", {}).items()}, "llm_hosts": self.store.setting("llm_hosts"), "suppliers": [{"key": s.key, "name": s.name, "credential_fields": s.credential_fields} for s in REGISTRY.values()]}

    def save_settings(self, user, data):
        if not isinstance(data, dict):
            raise ValidationError("Settings must be an object")
        if set(data) - {"repo", "branch", "server_token", "client_token", "llm", "suppliers", "llm_hosts"}:
            raise ValidationError("Unknown settings")
        admin_fields = {"repo", "branch", "server_token", "llm_hosts"}
        if set(data) & admin_fields and user["role"] != "admin":
            raise ValidationError("Administrator access required for database settings")
        repo = data.get("repo", self.store.setting("repo"))
        branch = data.get("branch", self.store.setting("branch"))
        # Validate repo/branch locally before saving anything, without sending tokens.
        if repo:
            GitHub("validation", repo, branch)
        profile = self.store.profile(user["id"])
        if "client_token" in data:
            profile["github_token"] = text(data["client_token"], "client token", 500)
        hosts = data.get("llm_hosts", self.store.setting("llm_hosts"))
        if not isinstance(hosts, list) or len(hosts) > 20 or any(not isinstance(h, str) or not h or "/" in h or ":" in h for h in hosts):
            raise ValidationError("LLM hosts must be a list of HTTPS host names")
        if "llm" in data:
            config = {**profile.get("llm", {}), **data["llm"]}
            if set(config) - {"base_url", "model", "api_key"}:
                raise ValidationError("Unknown LLM configuration")
            endpoint = config.get("base_url", "https://api.deepseek.com")
            from .model import safe_url
            safe_url(endpoint)
            if urlsplit(endpoint).hostname not in hosts or urlsplit(endpoint).port not in {None, 443} or urlsplit(endpoint).query or urlsplit(endpoint).fragment:
                raise ValidationError("LLM endpoint must use an administrator-approved HTTPS host")
            config["model"] = text(config.get("model", "deepseek-chat"), "model", 100, True)
            config["api_key"] = text(config.get("api_key", ""), "LLM API key", 500)
            profile["llm"] = config
        if "suppliers" in data:
            if not isinstance(data["suppliers"], dict):
                raise ValidationError("Invalid supplier configuration")
            current = profile.get("suppliers", {})
            for key, credentials in data["suppliers"].items():
                if key not in REGISTRY or not isinstance(credentials, dict) or set(credentials) - set(REGISTRY[key].credential_fields):
                    raise ValidationError("Unknown supplier or credential fields")
                current[key] = {k: text(v, "supplier credential", 500) for k, v in credentials.items()}
            profile["suppliers"] = current
        server_token = text(data.get("server_token", self.store.setting("github_token", "")), "server token", 500)
        for key, value in (("repo", repo), ("branch", branch), ("llm_hosts", hosts), ("github_token", server_token)):
            if user["role"] == "admin":
                self.store.set_setting(key, value)
        self.store.set_profile(user["id"], profile)
        return self.public_settings(user)

    def import_part(self, user, data):
        profile = self.store.profile(user["id"])
        supplier = data.get("supplier")
        # Fail before supplier requests if refinement cannot run.
        if not profile.get("llm", {}).get("api_key"):
            raise ValidationError("Save your LLM API key before importing")
        part, evidence = lookup(supplier, data.get("code"), profile.get("suppliers", {}).get(supplier, {}), product_url=data.get("product_url", ""))
        reviewed = refine(part, evidence, profile["llm"])
        return {"draft_id": self.store.draft(user["id"], reviewed), "original": part, "component": reviewed}

    def create(self, user, data):
        draft = self.store.read_draft(data.get("draft_id", ""), user["id"])
        if not draft:
            raise ValidationError("Import review expired; retrieve and review the part again")
        if data.get("confirmed") is not True:
            raise ValidationError("Confirm the reviewed information before saving")
        edits = data.get("edits", {})
        if not isinstance(edits, dict) or set(edits) - {"manufacturer", "mpn", "description", "category", "package", "location", "attributes"}:
            raise ValidationError("Invalid reviewed field edits")
        reviewed = {**draft, **edits, "review": {**draft["review"], "confirmed": True}}
        reviewed.pop("id", None)
        part = component(reviewed)
        event = new_event("create", part["id"], data.get("quantity"), part, data.get("note", ""))
        # Stable draft -> transaction mapping survives a lost HTTP response.
        saved = self.store.setting("draft-event:" + data["draft_id"])
        if saved:
            if saved["component"] != part or saved["delta"] != event["delta"] or saved["note"] != event["note"]:
                raise ValidationError("This import already has a proposal. Retry unchanged or import a new draft")
            event = saved
        else:
            self.store.set_setting("draft-event:" + data["draft_id"], event)
        return self.submit(user, event)

    def adjust(self, user, data):
        ident = text(data.get("request_id"), "request_id", 32, True)
        event = new_event("adjust", data.get("component_id"), data.get("delta"), note=data.get("note", ""))
        event["id"] = ident
        from .model import validate_event
        event = validate_event(event)
        prior = next((row for row in self.store.outbox(user["id"]) if row["id"] == ident), None)
        if prior:
            old = prior["event"]
            if any(old[k] != event[k] for k in ("delta", "component_id", "note")):
                raise ValidationError("Request ID already has different content")
            event = old
        return self.submit(user, event)

    def submit(self, user, event):
        gh = self.github(user["id"])
        self.store.queue(user["id"], gh.repo, gh.branch, event)
        try:
            result = gh.submit(event)
        except RemoteError as exc:
            result = {"status": "queued", "error": str(exc), "event_id": event["id"]}
        self.store.result(event["id"], result)
        return result

    def flush(self, user):
        gh = self.github(user["id"])
        rows = self.store.outbox(user["id"])
        for row in rows:
            if row["repo"] != gh.repo or row["branch"] != gh.branch or row["result"].get("status") in {"applied", "rejected"}:
                continue
            try:
                result = gh.submit(row["event"])
            except (RemoteError, ValidationError) as exc:
                result = {**row["result"], "error": str(exc)}
            self.store.result(row["id"], result)
        return self.store.outbox(user["id"])

    def inventory(self, user):
        gh = self.github(user["id"])
        key = f"cache:{gh.repo}:{gh.branch}"
        try:
            value = gh.inventory()
            self.store.set_setting(key, {"inventory": value, "time": time.time()})
            return {"inventory": value, "stale": False}
        except RemoteError as exc:
            cache = self.store.setting(key)
            if not cache:
                raise
            return {**cache, "stale": True, "warning": str(exc)}

    def sync(self):
        if self.mode != "server":
            raise ValidationError("Automatic merging only runs in server mode")
        if not self.sync_lock.acquire(blocking=False):
            return {"busy": True}
        repo = self.store.setting("repo", "")
        results = []
        try:
            gh = self.github()
            for pr in gh.proposals():
                if not pr["head"]["ref"].startswith("kosuzu/"):
                    continue
                try:
                    results.append({"number": pr["number"], **gh.merge(pr["number"])})
                    self.store.resolve(repo, pr["number"])
                except (ValidationError, RemoteError) as exc:
                    self.store.error(repo, pr["number"], str(exc))
                    results.append({"number": pr["number"], "error": str(exc)})
            # Closed or manually fixed proposals clear queue entries on the next pass.
            for item in self.store.errors(repo):
                if item["status"] == "open" and item["number"]:
                    pr = gh.call("GET", f"pulls/{item['number']}")
                    if pr["state"] == "closed":
                        self.store.resolve(repo, item["number"])
            self.store.resolve(repo, None)
            self.store.set_setting("last_sync", {"time": time.time(), "results": results})
            return {"results": results}
        except (RemoteError, ValidationError) as exc:
            self.store.error(repo, None, str(exc))
            raise
        finally:
            self.sync_lock.release()

    def queue_action(self, user, data):
        if user["role"] != "admin" or self.mode != "server":
            raise ValidationError("Server administrator access required")
        number = data.get("number")
        if type(number) is not int or number < 1:
            raise ValidationError("Invalid proposal number")
        gh = self.github()
        if data.get("action") == "reject":
            pr = gh.call("GET", f"pulls/{number}")
            if not pr["head"]["ref"].startswith("kosuzu/"):
                raise ValidationError("Only Kosuzu proposals can be rejected")
            gh.reject(number)
            self.store.resolve(gh.repo, number)
            return {"status": "rejected"}
        if data.get("action") == "retry":
            result = gh.merge(number)
            self.store.resolve(gh.repo, number)
            return result
        raise ValidationError("Unknown queue action")
