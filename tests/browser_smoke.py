"""End-to-end UI regression in Chromium and iPhone-sized WebKit.

Uses the real HTTP service and Git DAG adapter; supplier/LLM responses are
deterministic fixtures so the test needs no distributor or LLM credentials.
"""
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect
from kosuzu.model import new_event
from kosuzu.server import make_server
from kosuzu.service import Service
from kosuzu.store import Store
from tests.helpers import FakeGitHub, part


def run(browser_type, mobile=False):
    with tempfile.TemporaryDirectory() as directory:
        store=Store(directory); upstream=FakeGitHub()
        service=Service(store,"server",upstream.factory)
        server=make_server(service,"127.0.0.1",0)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        browser=browser_type.launch()
        context=browser.new_context(viewport={"width":390,"height":844} if mobile else {"width":1440,"height":1000},is_mobile=mobile,has_touch=mobile)
        page=context.new_page(); errors=[]; page.on("pageerror",lambda error:errors.append(str(error)))
        candidate=part(); candidate["package"]="0603"; candidate["category"]="Resistors"; candidate["review"]["confirmed"]=False
        try:
            page.goto(server.public_url)
            expect(page.locator("#login-form")).to_be_visible()
            page.locator("#access-key").fill(store.setting("admin_key")); page.locator("#login-form button").click()
            expect(page.locator("#view-settings")).to_be_visible()
            page.locator("#setting-repo").fill("test/library")
            page.locator("#client-token").fill("client-test-token"); page.locator("#server-token").fill("server-test-token")
            page.locator("#llm-key").fill("llm-test-key"); page.locator("#llm-model").fill("test-model")
            page.get_by_role("button",name="Save settings",exact=True).click()
            expect(page.locator("#settings-saved")).to_have_text("Saved.")
            expect(page.locator("#client-token")).to_have_value("")
            page.locator('[data-view="import"]').click()
            page.locator("#supplier").select_option("lcsc"); page.locator("#supplier-code").fill("C25804")
            with patch("kosuzu.service.lookup",return_value=(candidate,"fixture supplier evidence")),patch("kosuzu.service.refine",return_value=candidate):
                page.locator("#lookup-button").click()
                expect(page.locator("#review-panel")).to_be_visible()
            page.locator("#part-location").fill("A2 / Drawer 6"); page.locator("#part-quantity").fill("50")
            page.locator("#part-confirmed").check(); page.locator('#review-form button[type="submit"]').click()
            expect(page.locator("#view-requests")).to_be_visible()
            expect(page.locator("#request-list")).to_contain_text("pending")
            page.locator('[data-view="queue"]').click(); page.locator("#sync-server").click()
            expect(page.locator("#sync-info")).to_contain_text("Last completed sync")
            page.locator('[data-view="inventory"]').click(); page.locator("#refresh").click()
            expect(page.locator("#inventory-rows")).to_contain_text("R-10K")
            expect(page.locator("#stat-pieces")).to_have_text("50")
            page.locator("#search").fill("A2"); expect(page.locator("#result-count")).to_have_text("1 of 1 components")
            page.locator("#search").fill("no-such-part"); expect(page.locator("#inventory-empty")).to_be_visible()
            page.locator("#search").fill("")
            page.get_by_role("button",name="Adjust stock",exact=True).click()
            page.locator("#stock-action").select_option("remove"); page.locator("#stock-quantity").fill("40")
            page.locator('#stock-form button[type="submit"]').click()
            expect(page.locator("#view-requests")).to_be_visible()
            page.locator('[data-view="queue"]').click(); page.locator("#sync-server").click()
            page.locator('[data-view="inventory"]').click(); page.locator("#refresh").click()
            expect(page.locator("#stat-pieces")).to_have_text("10")
            page.get_by_role("button",name="Adjust stock",exact=True).click()
            page.locator("#stock-action").select_option("remove"); page.locator("#stock-quantity").fill("20")
            page.locator('#stock-form button[type="submit"]').click()
            expect(page.locator("#view-requests")).to_be_visible()
            page.locator('[data-view="queue"]').click(); page.locator("#sync-server").click()
            expect(page.locator("#error-list")).to_contain_text("Insufficient stock")
            expect(page.locator("#queue-count")).to_have_text("1")
            page.once("dialog",lambda dialog:dialog.accept())
            page.get_by_role("button",name="Reject request",exact=True).click()
            expect(page.locator("#error-list .status")).to_have_text("resolved")
            page.locator('[data-view="inventory"]').click()
            page.locator("#refresh").click(); expect(page.locator("#stat-pieces")).to_have_text("10")
            width=page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert width, "Page has horizontal overflow"
            Path("test-results").mkdir(exist_ok=True)
            page.screenshot(path=f"test-results/{'mobile' if mobile else 'desktop'}-inventory.png",full_page=True)
            manifest=page.request.get(server.public_url+"/manifest.webmanifest").json()
            assert manifest["display"]=="standalone"
            assert not errors,errors
            print(f"PASS {browser_type.name} {'mobile' if mobile else 'desktop'}: import, confirmation, stock adjustments, queue rejection, responsive layout")
        finally:
            context.close(); browser.close(); server.shutdown(); server.server_close(); store.db.close()


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument("--chromium-only",action="store_true"); args=parser.parse_args()
    with sync_playwright() as playwright:
        run(playwright.chromium)
        if not args.chromium_only: run(playwright.webkit,mobile=True)
