"""GitHub event proposals and atomic validated two-parent merges."""
import base64
import json
import re
from urllib.parse import quote, urlencode
from .http import RemoteError, Transport
from .model import ValidationError, apply_event, canonical, empty_inventory, validate_event, validate_inventory


class GitHub:
    def __init__(self, token, repo, branch="main", transport=None):
        if not token:
            raise ValidationError("Save a dedicated GitHub token first")
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValidationError("Repository must be owner/name")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", branch) or ".." in branch:
            raise ValidationError("Invalid database branch")
        self.repo, self.branch = repo, branch
        self.transport = transport or Transport()
        self.headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

    def call(self, method, path, data=None):
        return self.transport.request(method, f"https://api.github.com/repos/{self.repo}/{path}", data, self.headers)

    def pages(self, path):
        rows = []
        for page in range(1, 101):
            batch = self.call("GET", path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise RemoteError("Too many results; archive old proposals before continuing")

    def head(self):
        return self.call("GET", "git/ref/heads/" + quote(self.branch, safe=""))["object"]["sha"]

    def repository(self):
        return self.transport.request("GET", f"https://api.github.com/repos/{self.repo}", headers=self.headers)

    def read(self, path, ref):
        value = self.call("GET", "contents/" + quote(path, safe="/") + "?" + urlencode({"ref": ref}))
        if value.get("encoding") != "base64" or not value.get("content"):
            raise ValidationError("Database file missing or too large (GitHub Contents limit)")
        try:
            return json.loads(base64.b64decode(value["content"]))
        except (ValueError, UnicodeError):
            raise ValidationError("Database or proposal file contains invalid JSON") from None

    def inventory(self, ref=None):
        return validate_inventory(self.read("inventory.json", ref or self.head()))

    def initialize(self):
        try:
            head = self.head()
        except RemoteError as exc:
            if exc.status != 409:
                raise
            # Git objects cannot be written into an empty repository. Contents
            # can create the first commit, and omitting sha prevents overwrites.
            default = self.repository()["default_branch"]
            if self.branch != default:
                raise ValidationError(f"This repository is empty. Use its default branch ({default}) to initialize it")
            try:
                self.call("PUT", "contents/inventory.json", {"message": "Initialize Kosuzu inventory", "branch": self.branch, "content": base64.b64encode(canonical(empty_inventory()).encode()).decode()})
            except RemoteError as race:
                if race.status not in {409, 422}:
                    raise
                self.inventory()  # Another initializer won; verify its snapshot.
                return False
            return True
        try:
            self.inventory(head)
            return False
        except RemoteError as exc:
            if exc.status != 404:
                raise
        self.commit_files(head, {"inventory.json": canonical(empty_inventory())}, "Initialize Kosuzu inventory")
        return True

    def commit_files(self, base, files, message, second_parent=None, branch=None):
        base_tree = self.call("GET", "git/commits/" + base)["tree"]["sha"]
        entries = [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()]
        tree = self.call("POST", "git/trees", {"base_tree": base_tree, "tree": entries})["sha"]
        parents = [base] + ([second_parent] if second_parent and second_parent != base else [])
        commit = self.call("POST", "git/commits", {"message": message, "tree": tree, "parents": parents})["sha"]
        target = branch or self.branch
        self.call("PATCH", "git/refs/heads/" + quote(target, safe=""), {"sha": commit, "force": False})
        return commit

    def proposals(self):
        return self.pages("pulls?" + urlencode({"state": "open", "base": self.branch, "sort": "created", "direction": "asc"}))

    def submit(self, raw):
        event = validate_event(raw)
        ident = event["id"]
        base = self.head()
        inventory = self.inventory(base)
        if ident in inventory["receipts"]:
            apply_event(inventory, event)  # Detect reuse with a changed payload.
            return {"status": "applied", "event_id": ident}
        branch = "kosuzu/" + ident
        path = "events/" + ident + ".json"
        try:
            branch_head = self.call("GET", "git/ref/heads/" + quote(branch, safe=""))["object"]["sha"]
        except RemoteError as exc:
            if exc.status != 404:
                raise
            try:
                self.call("POST", "git/refs", {"ref": "refs/heads/" + branch, "sha": base})
                branch_head = base
            except RemoteError as race:
                if race.status != 422:
                    raise
                branch_head = self.call("GET", "git/ref/heads/" + quote(branch, safe=""))["object"]["sha"]
        try:
            prior = self.read(path, branch_head)
            if canonical(validate_event(prior)) != canonical(event):
                raise ValidationError("Proposal ID already has different content")
        except RemoteError as exc:
            if exc.status != 404:
                raise
            self.commit_files(branch_head, {path: canonical(event)}, f"Kosuzu: {event['kind']} {event['delta']:+d}", branch=branch)
        existing = self.pages("pulls?" + urlencode({"state": "all", "head": self.repo.split("/")[0] + ":" + branch, "base": self.branch}))
        if existing:
            pr = existing[0]
            result = {"status": "applied" if pr.get("merged_at") else "rejected" if pr["state"] == "closed" else "pending", "event_id": ident, "number": pr["number"], "url": pr["html_url"]}
            statuses = self.call("GET", f"commits/{pr['head']['sha']}/status").get("statuses", [])
            report = next((s for s in statuses if s["context"] == "kosuzu/inventory"), None)
            if report and report["state"] == "failure" and result["status"] != "applied":
                result["error"] = report["description"]
                if result["status"] == "pending":
                    result["status"] = "blocked"
            return result
        pr = self.call("POST", "pulls", {"title": f"Kosuzu: {event['kind']} {event['delta']:+d}", "head": branch, "base": self.branch, "body": f"Transaction `{ident}`. The Kosuzu server validates this proposal against current stock before merging."})
        return {"status": "pending", "event_id": ident, "number": pr["number"], "url": pr["html_url"]}

    def proposal_event(self, number):
        pr = self.call("GET", f"pulls/{number}")
        if pr["state"] != "open" or pr.get("draft") or pr["base"]["ref"] != self.branch or not pr["head"]["ref"].startswith("kosuzu/") or pr["head"]["repo"] is None or pr["head"]["repo"]["full_name"].casefold() != self.repo.casefold():
            raise ValidationError("Only open, same-repository Kosuzu proposals can be merged")
        files = self.pages(f"pulls/{number}/files")
        if len(files) != 1 or files[0]["status"] != "added" or not re.fullmatch(r"events/[0-9a-f]{32}\.json", files[0]["filename"]):
            raise ValidationError("Proposal must add exactly one transaction; other changes are forbidden")
        event = validate_event(self.read(files[0]["filename"], pr["head"]["sha"]))
        if files[0]["filename"] != f"events/{event['id']}.json" or pr["head"]["ref"] != "kosuzu/" + event["id"]:
            raise ValidationError("Transaction ID must match its filename and branch")
        return pr, event, files[0]["filename"]

    def merge(self, number):
        for attempt in range(4):
            pr, event, path = self.proposal_event(number)
            base = self.head()
            inventory = self.inventory(base)
            updated = apply_event(inventory, event)
            if updated["revision"] == inventory["revision"]:
                # A lost response/restart must never apply the same change twice.
                self.call("PATCH", f"pulls/{number}", {"state": "closed"})
                return {"status": "applied", "event_id": event["id"]}
            try:
                sha = self.commit_files(base, {path: canonical(event), "inventory.json": canonical(updated)}, f"Merge Kosuzu transaction #{number}\n\n{event['id']}", second_parent=pr["head"]["sha"])
                return {"status": "applied", "event_id": event["id"], "sha": sha}
            except RemoteError as exc:
                if exc.status not in {409, 422} or attempt == 3:
                    raise
                # Non-fast-forward rejection: reread stock and validate again.
        raise RemoteError("Database is busy; retry later")

    def reject(self, number):
        self.call("PATCH", f"pulls/{number}", {"state": "closed"})

    def report(self, number, error=""):
        """Publish machine-readable warnings for independent desktop clients."""
        pr = self.call("GET", f"pulls/{number}")
        sha = pr["head"]["sha"]
        description = error[:140] if error else "Inventory transaction applied"
        state = "failure" if error else "success"
        statuses = self.call("GET", f"commits/{sha}/status").get("statuses", [])
        prior = next((s for s in statuses if s["context"] == "kosuzu/inventory"), None)
        if prior and prior["description"] == description and prior["state"] == state:
            return
        self.call("POST", f"statuses/{sha}", {"state": state, "context": "kosuzu/inventory", "description": description})
