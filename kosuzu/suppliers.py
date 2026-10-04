"""Supplier adapters. Register an adapter to add another distributor."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from .http import RemoteError, Transport, secure_opener
from .model import ValidationError, safe_url, text

REGISTRY = {}


def register(adapter):
    REGISTRY[adapter.key] = adapter
    return adapter


def code_text(value):
    value = text(value, "supplier code", 120, True)
    if not re.fullmatch(r"[A-Za-z0-9_.+/#() -]+", value):
        raise ValidationError("Supplier code contains unsupported characters")
    return value


class SupplierRedirect(HTTPRedirectHandler):
    def __init__(self, domains):
        self.domains = domains

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_domain(newurl, self.domains)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_domain(url, domains):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.port not in {None, 443} or parts.hostname not in domains:
        raise ValidationError("Product URL must belong to the selected supplier")


def public_page(url, domains):
    check_domain(url, domains)
    try:
        with secure_opener(SupplierRedirect(domains)).open(Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Kosuzu/0.1)"}), timeout=25) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise RemoteError("Product page exceeds 4 MB")
            return raw.decode("utf-8", errors="replace"), response.url
    except HTTPError as exc:
        raise RemoteError(f"Supplier page HTTP {exc.code}; configure supplier API credentials or provide its exact product URL", exc.code) from None
    except (URLError, TimeoutError, OSError):
        raise RemoteError("Supplier page unavailable; retry or configure API credentials") from None


class ProductPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.scripts, self.lines = {}, [], []
        self.in_json = False
        self.script = ""
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.meta[attrs.get("property", attrs.get("name", ""))] = attrs.get("content", "")
        if tag == "script":
            self.in_json = attrs.get("type") == "application/ld+json"
            self.script = ""
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag == "script" and self.in_json:
            try:
                self.scripts.append(json.loads(self.script))
            except json.JSONDecodeError:
                pass
            self.in_json = False
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if self.in_json:
            self.script += data
        if not self.hidden and data.strip():
            self.lines.append(data.strip())

    def products(self):
        def walk(obj):
            if isinstance(obj, list):
                for child in obj:
                    yield from walk(child)
            elif isinstance(obj, dict):
                types = obj.get("@type", [])
                if "Product" in (types if isinstance(types, list) else [types]):
                    yield obj
                for child in obj.values():
                    if isinstance(child, (dict, list)):
                        yield from walk(child)
        return list(walk(self.scripts))


def parse_page(html, supplier, code, url):
    page = ProductPage()
    page.feed(html)
    matches = []
    for item in page.products():
        identities = [str(item.get(k, "")) for k in ("sku", "mpn", "productID")]
        if any(v.casefold() == code.casefold() for v in identities):
            matches.append(item)
    if len(matches) > 1:
        unique = {json.dumps(v, sort_keys=True): v for v in matches}
        matches = list(unique.values())
    if len(matches) == 1:
        item = matches[0]
        brand = item.get("manufacturer") or item.get("brand") or ""
        if isinstance(brand, dict):
            brand = brand.get("name", "")
        image = item.get("image", "")
        if isinstance(image, list):
            image = image[0] if image else ""
        if isinstance(image, dict):
            image = image.get("url", "")
        attrs = {p.get("name", ""): str(p.get("value", "")) for p in item.get("additionalProperty", []) if isinstance(p, dict) and p.get("name")}
        return draft(supplier, code, str(brand), str(item.get("mpn", "")), item.get("description") or item.get("name", ""), url, image_url=urljoin(url, image) if image else "", attributes=attrs), "\n".join(page.lines)[:12000]
    # LCSC publishes a specifications table even when JSON-LD is absent.
    if supplier == "lcsc" and any(v.casefold() == code.casefold() for v in page.lines):
        def after(labels):
            for i, line in enumerate(page.lines[:-1]):
                if line in labels:
                    return page.lines[i + 1].replace("Asian Brands", "").strip()
            return ""
        mpn = after({"MPN", "Mfr. Part #", "Manufacturer Part Number"})
        manufacturer = after({"Manufacturer"})
        if mpn and manufacturer:
            return draft(supplier, code, manufacturer, mpn, after({"Description", "Key Attributes"}), url, image_url=page.meta.get("og:image", ""), package=after({"Packaging", "Package"})), "\n".join(page.lines)[:12000]
    raise ValidationError("No unambiguous product matching this code on the page. Use the exact product URL or supplier API; no guessed part will be imported")


def draft(supplier, code, manufacturer, mpn, description, source_url, **extra):
    result = {"supplier": supplier, "supplier_code": code, "manufacturer": manufacturer, "mpn": mpn, "description": description, "source_url": source_url, "image_url": "", "datasheet_url": "", "package": "", "category": "", "location": "", "attributes": {}, **extra}
    for field in ("source_url", "image_url", "datasheet_url"):
        if result[field].startswith("http://"):
            result[field] = "https://" + result[field][7:]
        safe_url(result[field])
    return result


class WebSupplier:
    credential_fields = []

    def lookup(self, code, credentials, transport, product_url=""):
        url = product_url or self.search_url(code)
        html, final = public_page(url, self.domains)
        return parse_page(html, self.key, code, final)


@register
class DigiKey(WebSupplier):
    key, name = "digikey", "DigiKey"
    domains = {"www.digikey.com", "digikey.com"}
    credential_fields = ["client_id", "client_secret", "account_id"]

    def search_url(self, code):
        return "https://www.digikey.com/en/products?" + urlencode({"keywords": code})

    def lookup(self, code, credentials, transport, product_url=""):
        if not credentials.get("client_id") or not credentials.get("client_secret"):
            return super().lookup(code, credentials, transport, product_url)
        if not credentials.get("account_id"):
            raise ValidationError("DigiKey client-credentials OAuth requires your account ID. Save it with the client ID and secret in Settings")
        # DigiKey's OAuth endpoint requires form encoding, unlike its product API.
        try:
            req = Request("https://api.digikey.com/v1/oauth2/token", data=urlencode({"client_id": credentials["client_id"], "client_secret": credentials["client_secret"], "grant_type": "client_credentials"}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
            if hasattr(transport, "oauth"):
                token = transport.oauth(req)
            else:
                with secure_opener(SupplierRedirect({"api.digikey.com"})).open(req, timeout=25) as response:
                    token = json.loads(response.read(100000))
            result = transport.request("GET", f"https://api.digikey.com/products/v4/search/{quote(code, safe='')}/productdetails", headers={"Authorization": "Bearer " + token["access_token"], "X-DIGIKEY-Client-Id": credentials["client_id"], "X-DIGIKEY-Account-Id": credentials["account_id"], "X-DIGIKEY-Locale-Site": "US", "X-DIGIKEY-Locale-Language": "en", "X-DIGIKEY-Locale-Currency": "USD"})
            p = result["Product"]
            codes = [p.get("ManufacturerProductNumber", "")] + [v.get("DigiKeyProductNumber", "") for v in p.get("ProductVariations", [])]
            if code.casefold() not in [c.casefold() for c in codes]:
                raise ValidationError("DigiKey returned a different part")
            return draft(self.key, code, p["Manufacturer"]["Name"], p["ManufacturerProductNumber"], p["Description"]["ProductDescription"], p["ProductUrl"], image_url=p.get("PhotoUrl", ""), datasheet_url=p.get("DatasheetUrl", ""), attributes={a["ParameterText"]: a["ValueText"] for a in p.get("Parameters", [])}), json.dumps(p)[:16000]
        except (HTTPError, URLError, TimeoutError, OSError):
            raise RemoteError("DigiKey authentication unavailable; check credentials") from None
        except (KeyError, TypeError, json.JSONDecodeError):
            raise RemoteError("DigiKey response schema changed or credentials invalid") from None


@register
class Mouser(WebSupplier):
    key, name = "mouser", "Mouser"
    domains = {"www.mouser.com", "mouser.com"}
    credential_fields = ["api_key"]

    def search_url(self, code):
        return "https://www.mouser.com/c/?" + urlencode({"q": code})

    def lookup(self, code, credentials, transport, product_url=""):
        if not credentials.get("api_key"):
            return super().lookup(code, credentials, transport, product_url)
        result = transport.request("POST", "https://api.mouser.com/api/v1/search/partnumber?" + urlencode({"apiKey": credentials["api_key"]}), {"SearchByPartRequest": {"mouserPartNumber": code, "partSearchOptions": "1"}})
        parts = result.get("SearchResults", {}).get("Parts", [])
        matches = [p for p in parts if code.casefold() in [str(p.get(k, "")).casefold() for k in ("MouserPartNumber", "ManufacturerPartNumber")]]
        if len(matches) != 1:
            raise ValidationError("Mouser found no unique exact match; check the supplier code")
        p = matches[0]
        return draft(self.key, code, p["Manufacturer"], p["ManufacturerPartNumber"], p["Description"], p["ProductDetailUrl"], image_url=p.get("ImagePath", ""), datasheet_url=p.get("DataSheetUrl", ""), category=p.get("Category", ""), attributes={a["AttributeName"]: a["AttributeValue"] for a in p.get("ProductAttributes", [])}), json.dumps(p)[:16000]


@register
class Arrow(WebSupplier):
    key, name = "arrow", "Arrow"
    domains = {"www.arrow.com", "arrow.com"}
    credential_fields = ["login", "api_key"]

    def search_url(self, code):
        return "https://www.arrow.com/en/products/search?" + urlencode({"q": code})

    def lookup(self, code, credentials, transport, product_url=""):
        if not credentials.get("api_key") or not credentials.get("login"):
            return super().lookup(code, credentials, transport, product_url)
        result = transport.request("GET", "https://api.arrow.com/itemservice/v4/en/search/token?" + urlencode({"search_token": code, "login": credentials["login"], "apikey": credentials["api_key"], "rows": 25}))
        parts = [p for group in result.get("itemserviceresult", {}).get("data", []) for p in group.get("PartList", [])]
        def identities(p):
            ids = [str(p.get("partNum", "")), str(p.get("itemId", ""))]
            for site in p.get("InvOrg", {}).get("webSites", []):
                for source in site.get("sources", []):
                    for part in source.get("sourceParts", []):
                        ids.extend([str(part.get("sourcePartId", "")), str(part.get("productCode", ""))])
            return [s.casefold() for s in ids]
        matches = [p for p in parts if code.casefold() in identities(p)]
        if len(matches) != 1:
            raise ValidationError("Arrow found no unique exact match; use its MPN or item code")
        p = matches[0]
        url = next((r["uri"] for r in p.get("resources", []) if r.get("type") == "cloud_part_detail"), product_url)
        part = draft(self.key, code, p["manufacturer"]["mfrName"], p["partNum"], p.get("desc", ""), url, category=p.get("categoryName", ""), package=p.get("packageType", ""))
        # The Arrow search API doesn't provide product imagery; fetch its product page.
        if url:
            html, final = public_page(url, self.domains)
            page = ProductPage()
            page.feed(html)
            part["image_url"] = safe_url(page.meta.get("og:image", ""))
        return part, json.dumps(p)[:16000]


@register
class LCSC(WebSupplier):
    key, name = "lcsc", "LCSC"
    domains = {"www.lcsc.com", "lcsc.com"}

    def search_url(self, code):
        if not re.fullmatch(r"C[0-9]+", code, re.I):
            raise ValidationError("LCSC codes must be C followed by digits")
        return f"https://www.lcsc.com/product-detail/{code.upper()}.html"


@register
class Adafruit(WebSupplier):
    key, name = "adafruit", "Adafruit"
    domains = {"www.adafruit.com", "adafruit.com"}

    def lookup(self, code, credentials, transport, product_url=""):
        match = re.fullmatch(r"(?:PID[ -]?)?#?([1-9][0-9]{0,7})", code, re.I)
        if not match:
            raise ValidationError("Adafruit codes must be a numeric product ID, such as 3406 or PID 3406")
        ident = match[1]
        html, final = public_page(product_url or f"https://www.adafruit.com/product/{ident}", self.domains)
        page = ProductPage(); page.feed(html)
        products = [p for p in page.products() if str(p.get("sku", p.get("productID", ""))) == ident]
        if len(products) != 1:
            raise ValidationError("Adafruit found no unique exact product ID; check the code and product URL")
        p = products[0]
        brand = p.get("manufacturer") or p.get("brand")
        if isinstance(brand, dict): brand = brand.get("name")
        mpn = p.get("mpn")
        attrs = {a["name"]: str(a.get("value", "")) for a in p.get("additionalProperty", []) if a.get("name")}
        attrs["Adafruit product ID"] = ident
        if not brand or not mpn:
            attrs["Identity basis"] = "Adafruit does not list a complete manufacturer/MPN identity. Catalog identity is used; verify manufacturer and MPN against the product documentation."
        image = p.get("image") or page.meta.get("og:image", "")
        if isinstance(image, list): image = image[0] if image else ""
        if isinstance(image, dict): image = image.get("url", "")
        description = p.get("description") or p.get("name") or page.meta.get("og:title", "")
        # Product descriptions can contain a whole guide; keep the candidate
        # bounded, while the visible page supplies separate review evidence.
        clean = ProductPage(); clean.feed(str(description))
        description = " ".join(clean.lines)[:2000] or str(p.get("name", ""))[:2000]
        return draft(self.key, ident, str(brand or "Adafruit (catalog)"), str(mpn or "ADA-" + ident), description, final, image_url=urljoin(final, image) if image else "", category=str(p.get("category", "")), attributes=attrs), "\n".join(page.lines)[:16000]


def lookup(supplier, code, credentials=None, transport=None, product_url=""):
    if supplier not in REGISTRY:
        raise ValidationError("Unknown supplier")
    return REGISTRY[supplier]().lookup(code_text(code), credentials or {}, transport or Transport(), product_url)
