"""Application service shared by the server, desktop client, and mobile web UI."""
import hashlib
import hmac
import json
import secrets
import threading
import time
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .github import GitHub
from . import catalog
from .http import RemoteError
from .llm import refine
from .model import ValidationError, component, new_event, new_transfer, new_box_event, choose_box, text, safe_url, validate_event, validate_inventory, canonical
from .suppliers import REGISTRY, lookup


class Service:
    def __init__(self, store, mode="server", github_factory=GitHub):
        self.store, self.mode, self.github_factory = store, mode, github_factory
        self.sync_lock = threading.Lock()

    def login(self, key, identity=""):
        key = text(key, "access key", 200, True)
        role = "admin" if hmac.compare_digest(key, self.store.setting("admin_key")) else "client" if hmac.compare_digest(key, self.store.setting("client_key")) else None
        if not role:
            raise ValidationError("Incorrect access key")
        token = identity if re.fullmatch(r"[A-Za-z0-9_-]{43}", identity) else secrets.token_urlsafe(32)
        ident = hashlib.sha256(token.encode()).hexdigest()
        self.store.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        self.store.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?)", (ident, role, time.time() + 30 * 86400))
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
        return {"pages_enabled": self.store.setting("pages_enabled", False), "pages_status": self.pages_status(), "repo": self.store.setting("repo", ""), "branch": self.store.setting("branch", "main"), "mode": self.mode, "role": user["role"], "client_token_saved": bool(profile.get("github_token")), "server_token_saved": bool(self.store.setting("github_token")), "llm": {k: v for k, v in profile.get("llm", {}).items() if k != "api_key"}, "llm_key_saved": bool(profile.get("llm", {}).get("api_key")), "supplier_credentials_saved": {k: bool(v) for k, v in profile.get("suppliers", {}).items()}, "supplier_api_ready": {s.key: bool(s.credential_fields) and all(profile.get("suppliers", {}).get(s.key, {}).get(f) for f in s.credential_fields) for s in REGISTRY.values()}, "features": self.features(profile), "onboarding_complete": profile.get("onboarding_complete", False), "onboarding_step": profile.get("onboarding_step", 0), "llm_hosts": self.store.setting("llm_hosts"), "suppliers": [{"key": s.key, "name": s.name, "credential_fields": s.credential_fields} for s in REGISTRY.values()]}

    @staticmethod
    def features(profile):
        saved = profile.get("features", {})
        return {"llm_enabled": saved.get("llm_enabled", True), "llm_fallback": saved.get("llm_fallback", True), "suppliers": {key: {"enabled": True, "use_api": True, "public_fallback": True, **saved.get("suppliers", {}).get(key, {})} for key in REGISTRY}}

    def save_settings(self, user, data):
        if not isinstance(data, dict):
            raise ValidationError("Settings must be an object")
        if set(data) - {"repo", "branch", "server_token", "client_token", "llm", "suppliers", "llm_hosts", "features", "onboarding_complete", "onboarding_step", "pages_enabled"}:
            raise ValidationError("Unknown settings")
        admin_fields = {"repo", "branch", "server_token", "llm_hosts", "pages_enabled"}
        if set(data) & admin_fields and user["role"] != "admin":
            raise ValidationError("Administrator access required for database settings")
        if "pages_enabled" in data:
            if self.mode != "server" or type(data["pages_enabled"]) is not bool:
                raise ValidationError("Pages publishing requires a server and a true/false switch")
        repo = data.get("repo", self.store.setting("repo"))
        branch = data.get("branch", self.store.setting("branch"))
        # Validate repo/branch locally before saving anything, without sending tokens.
        if repo:
            GitHub("validation", repo, branch)
        profile = self.store.profile(user["id"])
        if "features" in data:
            features = data["features"]
            if not isinstance(features, dict) or set(features) - {"llm_enabled", "llm_fallback", "suppliers"}:
                raise ValidationError("Invalid feature settings")
            merged = self.features(profile)
            for key in ("llm_enabled", "llm_fallback"):
                if key in features:
                    if type(features[key]) is not bool:
                        raise ValidationError("Feature switches must be true or false")
                    merged[key] = features[key]
            suppliers = features.get("suppliers", {})
            if not isinstance(suppliers, dict):
                raise ValidationError("Invalid supplier switches")
            for key, switches in suppliers.items():
                if key not in REGISTRY or not isinstance(switches, dict) or set(switches) - {"enabled", "use_api", "public_fallback"} or any(type(v) is not bool for v in switches.values()):
                    raise ValidationError("Invalid supplier switches")
                merged["suppliers"][key].update(switches)
            profile["features"] = merged
        if "onboarding_step" in data:
            if type(data["onboarding_step"]) is not int or not 0 <= data["onboarding_step"] <= 3:
                raise ValidationError("Invalid setup step")
            profile["onboarding_step"] = data["onboarding_step"]
        if "onboarding_complete" in data:
            if type(data["onboarding_complete"]) is not bool:
                raise ValidationError("Invalid setup completion")
            profile["onboarding_complete"] = data["onboarding_complete"]
        if "client_token" in data:
            profile["github_token"] = text(data["client_token"], "client token", 500)
        hosts = data.get("llm_hosts", self.store.setting("llm_hosts"))
        if not isinstance(hosts, list) or len(hosts) > 20 or any(not isinstance(h, str) or not h or "/" in h or ":" in h for h in hosts):
            raise ValidationError("LLM hosts must be a list of HTTPS host names")
        if "llm" in data:
            if not isinstance(data["llm"], dict):
                raise ValidationError("Invalid LLM configuration")
            config = {**profile.get("llm", {}), **data["llm"]}
            if set(config) - {"base_url", "model", "api_key"}:
                raise ValidationError("Unknown LLM configuration")
            endpoint = config.get("base_url", "https://api.deepseek.com")
            safe_url(endpoint)
            if urlsplit(endpoint).hostname not in hosts or urlsplit(endpoint).port not in {None, 443} or urlsplit(endpoint).query or urlsplit(endpoint).fragment:
                raise ValidationError("LLM endpoint must use an administrator-approved HTTPS host")
            config["model"] = text(config.get("model", "deepseek-flash"), "model", 100, True)
            config["api_key"] = text(config.get("api_key", ""), "LLM API key", 500)
            profile["llm"] = config
        if "suppliers" in data:
            if not isinstance(data["suppliers"], dict):
                raise ValidationError("Invalid supplier configuration")
            current = profile.get("suppliers", {})
            for key, credentials in data["suppliers"].items():
                if key not in REGISTRY or not isinstance(credentials, dict) or set(credentials) - set(REGISTRY[key].credential_fields):
                    raise ValidationError("Unknown supplier or credential fields")
                current[key] = {**current.get(key, {}), **{k: text(v, "supplier credential", 500) for k, v in credentials.items()}}
            profile["suppliers"] = current
        server_token = text(data.get("server_token", self.store.setting("github_token", "")), "server token", 500)
        if data.get("onboarding_complete") and (not repo or not profile.get("github_token") or (self.mode == "server" and user["role"] == "admin" and not server_token)):
            raise ValidationError("Save the repository and required GitHub tokens before finishing setup")
        for key, value in (("repo", repo), ("branch", branch), ("llm_hosts", hosts), ("github_token", server_token)):
            if user["role"] == "admin":
                self.store.set_setting(key, value)
        if "pages_enabled" in data:
            self.store.set_setting("pages_enabled", data["pages_enabled"])
        self.store.set_profile(user["id"], profile)
        return self.public_settings(user)

    def import_part(self, user, data):
        profile = self.store.profile(user["id"])
        supplier = data.get("supplier")
        features = self.features(profile)
        warnings = []
        if supplier == "manual":
            fields = data.get("manual", {})
            if not isinstance(fields, dict) or set(fields) - {"manufacturer", "mpn", "description", "source_url", "datasheet_url", "image_url"}:
                raise ValidationError("Invalid manual component fields")
            part = {"supplier": "manual", "supplier_code": text(fields.get("mpn", ""), "mpn", required=True), **fields}
            evidence = "User-entered information; no distributor lookup was performed."
            warnings.append("This part was entered manually. No distributor lookup was performed.")
        else:
            if supplier not in REGISTRY:
                raise ValidationError("Unknown supplier")
            options = features["suppliers"][supplier]
            if not options["enabled"]:
                raise ValidationError("This supplier is disabled in Settings. Enable it or enter the part manually")
            if features["llm_enabled"] and not features["llm_fallback"] and not profile.get("llm", {}).get("api_key"):
                raise ValidationError("Save your LLM API key, enable manual fallback, or turn off LLM review in Settings")
            credentials = profile.get("suppliers", {}).get(supplier, {})
            fields = REGISTRY[supplier].credential_fields
            use_api = bool(fields) and options["use_api"] and all(credentials.get(f) for f in fields)
            if fields and options["use_api"] and not use_api and not options["public_fallback"]:
                raise ValidationError("Supplier API credentials are incomplete. Save all required fields or enable public-page fallback")
            try:
                part, evidence = lookup(supplier, data.get("code"), credentials if use_api else {}, product_url=data.get("product_url", ""))
            except RemoteError:
                if not use_api or not options["public_fallback"]:
                    raise
                part, evidence = lookup(supplier, data.get("code"), {}, product_url=data.get("product_url", ""))
                warnings.append("The supplier API was unavailable. Details were retrieved from its public page instead.")
            if part.get("attributes", {}).get("Identity basis"):
                warnings.append(part["attributes"]["Identity basis"])
        # Validate user/supplier metadata before sending anything to an LLM.
        part = component({**part, "review": {"model": "manual", "checked_at": datetime.now(timezone.utc).isoformat(), "warnings": [], "confirmed": True}})
        part["review"]["confirmed"] = False
        if features["llm_enabled"] and profile.get("llm", {}).get("api_key"):
            try:
                reviewed = refine(part, evidence, profile["llm"])
            except (RemoteError, ValidationError):
                if not features["llm_fallback"]:
                    raise
                reviewed = part
                warnings.append("LLM review failed. The original details are unchanged; review them manually against the datasheet.")
        else:
            if features["llm_enabled"] and not features["llm_fallback"]:
                raise ValidationError("Save your LLM API key, enable manual fallback, or turn off LLM review in Settings")
            reviewed = part
            warnings.append("LLM review is disabled." if not features["llm_enabled"] else "No LLM key is saved; using manual review.")
        if reviewed["review"]["model"] == "manual":
            warnings.append("Check manufacturer, MPN, package and electrical specifications against the datasheet before confirming.")
        reviewed = {**reviewed, "review": {**reviewed["review"], "warnings": reviewed["review"]["warnings"] + warnings, "confirmed": False}}
        return {"draft_id": self.store.draft(user["id"], reviewed), "original": part, "component": reviewed}

    def check_connection(self, user):
        inventory = self.github(user["id"]).inventory()
        if self.mode == "server" and user["role"] == "admin":
            self.github().inventory()
        return {"revision": inventory["revision"], "components": len(inventory["components"])}

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
        event = new_event("create", part["id"], data.get("quantity"), part, data.get("note", ""), box_id=data.get("box_id", ""))
        # Stable draft -> transaction mapping survives a lost HTTP response.
        with self.store.lock:
            saved = self.store.setting("draft-event:" + data["draft_id"])
            if saved:
                if saved["component"] != part or saved["delta"] != event["delta"] or saved["note"] != event["note"] or saved.get("box_id", "") != event["box_id"]:
                    raise ValidationError("This import already has a proposal. Retry unchanged or import a new draft")
                event = saved
            else:
                self.store.set_setting("draft-event:" + data["draft_id"], event)
        return self.submit(user, event)

    def adjust(self, user, data):
        selected = data.get("box_id")
        if selected is None:
            # Pin the user's current single placement into the proposal. Never
            # silently choose a different location when the server later merges.
            prior = next((row for row in self.store.outbox(user["id"]) if row["id"] == data.get("request_id")), None)
            if prior and "box_id" in prior["event"]:
                selected = prior["event"]["box_id"]
            else:
                inventory = self.inventory(user)["inventory"]
                row = inventory["components"].get(data.get("component_id"))
                if not row: raise ValidationError("Component does not exist; synchronize first")
                selected = choose_box(inventory, row)
        event = new_event("adjust", data.get("component_id"), data.get("delta"), note=data.get("note", ""), box_id=selected)
        return self.request(user, data, event)

    def transfer(self, user, data):
        event = new_transfer(data.get("component_id"), data.get("quantity"), data.get("from_box"), data.get("to_box"), data.get("note", ""))
        return self.request(user, data, event)

    def save_box(self, user, data):
        value = {"id": data.get("box_id"), "name": data.get("name", ""), "description": data.get("description", ""), "image_url": data.get("image_url", "")}
        event = new_box_event(value, data.get("previous"))
        return self.request(user, data, event)

    def request(self, user, data, event):
        ident = text(data.get("request_id"), "request_id", 32, True)
        event["id"] = ident
        event = validate_event(event)
        with self.store.lock:
            prior = next((row for row in self.store.outbox(user["id"]) if row["id"] == ident), None)
            if prior:
                old = prior["event"]
                if canonical({k:v for k,v in old.items() if k not in {"id", "created_at"}}) != canonical({k:v for k,v in event.items() if k not in {"id", "created_at"}}):
                    raise ValidationError("Request ID already has different content")
                event = old
            gh = self.github(user["id"])
            self.store.queue(user["id"], gh.repo, gh.branch, event)
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
            except Exception:
                result = {**row["result"], "error": "Unexpected upstream data; request retained for retry. Check connections or report the problem"}
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
            return {**cache, "inventory": validate_inventory(cache["inventory"]), "stale": True, "warning": str(exc)}

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
                    self.report(gh, pr["number"])
                except (ValidationError, RemoteError) as exc:
                    self.store.error(repo, pr["number"], str(exc))
                    self.report(gh, pr["number"], str(exc))
                    results.append({"number": pr["number"], "error": str(exc)})
            # Closed or manually fixed proposals clear queue entries on the next pass.
            for item in self.store.errors(repo):
                if item["status"] == "open" and item["number"] and item["number"] < 0:
                    self.report(gh, -item["number"], self.store.setting(f"report:{repo}#{-item['number']}", ""))
                elif item["status"] == "open" and item["number"]:
                    pr = gh.call("GET", f"pulls/{item['number']}")
                    if pr["state"] == "closed":
                        self.store.resolve(repo, item["number"])
            self.store.resolve(repo, None)
            self.store.set_setting("last_sync", {"time": time.time(), "results": results})
            pages = self._publish_pages() if self.store.setting("pages_enabled", False) else None
            return {"results": results, "pages": pages}
        except (RemoteError, ValidationError) as exc:
            self.store.error(repo, None, str(exc))
            raise
        finally:
            self.sync_lock.release()

    def pages_status(self):
        status = self.store.setting("pages_status", {})
        if status.get("repo") != self.store.setting("repo", "") or status.get("branch") != self.store.setting("branch", "main"):
            return {}
        return status

    def _publish_pages(self, remove=False):
        status = {"repo": self.store.setting("repo", ""), "branch": self.store.setting("branch", "main"), "time": time.time()}
        try:
            status.update(catalog.unpublish(self.github()) if remove else catalog.publish(self.github()))
        except (RemoteError, ValidationError) as exc:
            status.update(status="error", error=str(exc))
            if isinstance(exc, RemoteError) and exc.status in {403, 404}:
                status["error"] += " Grant the server token Pages: read/write and Contents: read/write, and check that this repository's plan supports GitHub Pages. Alternatively configure Settings → Pages → Deploy from a branch → kosuzu-pages / (root)."
        except Exception:
            status.update(status="error", error="Unexpected catalog publication failure. Stock changes remain applied; retry publishing or check the installed catalog assets")
        self.store.set_setting("pages_status", status)
        return status

    def publish_pages(self, user, remove=False):
        if user["role"] != "admin" or self.mode != "server":
            raise ValidationError("Server administrator access required")
        if not remove and not self.store.setting("pages_enabled", False):
            raise ValidationError("Enable public catalog publishing in Settings first")
        with self.sync_lock:
            status = self._publish_pages(remove)
            if remove and status["status"] != "error":
                self.store.set_setting("pages_enabled", False)
            return status

    def report(self, gh, number, error=""):
        self.store.set_setting(f"report:{gh.repo}#{number}", error)
        try:
            gh.report(number, error)
            self.store.resolve(gh.repo, -number)
        except RemoteError as exc:
            self.store.error(gh.repo, -number, "Could not publish client status. Grant the server Commit statuses: write, then retry server sync. " + str(exc))

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
            self.report(gh, number)
            return result
        raise ValidationError("Unknown queue action")
