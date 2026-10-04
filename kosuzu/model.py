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


def box(value):
    if not isinstance(value, dict) or set(value) != {"id", "name", "description", "image_url"}:
        raise ValidationError("Invalid box fields")
    if not isinstance(value["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["id"]):
        raise ValidationError("Invalid box ID")
    return {"id": value["id"], "name": text(value["name"], "box name", 120, True), "description": text(value["description"], "box description"), "image_url": safe_url(value["image_url"])}


def box_id(value):
    if not isinstance(value, str) or (value and not re.fullmatch(r"[0-9a-f]{32}", value)):
        raise ValidationError("Choose a valid box or Unboxed")
    return value


def validate_event(value):
    if not isinstance(value, dict):
        raise ValidationError("Invalid event fields")
    if type(value.get("schema")) is not int:
        raise ValidationError("Unsupported transaction schema")
    kinds = {"create", "adjust"} if value.get("schema") == 1 else {"create", "adjust", "transfer", "box_create", "box_update"} if value.get("schema") == 2 else set()
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in kinds:
        raise ValidationError("Unsupported transaction schema or kind")
    fields = {"schema", "id", "kind", "delta", "created_at", "note"}
    fields |= {"box_id", "box"} if kind.startswith("box_") else {"component_id"}
    if kind == "box_update": fields.add("expected_box")
    if kind == "create": fields.add("component")
    if value["schema"] == 2 and kind in {"create", "adjust"}: fields.add("box_id")
    if kind == "transfer": fields |= {"from_box", "to_box", "quantity"}
    if set(value) - fields:
        raise ValidationError("Invalid event fields")
    result = copy.deepcopy(value)
    if not isinstance(value.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["id"]):
        raise ValidationError("Invalid transaction ID")
    delta = value.get("delta")
    if type(delta) is not int or abs(delta) > 1_000_000_000 or (delta == 0 if kind in {"create", "adjust"} else delta != 0):
        raise ValidationError("Quantity change must be a nonzero integer, at most one billion; box/transfer events use zero")
    text(value.get("created_at"), "created_at", 100, True)
    result["note"] = text(value.get("note", ""), "note", 1000)
    if kind.startswith("box_"):
        result["box"] = box(value.get("box"))
        if result["box"]["id"] != value.get("box_id"):
            raise ValidationError("Box identity does not match transaction")
        if kind == "box_update" and (not isinstance(value.get("expected_box"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["expected_box"])):
            raise ValidationError("Box edits require the previous box version")
    elif kind == "create":
        if delta < 1: raise ValidationError("Initial stock must be positive")
        result["component"] = component(value.get("component"))
        if value.get("component_id") != result["component"]["id"]:
            raise ValidationError("Invalid component identity")
    elif not re.fullmatch(r"[0-9a-f]{24}", str(value.get("component_id", ""))):
        raise ValidationError("Adjustment needs a valid component ID and cannot replace metadata")
    if kind == "transfer":
        for key in ("from_box", "to_box"): box_id(value.get(key))
        if value["from_box"] == value["to_box"]:
            raise ValidationError("Choose different source and destination boxes")
        if type(value.get("quantity")) is not int or not 1 <= value["quantity"] <= 1_000_000_000:
            raise ValidationError("Transfer quantity must be a positive integer")
    if "box_id" in value: box_id(value["box_id"])
    return result


def empty_inventory():
    return {"schema": 2, "revision": 0, "components": {}, "boxes": {}, "receipts": {}}


def validate_inventory(value):
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] not in {1, 2} or type(value.get("revision")) is not int or value["revision"] < 0:
        raise ValidationError("Invalid database snapshot; restore from a trusted backup")
    legacy = value["schema"] == 1
    if set(value) != ({"schema", "revision", "components", "receipts"} if legacy else {"schema", "revision", "components", "boxes", "receipts"}):
        raise ValidationError("Invalid database snapshot fields")
    result = copy.deepcopy(value)
    if legacy: result.update(schema=2, boxes={})
    if any(not isinstance(result[key], dict) for key in ("components", "boxes", "receipts")):
        raise ValidationError("Invalid database collections")
    names = set()
    for ident, stored in result["boxes"].items():
        current = box(stored)
        if current["id"] != ident or current["name"].casefold() in names:
            raise ValidationError("Invalid or duplicate box identity/name")
        names.add(current["name"].casefold())
    for ident, row in result["components"].items():
        fields = {"component", "quantity"} if legacy else {"component", "quantity", "boxes"}
        if not isinstance(row, dict) or set(row) != fields or type(row["quantity"]) is not int or not 0 <= row["quantity"] <= 1_000_000_000 or component(row["component"])["id"] != ident:
            raise ValidationError("Invalid stock record")
        if legacy: row["boxes"] = {"": row["quantity"]}
        if not isinstance(row["boxes"], dict) or not row["boxes"] or len(row["boxes"]) > 1000:
            raise ValidationError("Invalid box allocations")
        for placement, count in row["boxes"].items():
            box_id(placement)
            if (placement and placement not in result["boxes"]) or type(count) is not int or not 0 <= count <= 1_000_000_000:
                raise ValidationError("Invalid box stock record")
        if sum(row["boxes"].values()) != row["quantity"]:
            raise ValidationError("Box quantities must sum to total stock")
    for ident, digest in result["receipts"].items():
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValidationError("Invalid transaction receipt")
    return result


def choose_box(inventory, row, selected=None):
    if selected is None:
        if len(row["boxes"]) != 1:
            raise ValidationError("This part belongs to multiple boxes. Choose the box for this stock change")
        selected = next(iter(row["boxes"]))
    box_id(selected)
    if selected and selected not in inventory["boxes"]:
        raise ValidationError("Box does not exist; synchronize and choose an existing box")
    return selected


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def apply_event(inventory, raw):
    inventory = validate_inventory(inventory)
    event = validate_event(raw)
    digest = hashlib.sha256(canonical(event).encode()).hexdigest()
    prior = inventory["receipts"].get(event["id"])
    if prior:
        if prior != digest: raise ValidationError("Transaction ID was already used with different content")
        return copy.deepcopy(inventory)
    result = copy.deepcopy(inventory)
    kind = event["kind"]
    if kind.startswith("box_"):
        ident = event["box_id"]
        current = result["boxes"].get(ident)
        if kind == "box_create" and current:
            raise ValidationError("Box already exists; synchronize before editing")
        if kind == "box_update" and (not current or hashlib.sha256(canonical(current).encode()).hexdigest() != event["expected_box"]):
            raise ValidationError("Box changed since your edit. Synchronize and edit the latest box")
        if any(b["name"].casefold() == event["box"]["name"].casefold() for key, b in result["boxes"].items() if key != ident):
            raise ValidationError("A box with this name already exists. Choose a different name")
        result["boxes"][ident] = event["box"]
    else:
        ident = event["component_id"]
        if kind == "create":
            if ident in result["components"]:
                raise ValidationError("This manufacturer/MPN already exists. Add stock to the existing entry")
            result["components"][ident] = {"component": event["component"], "quantity": 0, "boxes": {"": 0}}
        if ident not in result["components"]:
            raise ValidationError("Component does not exist; synchronize and select an existing component")
        row = result["components"][ident]
        selected = choose_box(result, row, event.get("from_box") if kind == "transfer" else event.get("box_id"))
        change = -event["quantity"] if kind == "transfer" else event["delta"]
        available = row["boxes"].get(selected, 0)
        if available + change < 0:
            label = result["boxes"][selected]["name"] if selected else "Unboxed"
            raise ValidationError(f"Insufficient stock in {label}: available {available}, requested {-change}. Submit a smaller removal")
        if row["quantity"] + event["delta"] > 1_000_000_000:
            raise ValidationError("Stock exceeds one billion")
        row["boxes"][selected] = available + change
        if kind == "transfer":
            target = choose_box(result, row, event["to_box"])
            row["boxes"][target] = row["boxes"].get(target, 0) + event["quantity"]
        row["quantity"] += event["delta"]
        # Empty placements disappear while the last location stays available
        # for restocking a completely exhausted part.
        row["boxes"] = {key: count for key, count in row["boxes"].items() if count} or {selected: 0}
    result["receipts"][event["id"]] = digest
    result["revision"] += 1
    validate_inventory(result)
    return result


def new_event(kind, ident, delta, part=None, note="", box_id=None):
    event = {"schema": 2, "id": uuid.uuid4().hex, "kind": kind, "component_id": ident, "delta": delta, "created_at": datetime.now(timezone.utc).isoformat(), "note": note}
    if part is not None: event["component"] = part
    if box_id is not None: event["box_id"] = box_id
    return validate_event(event)


def new_transfer(ident, quantity, source, target, note=""):
    event = {"schema": 2, "id": uuid.uuid4().hex, "kind": "transfer", "component_id": ident, "delta": 0, "quantity": quantity, "from_box": source, "to_box": target, "created_at": datetime.now(timezone.utc).isoformat(), "note": note}
    return validate_event(event)


def new_box_event(value, previous=None):
    event = {"schema": 2, "id": uuid.uuid4().hex, "kind": "box_update" if previous is not None else "box_create", "box_id": value["id"], "box": box(value), "delta": 0, "created_at": datetime.now(timezone.utc).isoformat(), "note": ""}
    if previous is not None: event["expected_box"] = hashlib.sha256(canonical(box(previous)).encode()).hexdigest()
    return validate_event(event)
