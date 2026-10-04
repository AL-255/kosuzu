import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from kosuzu.model import ValidationError
from kosuzu.worksheet import Resolver, Search, SearchResults, apply, enrich, extract, read_csv, save_artifact, watch_pages
from tests.helpers import FakeGitHub, part, ResponseTransport


class WorksheetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / 'parts.csv'; self.checkpoint = self.root / 'parts.import.json'
        self.headers = ['Vendor', 'Invoice No', 'Distributor PN', 'MFR', 'MFR PN', 'Description', 'Purchase Date', 'MFR Date', 'Count']
        self.rows = [['LCSC', '00123', 'C25804', '', '', '', '2023-04-02', '', '10'],
                     ['LCSC', '00124', 'C25804', '', '', '', '', '', '2']]
        self.write(self.rows)

    def write(self, rows):
        with self.path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f); writer.writerow(self.headers); writer.writerows(rows)

    def prepared(self):
        resolver = Resolver(search=Search([]))
        with patch('kosuzu.worksheet.lookup', return_value=(part(), 'exact supplier evidence')):
            return enrich(self.path, self.path, self.checkpoint, 'test/library', 'main', 'box1', resolver, log=lambda *_: None)

    def test_fills_columns_preserves_purchase_data_and_checkpoint_has_no_credentials(self):
        original = self.path.read_bytes(); rows, state = self.prepared()
        self.assertEqual(rows[0]['MFR'], 'Example Components')
        self.assertEqual(rows[0]['MFR PN'], 'R-10K')
        self.assertEqual(rows[0]['Invoice No'], '00123')
        self.assertEqual(rows[0]['Purchase Date'], '2023-04-02')
        self.assertEqual(self.path.with_suffix('.original.csv').read_bytes(), original)
        self.assertIn('Source URL', rows[0])
        with patch('kosuzu.worksheet.lookup') as lookup:
            resumed, saved = enrich(self.path, self.path, self.checkpoint, 'test/library', 'main', 'box1', Resolver(), log=lambda *_: None)
        lookup.assert_not_called(); self.assertEqual(state, saved)
        self.assertEqual(resumed, rows)

    def test_import_merges_requests_aggregates_duplicate_rows_and_updates_pages_once(self):
        rows, state = self.prepared(); api = FakeGitHub(); gh = api.factory('memory-secret', 'test/library')
        inventory = apply(gh, rows, state, self.checkpoint, log=lambda *_: None)
        self.assertEqual(inventory['boxes'][state['box_id']]['name'], 'box1')
        self.assertEqual(inventory['components'][part()['id']]['boxes'], {state['box_id']: 12})
        self.assertEqual(inventory['revision'], 3)
        self.assertEqual(len(api.prs), 3)
        self.assertTrue(all(p['state'] == 'closed' for p in api.prs.values()))
        data = gh.read('data.json', api.refs['kosuzu-pages'])
        self.assertEqual(data['parts'][0]['boxes'], {state['box_id']: 12})
        self.assertNotIn('00123', json.dumps(data))
        self.assertNotIn('memory-secret', self.checkpoint.read_text())
        self.assertEqual(len([c for c in api.calls if c[:2] == ('POST', 'pages')]), 1)
        rerun = apply(gh, rows, state, self.checkpoint, log=lambda *_: None)
        self.assertEqual(rerun, inventory)
        self.assertEqual(len(api.prs), 3)

    def test_resume_partial_import_never_counts_first_row_twice(self):
        rows, state = self.prepared(); api = FakeGitHub(); gh = api.factory('memory-secret', 'test/library')
        original_submit = gh.submit; calls = 0
        def interrupted(event):
            nonlocal calls
            calls += 1
            if calls == 3: raise RuntimeError('Simulated process interruption')
            return original_submit(event)
        with patch.object(gh, 'submit', side_effect=interrupted):
            with self.assertRaises(RuntimeError): apply(gh, rows, state, self.checkpoint, log=lambda *_: None)
        saved = json.loads(self.checkpoint.read_text())
        inventory = apply(gh, rows, saved, self.checkpoint, log=lambda *_: None)
        self.assertEqual(inventory['components'][part()['id']]['quantity'], 12)
        self.assertEqual(inventory['revision'], 3)

    def test_changed_quantities_or_target_require_new_import_checkpoint(self):
        self.prepared()
        with self.path.open(newline='') as stream:
            reader = csv.DictReader(stream); headers = reader.fieldnames; rows = list(reader)
        rows[0]['Count'] = '11'
        with self.path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, headers); writer.writeheader(); writer.writerows(rows)
        with self.assertRaisesRegex(ValidationError, 'changed'):
            enrich(self.path, self.path, self.checkpoint, 'test/library', 'main', 'box1', Resolver())

    def test_unresolved_dry_run_retries_automatically_when_source_becomes_available(self):
        resolver = Resolver(search=Search([]))
        with patch('kosuzu.worksheet.lookup', side_effect=ValidationError('Supplier unavailable')):
            rows, state = enrich(self.path, self.path, self.checkpoint, 'test/library', 'main', 'box1', resolver, log=lambda *_: None)
        self.assertEqual(state['rows']['0']['status'], 'unresolved')
        with patch('kosuzu.worksheet.lookup', return_value=(part(), 'supplier evidence')) as lookup:
            rows, state = enrich(self.path, self.path, self.checkpoint, 'test/library', 'main', 'box1', resolver, log=lambda *_: None)
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(state['rows']['0']['status'], 'verified')
        self.assertEqual(rows[0]['MFR PN'], 'R-10K')

    def test_unresolved_rows_are_explicit_and_generic_label_does_not_invent_manufacturer(self):
        generic = dict(zip(self.headers, ['Generic', '', '', '', '', '2CL72 10KV 5mA 100nS High Voltage Diode', '', '', '11']))
        value = Resolver(search=Search([])).resolve(generic)
        self.assertEqual(value['status'], 'unresolved')
        self.assertEqual(value['component']['manufacturer'], 'Unknown (unverified)')
        self.assertEqual(value['component']['mpn'], '2CL72')
        item = dict(zip(self.headers, ['Adafruit', '', 'W16586-A', '', '', '', '', '', '1']))
        with patch('kosuzu.worksheet.lookup', side_effect=ValidationError('Invalid code')):
            value = Resolver(search=Search([])).resolve(item)
        self.assertEqual(value['component']['mpn'], 'UNVERIFIED-W16586-A')
        self.assertIn('unresolved', value['warnings'][-1])

    def test_search_requires_exact_supplier_identifier_and_original_supplier_domain(self):
        html = '<article class="result"><a href="https://www.mouser.com/ProductDetail/test">R-10K Example</a><p>123-R10K precision resistor</p></article>'
        html += '<article class="result"><a href="https://www.mouser.com.evil.test/test">123-R10K Wrong</a></article>'
        parser = SearchResults(); parser.feed(html); self.assertEqual(len(parser.rows), 2)
        search = Search(['https://search.example/search'], ResponseTransport(html))
        self.assertEqual(len(search.find('mouser', '123-R10K')), 1)
        self.assertEqual(search.find('mouser', '456-WRONG'), [])

    def test_llm_cannot_invent_identity_or_replace_source(self):
        source = {'url': 'https://www.mouser.com/ProductDetail/example', 'content': '123-R10K Example Components R-10K Precision resistor'}
        def result(mpn):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'source': 0, 'manufacturer': 'Example Components', 'mpn': mpn, 'description': 'Precision resistor'})}}]}
        value = extract('mouser', '123-R10K', [source], {'api_key': 'private'}, ResponseTransport(result('R-10K')))
        self.assertEqual(value['source_url'], source['url'])
        with self.assertRaises(ValidationError):
            extract('mouser', '123-R10K', [source], {'api_key': 'private'}, ResponseTransport(result('INVENTED')))
        with self.assertRaises(ValidationError):
            extract('mouser', '456-WRONG', [source], {'api_key': 'private'}, ResponseTransport(result('R-10K')))

    def test_public_page_verification_checks_all_imported_box_counts(self):
        rows, state = self.prepared(); api = FakeGitHub(); gh = api.factory('memory-secret', 'test/library')
        inventory = apply(gh, rows, state, self.checkpoint, log=lambda *_: None)
        data = gh.read('data.json', api.refs['kosuzu-pages'])
        url = watch_pages(gh, inventory, state, transport=ResponseTransport(data), log=lambda *_: None)
        self.assertEqual(url, 'https://test.github.io/library/')

    def test_bad_counts_fail_before_any_lookup_or_write(self):
        for invalid in ['0', '-1', '1.5', '1000000001']:
            self.rows[0][-1] = invalid; self.write(self.rows)
            with self.assertRaises(ValidationError): read_csv(self.path)

    def test_csv_artifact_is_exact_idempotent_and_excluded_from_public_catalog(self):
        rows, state = self.prepared(); api = FakeGitHub(); gh = api.factory('memory-secret', 'test/library')
        inventory = apply(gh, rows, state, self.checkpoint, log=lambda *_: None, artifact_file=self.path)
        self.assertEqual(api.files(gh.head())['artifacts/parts.csv'].encode(), self.path.read_bytes())
        self.assertNotIn('artifacts/parts.csv', api.files(api.refs['kosuzu-pages']))
        base = gh.head()
        self.assertFalse(save_artifact(gh, self.path)['changed'])
        self.assertEqual(gh.head(), base)
        self.assertEqual(gh.inventory(), inventory)
        for directory in ['/tmp', '../private', 'a/../b', 'a//b', 'a\\b']:
            with self.assertRaises(ValidationError): save_artifact(gh, self.path, directory)


if __name__ == '__main__':
    unittest.main()
