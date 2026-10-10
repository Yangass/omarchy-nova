"""Exercise real HTTP handlers with an offline provider, no live service disruption."""
import json
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import server
from providers import ProviderUnavailable
from test_model_fit import profile

class Offline:
    def available(self): return False
    def fit_models(self): raise ProviderUnavailable()
    def running_models(self): raise ProviderUnavailable()

class ApiTests(unittest.TestCase):
    def test_read_only_partial_endpoints_while_offline(self):
        with patch.object(server,'PROVIDERS',{'ollama':Offline()}), patch.object(server,'collect_profile',side_effect=lambda available:dict(profile(),local_ai={'available':available})):
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True); thread.start()
            try:
                base='http://127.0.0.1:'+str(http.server_port)
                for path in ['/hardware/profile','/models/fit']:
                    with urllib.request.urlopen(base+path,timeout=3) as response:
                        self.assertEqual(response.status,200)
                        self.assertEqual(response.headers['Cache-Control'],'no-store')
                        data=json.load(response)
                    if path.endswith('fit'):
                        self.assertEqual(data['models'],[]); self.assertFalse(data['provider_available'])
                    else: self.assertGreater(data['capacity']['memory']['total_bytes'],0)
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(urllib.request.Request(base+path,data=b'{}'),timeout=3)
                    self.assertEqual(caught.exception.code,404)
                with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(base+'/models/fit?model=x',timeout=3)
                self.assertEqual(caught.exception.code,400)
            finally:
                http.shutdown(); http.server_close(); thread.join()

if __name__=='__main__': unittest.main()
