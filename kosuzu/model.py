"""Strict transaction schema and pure, deterministic inventory reducer."""
import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit


class ValidationError(ValueError):
    pass


def text(value, field, limit=2000, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValidationError(f"{field} must be {'nonempty ' if required else ''}text (max {limit})")
    return value.strip()


def safe_url(value):
    value = text(value, "URL")
    if value and (urlsplit(value).scheme != "https" or not urlsplit(value).hostname or urlsplit(value).username):
        raise ValidationError("Image, product and datasheet URLs must use HTTPS")
    return value


def component_id(manufacturer, mpn):
    return hashlib.sha256((manufacturer.strip().casefold() + "\n" + mpn.strip().casefold()).encode()).hexdigest()[:24]


def component(value):
    if not isinstance(value, dict):
        raise ValidationError("Component must be an object")
    allowed = {"id", "manufacturer", "mpn", "description", "category", "package", "location", "image_url", "datasheet_url", "supplier", "supplier_code", "source_url", "attributes", "review"}
    if set(value) - allowed:
        raise ValidationError("Unknown component fields")
    result = {k: text(value.get(k, ""), k, required=k in {"manufacturer", "mpn", "description", "supplier", "supplier_code"}) for k in allowed - {"id", "attributes", "review"}}
    for k in ("image_url", "datasheet_url", "source_url"):
        result[k] = safe_url(result[k])
    attrs = value.get("attributes", {})
    if not isinstance(attrs, dict) or len(attrs) > 100:
        raise ValidationError("Attributes must be an object with at most 100 entries")
    result["attributes"] = {text(k, "attribute", 100, True): text(v, "attribute value", 500) for k, v in attrs.items()}
    review = value.get("review", {})
    if not isinstance(review, dict) or set(review) != {"model", "checked_at", "warnings", "confirmed"} or review.get("confirmed") is not True:
        raise ValidationError("A completed review and human confirmation are required")
    if not isinstance(review["warnings"], list) or len(review["warnings"]) > 50:
        raise ValidationError("Invalid review warnings")
    result["review"] = {"model": text(review["model"], "model", 100, True), "checked_at": text(review["checked_at"], "checked_at", 100, True), "warnings": [text(w, "warning", 1000) for w in review["warnings"]], "confirmed": True}
    result["id"] = component_id(result["manufacturer"], result["mpn"])
    if value.get("id", result["id"]) != result["id"]:
        raise ValidationError("Component ID does not match manufacturer and MPN")
    return result


def validate_event(value):
    if not isinstance(value, dict) or set(value) - {"schema", "id", "kind", "component_id", "component", "delta", "created_at", "note"}:
        raise ValidationError("Invalid event fields")
    result = copy.deepcopy(value)
    if value.get("schema") != 1 or value.get("kind") not in {"create", "adjust"}:
        raise ValidationError("Unsupported transaction schema or kind")
    if not isinstance(value.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["id"]):
        raise ValidationError("Invalid transaction ID")
    if type(value.get("delta")) is not int or abs(value["delta"]) > 1_000_000_000 or value["delta"] == 0:
        raise ValidationError("Quantity change must be a nonzero integer, at most one billion")
    text(value.get("created_at"), "created_at", 100, True)
    result["note"] = text(value.get("note", ""), "note", 1000)
    if value["kind"] == "create":
        if value["delta"] < 1:
            raise ValidationError("Initial stock must be positive")
        result["component"] = component(value.get("component"))
        result["component_id"] = result["component"]["id"]
        if value.get("component_id") != result["component_id"]:
            raise ValidationError("Invalid component identity")
    elif "component" in value or not re.fullmatch(r"[0-9a-f]{24}", str(value.get("component_id", ""))):
        raise ValidationError("Adjustment needs a valid component ID and cannot replace metadata")
    return result


def empty_inventory():
    return {"schema": 1, "revision": 0, "components": {}, "receipts": {}}


def validate_inventory(value):
    if not isinstance(value, dict) or set(value) != {"schema", "revision", "components", "receipts"} or value["schema"] != 1 or type(value["revision"]) is not int or value["revision"] < 0:
        raise ValidationError("Invalid database snapshot; restore from a trusted backup")
    if not isinstance(value["components"], dict) or not isinstance(value["receipts"], dict):
        raise ValidationError("Invalid database collections")
    for ident, row in value["components"].items():
        if not isinstance(row, dict) or set(row) != {"component", "quantity"} or type(row["quantity"]) is not int or row["quantity"] < 0 or component(row["component"])["id"] != ident:
            raise ValidationError("Invalid stock record")
    for ident, digest in value["receipts"].items():
        if not re.fullmatch(r"[0-9a-f]{32}", ident) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValidationError("Invalid transaction receipt")
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def apply_event(inventory, raw):
    validate_inventory(inventory)
    event = validate_event(raw)
    digest = hashlib.sha256(canonical(event).encode()).hexdigest()
    prior = inventory["receipts"].get(event["id"])
    if prior:
        if prior != digest:
            raise ValidationError("Transaction ID was already used with different content")
        return copy.deepcopy(inventory)
    result = copy.deepcopy(inventory)
    ident = event["component_id"]
    if event["kind"] == "create":
        if ident in result["components"]:
            raise ValidationError("This manufacturer/MPN already exists. Add stock to the existing entry")
        result["components"][ident] = {"component": event["component"], "quantity": 0}
    if ident not in result["components"]:
        raise ValidationError("Component does not exist; synchronize and select an existing component")
    row = result["components"][ident]
    quantity = row["quantity"] + event["delta"]
    if quantity < 0:
        raise ValidationError(f"Insufficient stock: available {row['quantity']}, requested {-event['delta']}. Submit a smaller removal")
    if quantity > 1_000_000_000:
        raise ValidationError("Stock exceeds one billion")
    row["quantity"] = quantity
    result["receipts"][event["id"]] = digest
    result["revision"] += 1
    return result


def new_event(kind, ident, delta, part=None, note=""):
    event = {"schema": 1, "id": uuid.uuid4().hex, "kind": kind, "component_id": ident, "delta": delta, "created_at": datetime.now(timezone.utc).isoformat(), "note": note}
    if part is not None:
        event["component"] = part
    return validate_event(event)
