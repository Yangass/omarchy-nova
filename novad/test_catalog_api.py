import copy
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import catalog
import catalog_observability as obs
import server

SEED = Path(__file__).resolve().parent.parent / 'catalog/models.json'

class Wire:
    def __init__(self, request): self.request=io.BytesIO(request);self.response=bytearray()
    def makefile(self,*_args,**_kwargs):return self.request
    def sendall(self,data):self.response.extend(data)

class CatalogApiTests(unittest.TestCase):
    def setUp(self):
        self.document=json.loads(SEED.read_text());self.catalog=catalog.validate_catalog(self.document)
        self.inventory={'provider':'ollama','status':'ready','source':'fixture','observed_at':None,'errors':[],
                        'records':[{'provider':'ollama','id':r['source']['upstream_id'],'installed':True,'digest':r['source']['revision_digest']} for r in self.catalog.records]}
    def request(self,path='/catalog/models',method='GET',loaded=None,inventory=None):
        wire=Wire(f'{method} {path} HTTP/1.0\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n'.encode())
        with patch.object(obs,'load_catalog',return_value=loaded or self.catalog),patch.object(obs,'local_inventory',return_value=inventory or self.inventory):
            server.Handler(wire,('127.0.0.1',1),None)
        headers,body=bytes(wire.response).split(b'\r\n\r\n',1)
        return int(headers.split()[1]),json.loads(body)
    def test_all_seed_records_exact_and_nulls_preserved(self):
        code,r=self.request();self.assertEqual(code,200);self.assertEqual(r['record_count'],len(self.document['models']))
        self.assertEqual(r['installed_match_counts']['exact'],len(self.document['models']))
        self.assertTrue(all(m['installed_match']['match_status']=='exact' for m in r['models']))
        self.assertTrue(all(m['capabilities']['coding'] is None for m in r['models']))
    def test_not_installed_fixture(self):
        self.inventory['records'].pop()
        row=self.request()[1]['models'][-1]['installed_match']
        self.assertEqual(row['match_status'],'not_installed');self.assertIs(row['installed'],False)
    def test_digest_mismatch_explained_without_mutation(self):
        self.inventory['records'][0]['digest']='different'
        before=copy.deepcopy((self.catalog,self.inventory))
        row=self.request()[1]['models'][0]['installed_match']
        self.assertEqual(row['match_status'],'revision_mismatch');self.assertTrue(row['installed'])
        self.assertEqual(row['installed_digest'],'different');self.assertNotEqual(row['catalog_digest'],'different')
        self.assertEqual((self.catalog,self.inventory),before)
    def test_ambiguous_inventory_never_guesses(self):
        self.inventory['records'].append(copy.deepcopy(self.inventory['records'][0]))
        row=self.request()[1]['models'][0]['installed_match']
        self.assertEqual(row['match_status'],'ambiguous');self.assertIsNone(row['installed_digest'])
    def test_missing_digest_unknown_not_exact(self):
        self.inventory['records'][0]['digest']=None
        row=self.request()[1]['models'][0]['installed_match']
        self.assertEqual(row['match_status'],'unknown');self.assertTrue(row['installed'])
    def test_incomplete_inventory_does_not_prove_absence(self):
        self.inventory['records']=[];self.inventory['status']='partial'
        self.assertEqual(self.request()[1]['installed_match_counts']['unknown'],len(self.document['models']))
    def test_other_provider_not_marked_absent(self):
        self.document['models'][0]['source']['provider']='future'
        c=catalog.validate_catalog(self.document)
        self.assertEqual(self.request(loaded=c)[1]['models'][0]['installed_match']['match_status'],'unknown')
    def test_malformed_record_preserves_siblings(self):
        self.document['models'].append({'bad':True})
        r=self.request(loaded=catalog.validate_catalog(self.document))[1]
        self.assertEqual(r['status'],'partial');self.assertEqual(r['record_count'],len(self.document['models'])-1);self.assertTrue(r['loader_errors'])
    def test_unsupported_version_safe(self):
        self.document['schema_version']=99
        r=self.request(loaded=catalog.validate_catalog(self.document))[1]
        self.assertEqual(r['status'],'invalid');self.assertEqual(r['models'],[]);self.assertTrue(r['loader_errors'])
    def test_missing_catalog_safe(self):
        r=self.request(loaded=catalog.Catalog('unavailable',None,(),('catalog file unavailable',)))[1]
        self.assertEqual(r['status'],'unavailable');self.assertEqual(r['record_count'],0)
    def test_summary_same_counts_without_models(self):
        summary=self.request('/catalog/status')[1];details=self.request()[1];del details['models']
        self.assertEqual(summary,details)
    def test_freshness_and_provenance_are_observations_only(self):
        self.document['models'][0]['metadata']['checked_at']='2000-01-01T00:00:00Z'
        c=catalog.validate_catalog(self.document);r=self.request(loaded=c)[1]
        self.assertEqual(r['models'][0]['freshness']['assessment'],'unknown')
        self.assertEqual(r['freshness']['oldest_record_checked_at'],'2000-01-01T00:00:00Z')
        self.assertEqual(r['models'][0]['metadata']['provenance'],self.document['models'][0]['metadata']['provenance'])
    def test_queries_and_mutations_rejected(self):
        self.assertEqual(self.request('/catalog/models?refresh=true')[0],400)
        for method in ('POST','PUT','PATCH','DELETE'):
            self.assertIn(self.request(method=method)[0],(404,405))

class LocalManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.manifest=self.root/'manifests/registry.ollama.ai/library/example/latest'
        self.manifest.parent.mkdir(parents=True);(self.root/'blobs').mkdir()
        blob=b'fixture weights';digest='sha256:'+hashlib.sha256(blob).hexdigest()
        (self.root/'blobs'/digest.replace(':','-')).write_bytes(blob)
        layer={'digest':digest,'size':len(blob)}
        self.raw=json.dumps({'schemaVersion':2,'config':layer,'layers':[layer]}).encode()
        self.manifest.write_bytes(self.raw)
    def tearDown(self):self.tmp.cleanup()
    def test_manifest_digest_and_exact_id(self):
        r=obs.local_inventory(self.root);self.assertEqual(r['status'],'ready')
        self.assertEqual(r['records'][0]['id'],'example:latest')
        self.assertEqual(r['records'][0]['digest'],hashlib.sha256(self.raw).hexdigest())
    def test_missing_blob_makes_unknown(self):
        for p in (self.root/'blobs').iterdir():p.unlink()
        r=obs.local_inventory(self.root)
        self.assertEqual(r['status'],'partial');self.assertIsNone(r['records'][0]['installed'])
    def test_malformed_manifest_and_missing_root(self):
        self.manifest.write_bytes(b'[]')
        self.assertEqual(obs.local_inventory(self.root)['status'],'partial')
        self.assertEqual(obs.local_inventory(self.root/'absent')['status'],'unavailable')
    def test_no_network_or_writes_through_real_catalog_handler(self):
        doc=json.loads(SEED.read_text());doc['models']=doc['models'][:1]
        doc['models'][0]['source']['upstream_id']='example:latest'
        doc['models'][0]['source']['revision_digest']=hashlib.sha256(self.raw).hexdigest()
        path=self.root/'catalog.json';path.write_text(json.dumps(doc))
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        active=(server.ACTIVE_PROVIDER,server.ACTIVE_MODEL,server.RUNTIME.snapshot())
        wire=Wire(b'GET /catalog/models HTTP/1.0\r\nHost: localhost\r\n\r\n')
        original=Path.open
        def readonly(p, mode='r', *args,**kwargs):
            self.assertEqual(mode,'rb');return original(p,mode,*args,**kwargs)
        with patch.object(obs,'DEFAULT_PATH',path),patch.object(obs,'local_inventory',side_effect=lambda:real_inventory(self.root)),patch.object(Path,'open',readonly),patch.object(socket,'socket',side_effect=AssertionError('outbound socket forbidden')),patch.object(urllib.request,'urlopen',side_effect=AssertionError('network forbidden')),patch.object(server,'save_config',side_effect=AssertionError('state mutation forbidden')),patch.object(server,'select_model',side_effect=AssertionError('switch forbidden')):
            server.Handler(wire,('127.0.0.1',1),None)
        result=json.loads(bytes(wire.response).split(b'\r\n\r\n',1)[1])
        self.assertEqual(result['installed_match_counts']['exact'],1)
        self.assertEqual(active,(server.ACTIVE_PROVIDER,server.ACTIVE_MODEL,server.RUNTIME.snapshot()))
        self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))
    def test_symlink_manifest_is_unknown(self):
        target=self.root/'other';self.manifest.rename(target);self.manifest.symlink_to(target)
        self.assertEqual(obs.local_inventory(self.root)['status'],'partial')

real_inventory=obs.local_inventory
if __name__=='__main__':unittest.main()
