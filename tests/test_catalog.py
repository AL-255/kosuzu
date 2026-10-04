import json
import tempfile
import unittest
from unittest.mock import patch
from kosuzu import catalog
from kosuzu.http import RemoteError
from kosuzu.model import ValidationError, apply_event, canonical, empty_inventory, new_event
from kosuzu.service import Service
from kosuzu.store import Store
from tests.helpers import FakeGitHub, part


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.gh = self.api.factory("private-token", "test/library")
        self.inventory = apply_event(empty_inventory(), new_event("create", part()["id"], 10, part(), note="private transaction note"))
        self.gh.commit_files(self.gh.head(), {"inventory.json": canonical(self.inventory)}, "Seed")

    def test_isolated_export_omits_history_and_reviews_preserves_stock(self):
        base = self.gh.head()
        result = catalog.publish(self.gh)
        self.assertTrue(result["changed"])
        self.assertEqual(base, self.gh.head())
        exported = self.api.files(self.api.refs[catalog.BRANCH])
        self.assertEqual(set(exported), {"index.html", "style.css", "app.js", "search.js", "data.json", ".nojekyll", "kosuzu-catalog.json"})
        data = json.loads(exported["data.json"])
        self.assertEqual(data["parts"][0]["quantity"], 10)
        self.assertEqual(data["parts"][0]["boxes"], {"": 10})
        self.assertNotIn("review", data["parts"][0])
        self.assertNotIn("receipts", data)
        self.assertNotIn("private transaction note", canonical(exported))
        self.assertNotIn("private-token", canonical(exported))
        self.assertEqual(self.api.page_site["source"], catalog.SOURCE)

    def test_unchanged_sync_is_idempotent_and_changes_advance_only_catalog(self):
        catalog.publish(self.gh)
        initial = self.api.refs[catalog.BRANCH]
        self.assertFalse(catalog.publish(self.gh)["changed"])
        self.assertEqual(initial, self.api.refs[catalog.BRANCH])
        updated = apply_event(self.inventory, new_event("adjust", part()["id"], -3))
        self.gh.commit_files(self.gh.head(), {"inventory.json": canonical(updated)}, "Take three")
        base = self.gh.head()
        catalog.publish(self.gh)
        data = self.gh.read("data.json", self.api.refs[catalog.BRANCH])
        self.assertEqual(data["parts"][0]["quantity"], 7)
        self.assertEqual(base, self.gh.head())
        self.assertTrue(self.api.ancestor(initial, self.api.refs[catalog.BRANCH]))

    def test_foreign_pages_site_and_branch_are_preserved(self):
        self.api.page_site = {"source": {"branch": "main", "path": "/docs"}, "html_url": "https://example.com"}
        with self.assertRaisesRegex(ValidationError, "different GitHub Pages"):
            catalog.publish(self.gh)
        self.assertNotIn(catalog.BRANCH, self.api.refs)
        self.api.page_site = None
        self.api.refs[catalog.BRANCH] = self.gh.head()
        base = self.api.refs[catalog.BRANCH]
        with self.assertRaisesRegex(ValidationError, "not owned"):
            catalog.publish(self.gh)
        self.assertEqual(self.api.refs[catalog.BRANCH], base)

    def test_recovery_after_lost_ref_or_site_create_response(self):
        for write in [("POST", "git/refs"), ("POST", "pages")]:
            with self.subTest(write=write):
                self.api.refs.pop(catalog.BRANCH, None); self.api.page_site = None
                self.api.fail_after = write
                with self.assertRaises(RemoteError): catalog.publish(self.gh)
                head = self.api.refs[catalog.BRANCH]
                result = catalog.publish(self.gh)
                self.assertEqual(self.api.refs[catalog.BRANCH], head)
                self.assertFalse(result["changed"])
                self.assertTrue(result["url"].startswith("https:"))

    def test_ref_race_retries_latest_database_without_forcing(self):
        catalog.publish(self.gh)
        updated = apply_event(self.inventory, new_event("adjust", part()["id"], 1))
        self.gh.commit_files(self.gh.head(), {"inventory.json": canonical(updated)}, "Put one")
        route = self.api.route
        raced = False
        def conflict(method, path, query, data):
            nonlocal raced
            if method == "PATCH" and path == "git/refs/heads/" + catalog.BRANCH and not raced:
                raced = True
                newest = apply_event(updated, new_event("adjust", part()["id"], 2))
                self.gh.commit_files(self.gh.head(), {"inventory.json": canonical(newest)}, "Put two")
                catalog.publish(self.gh)
            return route(method, path, query, data)
        with patch.object(self.api, "route", side_effect=conflict): catalog.publish(self.gh)
        self.assertTrue(raced)
        self.assertEqual(self.gh.read("data.json", self.api.refs[catalog.BRANCH])["parts"][0]["quantity"], 13)

    def test_contents_only_token_prepares_branch_without_changing_pages(self):
        route = self.api.route
        def restricted(method, path, query, data):
            if path == "pages": raise RemoteError("Forbidden", 403)
            return route(method, path, query, data)
        with patch.object(self.api, "route", side_effect=restricted):
            result = catalog.publish(self.gh)
            self.assertEqual(result["status"], "prepared")
            self.assertIn("Pages: read/write", result["warning"])
            self.assertIn(catalog.BRANCH, self.api.refs)
            self.assertFalse(catalog.publish(self.gh)["changed"])
        self.assertIsNone(self.api.page_site)

    def test_new_site_null_status_is_reported_as_building(self):
        route = self.api.route
        def initial_site(method, path, query, data):
            result = route(method, path, query, data)
            if method == "POST" and path == "pages": result["status"] = None
            return result
        with patch.object(self.api, "route", side_effect=initial_site):
            self.assertEqual(catalog.publish(self.gh)["status"], "building")

    def test_server_opt_in_permissions_failures_and_unpublish(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        store = Store(directory)
        self.addCleanup(store.db.close)
        service = Service(store, "server", self.api.factory)
        admin = service.session(service.login(store.setting("admin_key")))
        client = service.session(service.login(store.setting("client_key")))
        service.save_settings(admin, {"repo": "test/library", "server_token": "secret"})
        service.sync(); self.assertNotIn(catalog.BRANCH, self.api.refs)
        with self.assertRaises(ValidationError): service.save_settings(client, {"pages_enabled": True})
        with self.assertRaises(ValidationError): service.save_settings(admin, {"pages_enabled": "true"})
        with self.assertRaises(ValidationError): service.publish_pages(admin)
        service.save_settings(admin, {"pages_enabled": True})
        proposal = self.gh.submit(new_event("adjust", part()["id"], -1))
        with patch("kosuzu.catalog.publish", side_effect=RemoteError("Forbidden", 403)):
            result = service.sync()
        self.assertEqual(result["results"][0]["status"], "applied")
        self.assertIn("Pages: read/write", result["pages"]["error"])
        self.assertEqual(service.pages_status()["status"], "error")
        self.assertEqual(self.gh.inventory()["components"][part()["id"]]["quantity"], 9)
        with patch("kosuzu.catalog.publish", side_effect=RuntimeError("private upstream details")):
            result = service.sync()
        self.assertEqual(result["pages"]["status"], "error")
        self.assertNotIn("private upstream details", result["pages"]["error"])
        self.assertEqual(service.sync()["pages"]["status"], "building")
        with self.assertRaises(ValidationError): service.publish_pages(client)
        self.assertEqual(service.publish_pages(admin, remove=True)["status"], "unpublished")
        self.assertFalse(store.setting("pages_enabled"))
        self.assertIsNone(self.api.page_site)
        self.assertIn(catalog.BRANCH, self.api.refs)
        service.save_settings(admin, {"repo": "test/other"})
        self.assertEqual(service.pages_status(), {})
