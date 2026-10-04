import copy
import hashlib
import uuid
import unittest
from kosuzu.model import ValidationError, apply_event, canonical, empty_inventory, new_event, new_transfer, new_box_event, validate_event, validate_inventory
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

    def make_boxes(self):
        a={"id":uuid.uuid4().hex,"name":"BOXA","description":"Resistors","image_url":"https://example.com/box.jpg"}
        b={**a,"id":uuid.uuid4().hex,"name":"BOXB"}
        inventory=apply_event(self.inventory,new_box_event(a))
        inventory=apply_event(inventory,new_box_event(b))
        return inventory,a,b

    def test_box_creation_edit_duplicate_names_and_stale_edits(self):
        inventory,a,b=self.make_boxes()
        self.assertEqual(inventory["boxes"][a["id"]]["image_url"],a["image_url"])
        edit=new_box_event({**a,"description":"Shelf 2"},a)
        changed=apply_event(inventory,edit)
        self.assertEqual(changed,apply_event(changed,edit))
        with self.assertRaisesRegex(ValidationError,"changed since"):
            apply_event(changed,new_box_event({**a,"name":"Changed"},a))
        with self.assertRaisesRegex(ValidationError,"name already exists"):
            apply_event(inventory,new_box_event({**a,"id":uuid.uuid4().hex,"name":" boxb "}))
        with self.assertRaises(ValidationError): new_box_event({**a,"image_url":"file:///private"})

    def test_box_allocations_and_transfer_preserve_total_and_replay(self):
        inventory,a,b=self.make_boxes()
        event=new_transfer(self.part["id"],10,"",a["id"])
        inventory=apply_event(inventory,event)
        self.assertEqual(inventory,apply_event(inventory,event))
        inventory=apply_event(inventory,new_transfer(self.part["id"],20,"",b["id"]))
        row=inventory["components"][self.part["id"]]
        self.assertEqual(row["quantity"],50)
        self.assertEqual(row["boxes"],{"":20,a["id"]:10,b["id"]:20})
        inventory=apply_event(inventory,new_event("adjust",self.part["id"],-5,box_id=a["id"]))
        self.assertEqual(inventory["components"][self.part["id"]]["quantity"],45)
        self.assertEqual(inventory["components"][self.part["id"]]["boxes"][b["id"]],20)

    def test_multiple_boxes_require_choice_even_for_legacy_events(self):
        inventory,a,b=self.make_boxes()
        inventory=apply_event(inventory,new_transfer(self.part["id"],10,"",a["id"]))
        for delta in (1,-1):
            for schema in (1,2):
                event=new_event("adjust",self.part["id"],delta); event["schema"]=schema
                with self.assertRaisesRegex(ValidationError,"multiple boxes"):
                    apply_event(inventory,event)

    def test_box_specific_overspend_and_invalid_transfers_do_not_change_input(self):
        inventory,a,b=self.make_boxes()
        inventory=apply_event(inventory,new_transfer(self.part["id"],10,"",a["id"]))
        before=copy.deepcopy(inventory)
        for event in (new_event("adjust",self.part["id"],-11,box_id=a["id"]),new_transfer(self.part["id"],11,a["id"],b["id"])):
            with self.assertRaisesRegex(ValidationError,"BOXA: available 10"):
                apply_event(inventory,event)
        self.assertEqual(inventory,before)
        with self.assertRaises(ValidationError): new_transfer(self.part["id"],2,a["id"],a["id"])
        with self.assertRaises(ValidationError): apply_event(inventory,new_event("adjust",self.part["id"],1,box_id=uuid.uuid4().hex))

    def test_legacy_snapshot_upgrade_preserves_stock_and_old_receipts(self):
        event={**self.create,"schema":1}
        legacy={"schema":1,"revision":1,"components":{self.part["id"]:{"component":self.part,"quantity":50}},"receipts":{event["id"]:hashlib.sha256(canonical(event).encode()).hexdigest()}}
        upgraded=validate_inventory(legacy)
        self.assertEqual(upgraded["schema"],2)
        self.assertEqual(upgraded["components"][self.part["id"]]["boxes"],{"":50})
        self.assertEqual(upgraded,apply_event(legacy,event))
        self.assertNotIn("boxes",legacy)

    def test_snapshot_cannot_have_missing_boxes_or_mismatched_totals(self):
        for allocations in ({"":49},{uuid.uuid4().hex:50},{"":True},{}):
            inventory=copy.deepcopy(self.inventory)
            inventory["components"][self.part["id"]]["boxes"]=allocations
            with self.assertRaises(ValidationError): validate_inventory(inventory)

    def test_initial_stock_can_go_directly_into_a_box(self):
        inventory,a,b=self.make_boxes(); another=part("OTHER")
        result=apply_event(inventory,new_event("create",another["id"],30,another,box_id=a["id"]))
        self.assertEqual(result["components"][another["id"]]["boxes"],{a["id"]:30})
