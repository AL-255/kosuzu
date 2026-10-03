import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from kosuzu.model import ValidationError, new_event
from kosuzu.service import Service
from kosuzu.store import Store
from tests.helpers import FakeGitHub, part


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=Store(self.temp.name)
        self.addCleanup(self.temp.cleanup); self.addCleanup(self.store.db.close)
        self.api=FakeGitHub(); self.service=Service(self.store,"server",self.api.factory)
        self.admin=self.service.session(self.service.login(self.store.setting("admin_key")))
        self.client=self.service.session(self.service.login(self.store.setting("client_key")))
        self.service.save_settings(self.admin,{"repo":"test/library","server_token":"server-secret","client_token":"client-secret","llm":{"api_key":"llm-secret"},"suppliers":{"arrow":{"login":"user","api_key":"arrow-secret"}}})
        self.service.save_settings(self.client,{"client_token":"other-client-secret"})

    def test_settings_redact_secrets_and_roles_enforced(self):
        public=json.dumps(self.service.public_settings(self.admin))
        for secret in ["server-secret","client-secret","llm-secret","arrow-secret","other-client-secret"]: self.assertNotIn(secret,public)
        with self.assertRaisesRegex(ValidationError,"Administrator"): self.service.save_settings(self.client,{"server_token":"bad"})
        self.assertEqual(self.store.setting("github_token"),"server-secret")

    def test_sign_in_restores_profile_and_outbox_identity_after_session_expiry(self):
        token=self.service.login(self.store.setting("client_key"))
        user=self.service.session(token)
        self.service.save_settings(user,{"client_token":"saved-token"})
        p=self.seed(); event=new_event("adjust",p["id"],1)
        self.api.offline=True; self.service.submit(user,event); self.api.offline=False
        self.store.execute("UPDATE sessions SET expires=0 WHERE id=?",(user["id"],))
        self.assertIsNone(self.service.session(token))
        restored=self.service.session(self.service.login(self.store.setting("client_key"),token))
        self.assertEqual(restored["id"],user["id"])
        self.assertTrue(self.service.public_settings(restored)["client_token_saved"])
        self.service.flush(restored); self.service.sync()
        self.assertEqual(self.service.github().inventory()["components"][p["id"]]["quantity"],51)

    def test_partial_supplier_credentials_keep_other_saved_fields(self):
        self.service.save_settings(self.admin,{"suppliers":{"arrow":{"api_key":"replacement"}}})
        self.assertEqual(self.store.profile(self.admin["id"])["suppliers"]["arrow"],{"login":"user","api_key":"replacement"})

    def test_unapproved_llm_endpoint_rejected_before_credentials_saved(self):
        with self.assertRaises(ValidationError): self.service.save_settings(self.admin,{"llm":{"api_key":"changed","base_url":"https://evil.example"}})
        self.assertEqual(self.store.profile(self.admin["id"])["llm"]["api_key"],"llm-secret")

    def test_import_cannot_save_without_real_server_review_draft(self):
        with self.assertRaisesRegex(ValidationError,"expired"): self.service.create(self.admin,{"draft_id":"made-up","confirmed":True,"quantity":5})
        candidate=part(); candidate["review"]["confirmed"]=False
        draft=self.store.draft(self.admin["id"],candidate)
        with self.assertRaisesRegex(ValidationError,"Confirm"): self.service.create(self.admin,{"draft_id":draft,"confirmed":False,"quantity":5})
        with self.assertRaisesRegex(ValidationError,"expired"): self.service.create(self.client,{"draft_id":draft,"confirmed":True,"quantity":5})

    def test_import_and_stock_change_full_service_lifecycle(self):
        candidate=part(); candidate["review"]["confirmed"]=False
        with patch("kosuzu.service.lookup",return_value=(candidate,"evidence")),patch("kosuzu.service.refine",return_value=candidate):
            reviewed=self.service.import_part(self.admin,{"supplier":"lcsc","code":"C25804"})
        data={"draft_id":reviewed["draft_id"],"confirmed":True,"quantity":50,"edits":{"location":"A2"}}
        result=self.service.create(self.admin,data); retried=self.service.create(self.admin,data)
        self.assertEqual(result["event_id"],retried["event_id"])
        self.service.sync()
        event=new_event("adjust",candidate["id"],-40)
        request={"request_id":event["id"],"component_id":candidate["id"],"delta":-40}
        self.service.adjust(self.client,request); self.service.adjust(self.client,request)
        self.service.sync(); self.service.flush(self.client)
        self.assertEqual(self.service.inventory(self.client)["inventory"]["components"][candidate["id"]]["quantity"],10)
        self.assertEqual(self.store.outbox(self.client["id"])[0]["result"]["status"],"applied")
        self.assertEqual(len(self.api.prs),2)

    def seed(self):
        p=part(); gh=self.service.github(self.admin["id"]); pr=gh.submit(new_event("create",p["id"],50,p)); self.service.sync(); return p

    def test_error_queue_survives_restart_and_prompts_fix(self):
        p=self.seed(); self.service.submit(self.client,new_event("adjust",p["id"],-60)); self.service.sync()
        errors=self.store.errors("test/library"); self.assertEqual(len(errors),1); self.assertIn("Submit a smaller removal",errors[0]["message"])
        restarted=Store(self.temp.name); self.addCleanup(restarted.db.close)
        self.assertEqual(restarted.errors("test/library")[0]["status"],"open")
        with self.assertRaises(ValidationError): self.service.queue_action(self.client,{"action":"reject","number":2})
        self.service.queue_action(self.admin,{"action":"reject","number":2})
        self.assertEqual(self.store.errors("test/library")[0]["status"],"resolved")
        self.assertEqual(self.api.prs[2]["state"],"closed")

    def test_independent_clients_receive_actionable_failure_status(self):
        p=self.seed(); self.service.submit(self.client,new_event("adjust",p["id"],-60)); self.service.sync()
        self.service.flush(self.client)
        result=self.store.outbox(self.client["id"])[0]["result"]
        self.assertEqual(result["status"],"blocked")
        self.assertIn("available 50",result["error"])
        self.assertIn("smaller removal",result["error"])

    def test_applied_requests_keep_their_github_links(self):
        p=self.seed(); self.service.submit(self.client,new_event("adjust",p["id"],1)); self.service.sync(); self.service.flush(self.client)
        result=self.store.outbox(self.client["id"])[0]["result"]
        self.assertEqual(result["status"],"applied")
        self.assertEqual(result["number"],2)
        self.assertTrue(result["url"].endswith("/pull/2"))

    def test_status_permission_failure_is_queued_without_repeating_delta(self):
        p=self.seed(); self.service.submit(self.client,new_event("adjust",p["id"],1))
        original=self.api.request
        from kosuzu.http import RemoteError
        def no_status(method,url,*args,**kwargs):
            if method=="POST" and "/statuses/" in url: raise RemoteError("Access denied",403)
            return original(method,url,*args,**kwargs)
        self.api.request=no_status; self.service.sync()
        self.assertEqual(self.service.github().inventory()["components"][p["id"]]["quantity"],51)
        self.assertIn("Commit statuses: write",next(e for e in self.store.errors("test/library") if e["status"]=="open")["message"])
        self.api.request=original; self.service.sync()
        self.assertTrue(all(e["status"]=="resolved" for e in self.store.errors("test/library")))
        self.assertEqual(self.service.github().inventory()["components"][p["id"]]["quantity"],51)

    def test_network_outage_keeps_outbox_and_safe_retry(self):
        p=self.seed(); event=new_event("adjust",p["id"],5); self.api.offline=True
        result=self.service.submit(self.client,event); self.assertEqual(result["status"],"queued")
        self.api.offline=False; self.service.flush(self.client); self.service.sync(); self.service.flush(self.client)
        self.assertEqual(self.store.outbox(self.client["id"])[0]["result"]["status"],"applied")
        self.assertEqual(self.service.github().inventory()["components"][p["id"]]["quantity"],55)

    def test_unexpected_upstream_error_preserves_outbox_for_later_retry(self):
        p=self.seed(); e=new_event("adjust",p["id"],2)
        self.api.offline=True; self.service.submit(self.client,e); self.api.offline=False
        with patch("kosuzu.github.GitHub.submit",side_effect=RuntimeError("bad response")):
            self.service.flush(self.client)
        result=self.store.outbox(self.client["id"])[0]["result"]
        self.assertEqual(result["status"],"queued"); self.assertIn("retained for retry",result["error"])
        self.service.flush(self.client); self.service.sync(); self.service.flush(self.client)
        self.assertEqual(self.service.github().inventory()["components"][p["id"]]["quantity"],52)

    def test_outbox_stays_pinned_to_original_repo(self):
        p=self.seed(); e=new_event("adjust",p["id"],1); self.api.offline=True; self.service.submit(self.client,e); self.api.offline=False
        self.service.save_settings(self.admin,{"repo":"test/another"}); before=len(self.api.calls); self.service.flush(self.client)
        self.assertEqual(len(self.api.calls),before)
        self.assertEqual(self.store.outbox(self.client["id"])[0]["repo"],"test/library")

    def test_cached_inventory_marks_itself_stale(self):
        self.seed(); self.service.inventory(self.client); self.api.offline=True
        self.assertTrue(self.service.inventory(self.client)["stale"])

    def test_failed_sync_enters_persistent_error_queue(self):
        self.api.offline=True
        with self.assertRaises(Exception): self.service.sync()
        self.assertEqual(self.store.errors("test/library")[0]["number"],None)
        self.api.offline=False; self.service.sync(); self.assertEqual(self.store.errors("test/library")[0]["status"],"resolved")

    @unittest.skipIf(os.name=="nt","POSIX permissions only; Windows uses user profile ACLs")
    def test_private_state_permissions(self):
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.store.directory.stat().st_mode & 0o777,0o700)
