import unittest
import base64
import json
import uuid
from kosuzu.http import RemoteError
from kosuzu.model import ValidationError, new_event, new_transfer, new_box_event
from tests.helpers import FakeGitHub, part


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.api=FakeGitHub(); self.gh=self.api.factory("token","test/library"); self.part=part()

    def create(self,quantity=50):
        event=new_event("create",self.part["id"],quantity,self.part)
        result=self.gh.submit(event); self.gh.merge(result["number"]); return event

    def test_initialize_is_safe_and_repeatable(self):
        api=FakeGitHub(False); gh=api.factory("token","test/library")
        self.assertTrue(gh.initialize()); self.assertFalse(gh.initialize()); self.assertEqual(gh.inventory()["revision"],0)

    def empty_repository(self, race=False):
        api=FakeGitHub(); api.refs.clear()
        original=api.route
        def route(method,path,query,data):
            if method=="GET" and not path: return {"default_branch":"main"}
            if method=="GET" and path.startswith("git/ref/") and not api.refs: raise RemoteError("Repository empty",409)
            if method=="PUT" and path=="contents/inventory.json":
                self.assertNotIn("sha",data)
                self.assertEqual(data["branch"],"main")
                inventory=json.loads(base64.b64decode(data["content"]))
                tree=api.new_tree({"inventory.json":json.dumps(inventory)})
                api.refs["main"]=api.new_commit(tree,[])
                if race: raise RemoteError("Concurrent initializer",422)
                return {"commit":{"sha":api.refs["main"]}}
            return original(method,path,query,data)
        api.route=route
        return api

    def test_empty_repository_initialization_creates_first_commit_once(self):
        api=self.empty_repository(); gh=api.factory("token","test/library")
        self.assertTrue(gh.initialize()); self.assertFalse(gh.initialize())
        self.assertEqual(gh.inventory()["revision"],0)
        self.assertEqual(len([c for c in api.calls if c[0]=="PUT"]),1)

    def test_empty_repository_initialization_race_verifies_winner(self):
        api=self.empty_repository(race=True); gh=api.factory("token","test/library")
        self.assertFalse(gh.initialize()); self.assertEqual(gh.inventory()["revision"],0)

    def test_empty_repository_wrong_branch_does_not_write(self):
        api=self.empty_repository(); gh=api.factory("token","test/library","other")
        with self.assertRaisesRegex(ValidationError,"default branch"):
            gh.initialize()
        self.assertFalse(any(c[0]=="PUT" for c in api.calls))

    def test_retried_submission_reuses_branch_and_pr(self):
        e=new_event("create",self.part["id"],3,self.part)
        first=self.gh.submit(e); second=self.gh.submit(e)
        self.assertEqual(first,second); self.assertEqual(len(self.api.prs),1)
        self.gh.merge(first["number"])
        self.assertEqual(self.gh.submit(e)["status"],"applied")

    def test_lost_pr_creation_response_is_recoverable(self):
        e=new_event("create",self.part["id"],3,self.part)
        self.api.fail_after=("POST","pulls")
        with self.assertRaises(RemoteError): self.gh.submit(e)
        self.assertEqual(self.gh.submit(e)["number"],1); self.assertEqual(len(self.api.prs),1)

    def test_concurrent_removals_only_one_can_overspend(self):
        self.create(50)
        a=self.gh.submit(new_event("adjust",self.part["id"],-40))
        b=self.gh.submit(new_event("adjust",self.part["id"],-40))
        self.gh.merge(a["number"])
        with self.assertRaisesRegex(ValidationError,"available 10"): self.gh.merge(b["number"])
        self.assertEqual(self.gh.inventory()["components"][self.part["id"]]["quantity"],10)
        self.assertEqual(self.api.prs[b["number"]]["state"],"open")

    def test_box_concurrent_removals_cannot_borrow_from_other_box(self):
        a={"id":uuid.uuid4().hex,"name":"BOXA","description":"","image_url":""}
        b={**a,"id":uuid.uuid4().hex,"name":"BOXB"}
        for box in (a,b):
            pr=self.gh.submit(new_box_event(box)); self.gh.merge(pr["number"])
        event=new_event("create",self.part["id"],10,self.part,box_id=a["id"])
        pr=self.gh.submit(event); self.gh.merge(pr["number"])
        pr=self.gh.submit(new_event("adjust",self.part["id"],20,box_id=b["id"])); self.gh.merge(pr["number"])
        first=self.gh.submit(new_event("adjust",self.part["id"],-7,box_id=a["id"]))
        second=self.gh.submit(new_event("adjust",self.part["id"],-7,box_id=a["id"]))
        self.api.before_update=lambda:self.gh.merge(first["number"])
        with self.assertRaisesRegex(ValidationError,"BOXA: available 3"):
            self.gh.merge(second["number"])
        row=self.gh.inventory()["components"][self.part["id"]]
        self.assertEqual(row["boxes"],{a["id"]:3,b["id"]:20})
        self.assertEqual(row["quantity"],23)

    def test_box_transfer_response_loss_never_moves_twice(self):
        self.create(50)
        box={"id":uuid.uuid4().hex,"name":"BOXA","description":"","image_url":""}
        pr=self.gh.submit(new_box_event(box)); self.gh.merge(pr["number"])
        event=new_transfer(self.part["id"],20,"",box["id"])
        pr=self.gh.submit(event)
        self.api.fail_after=("PATCH","git/refs/heads/main")
        with self.assertRaises(RemoteError): self.gh.merge(pr["number"])
        self.assertEqual(self.gh.submit(event)["status"],"applied")
        self.assertEqual(self.gh.inventory()["components"][self.part["id"]]["boxes"],{"":30,box["id"]:20})

    def test_concurrent_ref_change_revalidates_stock(self):
        self.create(50)
        a=self.gh.submit(new_event("adjust",self.part["id"],-40))
        b=self.gh.submit(new_event("adjust",self.part["id"],-30))
        self.api.before_update=lambda:self.gh.merge(b["number"])
        with self.assertRaisesRegex(ValidationError,"available 20"): self.gh.merge(a["number"])
        self.assertEqual(self.gh.inventory()["revision"],2)

    def test_concurrent_additions_retry_without_lost_stock(self):
        self.create(50)
        a=self.gh.submit(new_event("adjust",self.part["id"],10))
        b=self.gh.submit(new_event("adjust",self.part["id"],20))
        self.api.before_update=lambda:self.gh.merge(b["number"])
        self.gh.merge(a["number"])
        inventory=self.gh.inventory()
        self.assertEqual(inventory["components"][self.part["id"]]["quantity"],80)
        self.assertEqual(len(inventory["receipts"]),3)

    def test_merge_response_lost_never_duplicates_quantity(self):
        self.create(50); e=new_event("adjust",self.part["id"],10); pr=self.gh.submit(e)
        self.api.fail_after=("PATCH","git/refs/heads/main")
        with self.assertRaises(RemoteError): self.gh.merge(pr["number"])
        self.assertEqual(self.gh.submit(e)["status"],"applied")
        self.assertEqual(self.gh.inventory()["components"][self.part["id"]]["quantity"],60)

    def test_other_files_are_never_auto_merged(self):
        e=new_event("create",self.part["id"],3,self.part); pr=self.gh.submit(e)
        branch="kosuzu/"+e["id"]
        self.gh.commit_files(self.api.refs[branch],{"README.md":"malicious replacement"},"Other change",branch=branch)
        self.api.prs[pr["number"]]["head"]["sha"]=self.api.refs[branch]
        with self.assertRaisesRegex(ValidationError,"exactly one transaction"): self.gh.merge(pr["number"])
        self.assertEqual(self.gh.inventory()["revision"],0)

    def test_wrong_filename_fork_and_draft_rejected(self):
        for change in [{"draft":True},{"head":{"ref":"kosuzu/"+"0"*32,"repo":{"full_name":"untrusted/library"},"sha":"fake"}}]:
            with self.subTest(change=change):
                e=new_event("create",self.part["id"],3,self.part); pr=self.gh.submit(e)
                self.api.prs[pr["number"]].update(change)
                with self.assertRaises(ValidationError): self.gh.merge(pr["number"])

    def test_pagination_collects_all_proposals(self):
        for _ in range(101): self.gh.submit(new_event("create",self.part["id"],3,self.part))
        self.assertEqual(len(self.gh.proposals()),101)

    def test_rejected_proposal_is_not_reopened(self):
        e=new_event("create",self.part["id"],3,self.part); pr=self.gh.submit(e)
        self.gh.reject(pr["number"])
        self.assertEqual(self.gh.submit(e)["status"],"rejected"); self.assertEqual(len(self.api.prs),1)
