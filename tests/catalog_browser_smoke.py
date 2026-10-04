"""Verify Pages project paths, fuzzy search, pagination and offline searches."""
import functools
import json
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from kosuzu.catalog import files
from kosuzu.model import apply_event, empty_inventory, new_event, new_box_event
from tests.helpers import part


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def run(browser_type, mobile=False):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "kosuzu-test"; root.mkdir()
        inventory = empty_inventory()
        box = {"id": "a" * 32, "name": "BOXA", "description": "Workbench passives", "image_url": ""}
        inventory = apply_event(inventory, new_box_event(box))
        candidate = part(); candidate["category"] = "Resistors"; candidate["description"] = '10 kohm resistor <img src=x onerror="window.injected=true">'
        inventory = apply_event(inventory, new_event("create", candidate["id"], 10, candidate, box_id=box["id"]))
        chip = part("ESP32-S3"); chip.update(description="WiFi microcontroller", category="Processors", attributes={}, source_url="https://example.com/product")
        inventory = apply_event(inventory, new_event("create", chip["id"], 2, chip))
        for i in range(45):
            extra = part(f"X-{i:03d}")
            inventory = apply_event(inventory, new_event("create", extra["id"], 1, extra))
        for name, content in files(inventory, "AL-255/kosuzu-test", "main").items():
            (root / name).write_text(content, encoding="utf-8")
        handler = functools.partial(QuietHandler, directory=directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        browser = browser_type.launch()
        context = browser.new_context(viewport={"width":390,"height":844} if mobile else {"width":1440,"height":1000}, is_mobile=mobile, has_touch=mobile)
        page = context.new_page(); errors=[]; requests=[]
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        try:
            page.goto(f"http://127.0.0.1:{server.server_port}/kosuzu-test/")
            expect(page.locator("#count")).to_have_text("47 components found")
            expect(page.locator(".part")).to_have_count(40)
            page.locator("#more").click(); expect(page.locator(".part")).to_have_count(47)
            page.locator("#search").fill("ESP32S3"); expect(page.locator(".part h2")).to_have_text(["ESP32-S3"])
            page.locator("#search").fill("microcontroler"); expect(page.locator(".part h2")).to_have_text(["ESP32-S3"])
            page.locator("#search").fill("I want a 10 kohm resitsor"); expect(page.locator("#count")).to_have_text("46 components found")
            page.locator("#box").select_option(box["id"]); expect(page.locator("#count")).to_have_text("1 component found")
            expect(page.locator(".placements")).to_have_text("BOXA: 10")
            expect(page.locator(".description")).to_contain_text("<img src=x")
            assert page.evaluate("!window.injected"), "Inventory text executed as HTML"
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Catalog overflows viewport"
            Path("test-results").mkdir(exist_ok=True)
            page.screenshot(path=f"test-results/{'mobile' if mobile else 'desktop'}-catalog.png", full_page=True)
            count = len(requests); context.set_offline(True)
            page.locator("#box").select_option(""); page.locator("#search").fill("ESP32S3")
            expect(page.locator(".part h2")).to_have_text(["ESP32-S3"])
            assert len(requests) == count, "Search made a network request"
            page.locator("details").click(); expect(page.locator(".links a").first).to_have_attribute("href", "https://example.com/product")
            page.locator("#refresh").click(); expect(page.locator("#error")).to_contain_text("keep searching")
            page.locator("#search").fill("unrelatedxyz"); expect(page.locator("#count")).to_have_text("0 components found")
            assert not errors, errors
            print(f"Catalog {'mobile' if mobile else 'desktop'} fuzzy search, project paths, pagination and offline search: PASS")
        finally:
            context.close(); browser.close(); server.shutdown(); server.server_close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        run(playwright.chromium)
        run(playwright.webkit, mobile=True)
