import copy
import json
import unittest
from unittest.mock import patch
from kosuzu.http import RemoteError
from kosuzu.llm import EDITABLE, refine
from kosuzu.model import ValidationError
from kosuzu.suppliers import REGISTRY, check_domain, lookup, parse_page
from tests.helpers import ResponseTransport, part


class ImportTests(unittest.TestCase):
    def test_all_required_suppliers_are_registered(self):
        self.assertEqual(set(REGISTRY),{"digikey","mouser","arrow","lcsc"})

    def test_mouser_exact_code_and_image(self):
        p={"MouserPartNumber":"123-TEST","ManufacturerPartNumber":"R-10K","Manufacturer":"Example Components","Description":"Resistor 10K","ProductDetailUrl":"https://www.mouser.com/ProductDetail/test","ImagePath":"https://www.mouser.com/images/test.jpg","DataSheetUrl":"https://www.example.com/resistor.pdf","ProductAttributes":[{"AttributeName":"Resistance","AttributeValue":"10 kohm"}]}
        transport=ResponseTransport({"SearchResults":{"Parts":[p]}})
        result,evidence=lookup("mouser","123-TEST",{"api_key":"mouser-secret"},transport)
        self.assertEqual(result["mpn"],"R-10K"); self.assertTrue(result["image_url"].endswith("test.jpg")); self.assertIn("10 kohm",evidence)
        with self.assertRaises(ValidationError): lookup("mouser","not-an-exact-code",{"api_key":"mouser-secret"},transport)

    def test_mouser_ambiguous_results_are_rejected(self):
        transport=ResponseTransport({"SearchResults":{"Parts":[{"MouserPartNumber":"123-X"},{"MouserPartNumber":"123-X"}]}})
        with self.assertRaisesRegex(ValidationError,"unique exact match"): lookup("mouser","123-X",{"api_key":"key"},transport)

    def test_digikey_variation_code_oauth_and_image(self):
        transport=ResponseTransport({"Product":{"Manufacturer":{"Name":"Example"},"ManufacturerProductNumber":"R-10K","Description":{"ProductDescription":"Resistor"},"ProductUrl":"https://www.digikey.com/en/products/detail/test","PhotoUrl":"https://www.digikey.com/img.jpg","ProductVariations":[{"DigiKeyProductNumber":"123-TEST-ND"}],"Parameters":[{"ParameterText":"Resistance","ValueText":"10 kohm"}]}})
        result,_=lookup("digikey","123-TEST-ND",{"client_id":"id","client_secret":"secret","account_id":"123"},transport)
        self.assertEqual(result["mpn"],"R-10K"); self.assertTrue(result["image_url"])
        self.assertEqual(transport.calls[0][1]["headers"]["Authorization"],"Bearer oauth-token")
        self.assertEqual(transport.calls[0][1]["headers"]["X-DIGIKEY-Account-Id"],"123")

    def test_digikey_requires_account_id_for_two_legged_oauth(self):
        transport=ResponseTransport({})
        with self.assertRaisesRegex(ValidationError,"account ID"):
            lookup("digikey","123-TEST-ND",{"client_id":"id","client_secret":"secret"},transport)
        self.assertEqual(transport.calls,[])

    def test_arrow_exact_source_code_and_page_image(self):
        transport=ResponseTransport({"itemserviceresult":{"data":[{"PartList":[{"partNum":"R-10K","itemId":123,"manufacturer":{"mfrName":"Example"},"desc":"Resistor","resources":[{"type":"cloud_part_detail","uri":"https://www.arrow.com/en/products/r-10k/example"}],"InvOrg":{"webSites":[{"sources":[{"sourceParts":[{"sourcePartId":"AR-001"}]}]}]}}]}]}})
        with patch("kosuzu.suppliers.public_page",return_value=('<meta property="og:image" content="https://www.arrow.com/image.jpg">','https://www.arrow.com/en/products/r-10k/example')):
            result,_=lookup("arrow","AR-001",{"login":"user","api_key":"key"},transport)
        self.assertEqual(result["image_url"],"https://www.arrow.com/image.jpg"); self.assertEqual(result["mpn"],"R-10K")

    def test_lcsc_structured_product(self):
        html='<script type="application/ld+json">'+json.dumps({"@context":"https://schema.org","@type":"Product","sku":"C25804","mpn":"R-10K","brand":{"name":"Example"},"description":"10K resistor","image":["https://www.lcsc.com/test.jpg"],"additionalProperty":[{"name":"Resistance","value":"10 kohm"}]})+'</script>'
        with patch("kosuzu.suppliers.public_page",return_value=(html,'https://www.lcsc.com/product-detail/C25804.html')):
            result,_=lookup("lcsc","C25804")
        self.assertEqual(result["attributes"]["Resistance"],"10 kohm"); self.assertEqual(result["image_url"],"https://www.lcsc.com/test.jpg")

    def test_lcsc_visible_table_fallback(self):
        html='<meta property="og:image" content="https://www.lcsc.com/test.jpg"><div>LCSC Part #</div><div>C25804</div><div>Manufacturer</div><div>Example</div><div>MPN</div><div>R-10K</div><div>Key Attributes</div><div>10kΩ ±1% 0603</div><div>Packaging</div><div>0603</div>'
        result,_=parse_page(html,"lcsc","C25804","https://www.lcsc.com/product-detail/C25804.html")
        self.assertEqual(result["package"],"0603"); self.assertEqual(result["mpn"],"R-10K")

    def test_unrelated_product_page_never_imports(self):
        html='<script type="application/ld+json">{"@type":"Product","sku":"WRONG","mpn":"WRONG"}</script>'
        with self.assertRaisesRegex(ValidationError,"unambiguous product"): parse_page(html,"mouser","RIGHT","https://www.mouser.com/example")

    def test_product_urls_reject_ssrf_and_cross_supplier_redirects(self):
        for url in ["http://www.lcsc.com/example","https://127.0.0.1/test","https://www.lcsc.com.evil.test/","https://user@www.lcsc.com/test","https://www.lcsc.com:444/test"]:
            with self.subTest(url=url),self.assertRaises(ValidationError): check_domain(url,{"www.lcsc.com"})

    def llm_transport(self, changes=None, warnings=None, finish="stop"):
        candidate=part(); edits={key:copy.deepcopy(candidate[key]) for key in EDITABLE}; edits.update(changes or {})
        return ResponseTransport({"choices":[{"finish_reason":finish,"message":{"content":json.dumps({"component":edits,"warnings":warnings or []})}}]})

    def test_llm_normalization_keeps_provenance_and_requires_confirmation(self):
        candidate=part(); candidate.pop("id"); candidate.pop("review")
        transport=self.llm_transport({"description":"10 kohm ±1% resistor"})
        result=refine(candidate,"Distributor evidence",{"api_key":"llm-secret","model":"test-model"},transport)
        self.assertEqual(result["source_url"],candidate["source_url"]); self.assertEqual(result["image_url"],candidate["image_url"])
        self.assertFalse(result["review"]["confirmed"]); self.assertIn("datasheet",result["review"]["warnings"][-1])
        self.assertNotIn("llm-secret",json.dumps(transport.calls[0][0][2]))

    def test_llm_identity_changes_get_a_warning(self):
        result=refine(part(),"evidence",{"api_key":"key"},self.llm_transport({"mpn":"R-100K"}))
        self.assertIn("changed part identity",result["review"]["warnings"][0])

    def test_llm_cannot_replace_urls_or_review(self):
        transport=self.llm_transport({"source_url":"https://evil.example"})
        with self.assertRaises(ValidationError): refine(part(),"evidence",{"api_key":"key"},transport)

    def test_llm_truncated_or_invalid_output_is_rejected(self):
        with self.assertRaises(ValidationError): refine(part(),"evidence",{"api_key":"key"},self.llm_transport(finish="length"))
        with self.assertRaises(RemoteError): refine(part(),"evidence",{"api_key":"key"},ResponseTransport({"choices":[]}))
        with self.assertRaises(ValidationError): refine(part(),"evidence",{},self.llm_transport())
