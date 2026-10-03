import copy
import unittest
from kosuzu.model import ValidationError, apply_event, canonical, empty_inventory, new_event, validate_event, validate_inventory
from tests.helpers import part


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.part=part(); self.create=new_event("create",self.part["id"],50,self.part)
        self.inventory=apply_event(empty_inventory(),self.create)

    def test_create_adjust_and_idempotent_replay(self):
        event=new_event("adjust",self.part["id"],-10)
        result=apply_event(self.inventory,event)
        self.assertEqual(result["components"][self.part["id"]]["quantity"],40)
        self.assertEqual(result,apply_event(result,event))
        self.assertEqual(self.inventory["components"][self.part["id"]]["quantity"],50)

    def test_insufficient_stock_never_changes_input(self):
        before=copy.deepcopy(self.inventory)
        with self.assertRaisesRegex(ValidationError,"available 50"):
            apply_event(self.inventory,new_event("adjust",self.part["id"],-51))
        self.assertEqual(before,self.inventory)

    def test_duplicate_part_requires_stock_adjustment(self):
        with self.assertRaisesRegex(ValidationError,"already exists"):
            apply_event(self.inventory,new_event("create",self.part["id"],5,self.part))

    def test_reused_id_different_content_rejected(self):
        changed={**self.create,"delta":12}
        with self.assertRaisesRegex(ValidationError,"different content"): apply_event(self.inventory,changed)

    def test_unknown_part_rejected(self):
        with self.assertRaisesRegex(ValidationError,"does not exist"):
            apply_event(empty_inventory(),new_event("adjust",self.part["id"],4))

    def test_invalid_quantities(self):
        for value in [True,False,1.2,"4",0,None,1000000001]:
            with self.subTest(value=value),self.assertRaises(ValidationError): new_event("adjust",self.part["id"],value)

    def test_create_needs_positive_stock(self):
        with self.assertRaises(ValidationError): new_event("create",self.part["id"],-1,self.part)

    def test_metadata_cannot_change_in_adjustment(self):
        event=new_event("adjust",self.part["id"],1); event["component"]=self.part
        with self.assertRaises(ValidationError): validate_event(event)

    def test_identity_and_review_required(self):
        for change in [{"id":"0"*24},{"review":{}},{"image_url":"javascript:alert(1)"},{"review":{**self.part["review"],"confirmed":False}}]:
            with self.subTest(change=change),self.assertRaises(ValidationError): new_event("create",self.part["id"],1,{**self.part,**change})

    def test_corrupt_snapshot_rejected(self):
        self.inventory["components"][self.part["id"]]["quantity"]=-1
        with self.assertRaises(ValidationError): validate_inventory(self.inventory)

    def test_canonical_json_order_is_stable(self):
        self.assertEqual(canonical({"b":1,"a":"Ω"}),canonical({"a":"Ω","b":1}))
