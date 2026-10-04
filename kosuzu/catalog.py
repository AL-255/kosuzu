"""Read-only inventory exports and an isolated GitHub Pages publisher."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from .http import RemoteError
from .model import ValidationError, canonical, validate_inventory

BRANCH = "kosuzu-pages"
SOURCE = {"branch": BRANCH, "path": "/"}
ASSETS = Path(__file__).parent / "catalog_assets"


def export(inventory, repo, branch):
    inventory = validate_inventory(inventory)
    return {"schema": 1, "repo": repo, "branch": branch, "revision": inventory["revision"],
            "boxes": list(inventory["boxes"].values()),
            "parts": [{**{k: v for k, v in row["component"].items() if k != "review"},
                       "quantity": row["quantity"], "boxes": row["boxes"]}
                      for _, row in sorted(inventory["components"].items())]}


def files(inventory, repo, branch):
    result = {p.name: p.read_text(encoding="utf-8") for p in ASSETS.iterdir() if p.is_file()}
    result["data.json"] = canonical(export(inventory, repo, branch))
    result[".nojekyll"] = ""
    digest = hashlib.sha256(canonical(result).encode()).hexdigest()
    result["kosuzu-catalog.json"] = canonical({"publisher": "kosuzu", "schema": 1, "repo": repo,
                                              "source_branch": branch, "digest": digest})
    return result


def site(gh):
    try:
        current = gh.call("GET", "pages")
    except RemoteError as exc:
        if exc.status != 404:
            raise
        return None
    if current.get("source") != SOURCE or current.get("build_type", "legacy") != "legacy":
        raise ValidationError("This repository already has a different GitHub Pages site. Kosuzu will not replace it; use a separate database repository")
    return current


def publish(gh):
    if gh.branch == BRANCH:
        raise ValidationError("Use a database branch other than kosuzu-pages")
    denied = None
    try:
        current = site(gh)
    except RemoteError as exc:
        if exc.status != 403:
            raise
        # Contents-only tokens can prepare the isolated branch for manual Pages
        # configuration. Never change site configuration without reading it.
        current, denied = None, exc
    changed = False
    for attempt in range(4):
        try:
            head = gh.call("GET", "git/ref/heads/" + quote(BRANCH, safe=""))["object"]["sha"]
        except RemoteError as exc:
            if exc.status != 404:
                raise
            head = None
        prior = None
        if head:
            try:
                prior = gh.read("kosuzu-catalog.json", head)
            except RemoteError as exc:
                if exc.status != 404:
                    raise
            if not isinstance(prior, dict) or prior.get("publisher") != "kosuzu" or prior.get("schema") != 1 or prior.get("repo") != gh.repo or prior.get("source_branch") != gh.branch:
                raise ValidationError("The kosuzu-pages branch is not owned by this database's publisher; it was left unchanged")
        generated = files(gh.inventory(), gh.repo, gh.branch)
        if prior == json.loads(generated["kosuzu-catalog.json"]):
            break
        # A fresh tree prevents events, receipts and unrelated repository files
        # from being published. Only the catalog branch advances; stock is untouched.
        tree = gh.call("POST", "git/trees", {"tree": [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in generated.items()]})["sha"]
        commit = gh.call("POST", "git/commits", {"message": "Publish Kosuzu searchable inventory", "tree": tree, "parents": [head] if head else []})["sha"]
        try:
            if head:
                gh.call("PATCH", "git/refs/heads/" + quote(BRANCH, safe=""), {"sha": commit, "force": False})
            else:
                gh.call("POST", "git/refs", {"ref": "refs/heads/" + BRANCH, "sha": commit})
            changed = True
            break
        except RemoteError as exc:
            if exc.status not in {409, 422} or attempt == 3:
                raise
    if denied:
        return {"status": "prepared", "changed": changed,
                "warning": "Catalog files are ready on kosuzu-pages. The server token cannot read GitHub Pages settings. Grant Pages: read/write, or configure repository Settings → Pages → Deploy from a branch → kosuzu-pages / (root). If already configured, branch updates will publish automatically."}
    if not current:
        try:
            current = gh.call("POST", "pages", {"build_type": "legacy", "source": SOURCE})
        except RemoteError as exc:
            if exc.status not in {409, 422}:
                raise
            current = site(gh)
            if not current:
                raise
    return {"url": current["html_url"], "status": current.get("status", "building"), "changed": changed}


def unpublish(gh):
    if site(gh):
        gh.call("DELETE", "pages")
    return {"status": "unpublished"}
