import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import catalog

SEED = Path(__file__).resolve().parent.parent / 'catalog/models.json'

class CatalogTests(unittest.TestCase):
    def setUp(self): self.document = json.loads(SEED.read_text())
    def test_valid_seed_loads(self):
        loaded=catalog.load_catalog(SEED)
        self.assertEqual(loaded.status,'ready'); self.assertEqual(len(loaded.records),len(self.document['models'])); self.assertFalse(loaded.errors)
    def test_missing_and_directory_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in (Path(directory),Path(directory)/'absent'):
                self.assertEqual(catalog.load_catalog(path).status,'unavailable')
    def test_invalid_json_encoding_duplicate_keys_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'test.json'
            for content in [b'{', b'\xff', b'{"schema_version":1,"schema_version":1}', b' '* (catalog.MAX_BYTES+1),b'['*2000]:
                p.write_bytes(content)
                self.assertEqual(catalog.load_catalog(p).status,'invalid')
    def test_unknown_versions_and_invalid_envelope(self):
        for version in (True,2,'1',None):
            self.document['schema_version']=version
            self.assertEqual(catalog.validate_catalog(self.document).status,'invalid')
        for envelope in (None,[],{}, {'schema_version':1,'models':{}}):
            self.assertEqual(catalog.validate_catalog(envelope).status,'invalid')
    def test_bad_record_isolated(self):
        self.document['models'].insert(0,{'catalog_id':'bad'})
        result=catalog.validate_catalog(self.document)
        self.assertEqual(result.status,'partial');self.assertEqual(len(result.records),len(self.document['models'])-1)
        self.assertTrue(result.errors[0].startswith('record[0]'))
    def test_null_capabilities_stay_unknown(self):
        result=catalog.load_catalog(SEED)
        for record in result.records:
            self.assertIsNone(record['capabilities']['coding'])
            self.assertIsNone(record['capabilities']['instruction_following'])
        self.assertIsNone(result.records[1]['capabilities']['vision'])
    def test_omitted_capabilities_normalized_to_null(self):
        self.document['models'][0]['capabilities']={}
        self.assertTrue(all(v is None for v in catalog.validate_catalog(self.document).records[0]['capabilities'].values()))
    def test_false_remains_false(self):
        self.document['models'][0]['capabilities']['coding']=False
        self.assertIs(catalog.validate_catalog(self.document).records[0]['capabilities']['coding'],False)
    def test_no_truthy_coercion(self):
        for bad in ('unknown','false',0,1,[],{}):
            self.document['models'][0]['capabilities']['coding']=bad
            self.assertEqual(len(catalog.validate_catalog(self.document).records),len(self.document['models'])-1)
    def test_invalid_properties_dates_and_runtime_fields(self):
        for field,value in [('parameter_count',True),('native_context',-1),('weight_size_bytes','10'),('quantizations','Q4_K_M')]:
            d=copy.deepcopy(self.document);d['models'][0]['properties'][field]=value
            self.assertEqual(catalog.validate_catalog(d).status,'partial')
        for key,value in [('release_date','2026-02-31'),('checked_at','yesterday')]:
            d=copy.deepcopy(self.document);d['models'][0]['metadata'][key]=value
            self.assertEqual(catalog.validate_catalog(d).status,'partial')
        for forbidden in ('installed','fit','active','score'):
            d=copy.deepcopy(self.document);d['models'][0][forbidden]=True
            self.assertEqual(catalog.validate_catalog(d).status,'partial')
    def test_duplicate_id_or_alias_rejects_all_collisions(self):
        for duplicate_id in (True,False):
            d=copy.deepcopy(self.document);duplicate=copy.deepcopy(d['models'][0])
            if duplicate_id:
                duplicate['source']['upstream_id']='other:tag'
                duplicate['acquisition']['artifact_id']='other:tag'
                duplicate['acquisition']['exact_tag']='tag'
            else: duplicate['catalog_id']='different'
            d['models'].append(duplicate)
            result=catalog.validate_catalog(d)
            self.assertEqual(len(result.records),len(self.document['models'])-1);self.assertEqual(len(result.errors),2)
    def test_exact_inventory_match_and_input_unchanged(self):
        c=catalog.load_catalog(SEED)
        inventory=[{'provider':'ollama','id':r['source']['upstream_id'],'installed':True} for r in c.records]
        original=copy.deepcopy(inventory);before=copy.deepcopy(c.records)
        matches=c.match_installed(inventory)
        self.assertEqual([r['catalog_id'] for r in matches],[r['catalog_id'] for r in c.records])
        self.assertTrue(all(r['revision_match'] is None for r in matches))
        self.assertEqual(inventory,original);self.assertEqual(c.records,before)
    def test_catalog_exists_without_installed_models(self):
        c=catalog.load_catalog(SEED)
        self.assertEqual(c.match_installed([]),[]);self.assertEqual(len(c.records),len(self.document['models']))
    def test_no_fuzzy_or_cross_provider_matching(self):
        c=catalog.load_catalog(SEED)
        inventory=[{'provider':'other','id':'qwen3:8b','installed':True}, {'provider':'ollama','id':'qwen3:8B','installed':True}, {'provider':'ollama','id':'qwen3:8b','installed':False}]
        self.assertEqual([r['catalog_id'] for r in c.match_installed(inventory)],[None,None])
    def test_revision_mismatch_is_visible(self):
        c=catalog.load_catalog(SEED);r=c.records[0]
        for digest,expected in [('changed',False),(r['source']['revision_digest'],True)]:
            matches=c.match_installed([{'provider':r['source']['provider'],'id':r['source']['upstream_id'],'installed':True,'digest':digest}])
            self.assertIs(matches[0]['revision_match'],expected)
    def test_detached_record_get(self):
        c=catalog.load_catalog(SEED);r=c.get(c.records[0]['catalog_id']);r['capabilities']['chat']=False
        self.assertTrue(c.records[0]['capabilities']['chat'])
    def test_load_never_opens_write_or_network(self):
        original=Path.open
        def readonly(path, mode='r', *args, **kwargs):
            self.assertEqual(mode,'rb');return original(path,mode,*args,**kwargs)
        with patch.object(Path,'open',readonly), patch.object(socket,'socket',side_effect=AssertionError('network forbidden')):
            self.assertEqual(catalog.load_catalog(SEED).status,'ready')
    def test_schema_validation_does_not_mutate_input(self):
        before=copy.deepcopy(self.document);catalog.validate_catalog(self.document)
        self.assertEqual(before,self.document)
    def test_minimum_provider_version_pins_eight_models_and_rejects_bad_values(self):
        expected={'qwen3.5-35b-q4-k-m':'0.30.0','qwen3.5-9b-q4-k-m':'0.30.0',
                  'gemma4-12b-q4-k-m':'0.30.9','qwen3.6-27b-q4-k-m':'0.30.0',
                  'gemma4-26b-q4-k-m':'0.30.9','qwen3.8-27b-q4-k-m':'0.32.12',
                  'qwen3.6-35b-q4-k-m':'0.30.0','gpt-oss-20b-mxfp4':'0.30.0'}  # gpt-oss: re-published 2026-10
        loaded=catalog.load_catalog(SEED)
        self.assertEqual(loaded.status,'ready');self.assertEqual(len(loaded.records),len(self.document['models']))
        for record in loaded.records:
            acquisition=record.get('acquisition')
            if record['catalog_id'] in expected:
                self.assertIsNotNone(acquisition)
                self.assertEqual(acquisition.get('minimum_provider_version'),expected[record['catalog_id']])
            else:
                self.assertNotIn('minimum_provider_version',acquisition or {})
        for bad in ('0.30','latest',30,''):
            d=copy.deepcopy(self.document);d['models'][0]['acquisition']['minimum_provider_version']=bad
            result=catalog.validate_catalog(d)
            self.assertEqual(result.status,'partial')
            self.assertEqual(len(result.records),len(d['models']))
            self.assertIsNone(result.records[0].get('acquisition'))
            self.assertTrue(any('minimum_provider_version' in error for error in result.errors))

if __name__=='__main__':unittest.main()
