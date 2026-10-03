import http.client
import json
import tempfile
import threading
import unittest
from kosuzu.server import make_server
from kosuzu.service import Service
from kosuzu.store import Store
from tests.helpers import FakeGitHub


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=Store(self.temp.name)
        self.service=Service(self.store,"server",FakeGitHub().factory)
        self.server=make_server(self.service,"127.0.0.1",0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.temp.cleanup); self.addCleanup(self.store.db.close); self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.cookie=""

    def request(self,path,body=None,headers=None,method=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        combined={"Host":self.server.allowed_host,"Cookie":self.cookie}
        if body is not None: combined.update({"Content-Type":"application/json","X-Kosuzu":"1"})
        combined.update(headers or {})
        conn.request(method or ("POST" if body is not None else "GET"),path,json.dumps(body) if body is not None else None,combined)
        result=conn.getresponse(); data=result.read(); status=result.status; response_headers=dict(result.getheaders()); conn.close()
        return status,data,response_headers

    def login(self,role="admin"):
        status,body,headers=self.request("/api/login",{"key":self.store.setting("admin_key" if role=="admin" else "client_key")})
        self.assertEqual(status,200); self.cookie=headers["Set-Cookie"].split(";",1)[0]; return headers

    def test_static_shell_and_manifest_served(self):
        for path in ["/","/app.js","/style.css","/sw.js","/manifest.webmanifest","/icon-192.png","/icon-512.png"]:
            with self.subTest(path=path):
                status,body,headers=self.request(path); self.assertEqual(status,200); self.assertTrue(body)
                self.assertIn("frame-ancestors 'none'",headers["Content-Security-Policy"])
        status,body,_=self.request("/manifest.webmanifest")
        self.assertEqual(json.loads(body)["display"],"standalone")

    def test_api_needs_authenticated_cookie(self):
        self.assertEqual(self.request("/api/settings")[0],401)
        headers=self.login(); self.assertIn("HttpOnly",headers["Set-Cookie"]); self.assertIn("SameSite=Strict",headers["Set-Cookie"])
        status,body,headers=self.request("/api/settings"); self.assertEqual(status,200); self.assertEqual(json.loads(body)["role"],"admin")
        self.assertEqual(headers["Cache-Control"],"no-store")

    def test_mutations_reject_csrf_bad_host_and_origin(self):
        self.login()
        for headers in [{"X-Kosuzu":""},{"Origin":"https://evil.example"},{"Host":"evil.example"}]:
            with self.subTest(headers=headers): self.assertEqual(self.request("/api/settings",{},headers)[0],403)

    def test_client_cannot_administer_but_can_read_warnings(self):
        self.login("client")
        self.assertEqual(self.request("/api/sync",{})[0],403)
        self.assertEqual(self.request("/api/settings",{"server_token":"evil"})[0],400)
        self.assertEqual(self.request("/api/queue")[0],200)

    def test_settings_store_and_return_only_credential_flags(self):
        self.login()
        status,body,_=self.request("/api/settings",{"repo":"test/library","server_token":"secret-server","client_token":"secret-client","llm":{"api_key":"secret-llm"}})
        self.assertEqual(status,200); self.assertTrue(json.loads(body)["client_token_saved"])
        self.assertNotIn(b"secret-",body)

    def test_logout_revokes_session_and_removes_profile_credentials(self):
        self.login(); self.request("/api/settings",{"client_token":"secret-client"})
        self.assertEqual(self.request("/api/logout",{})[0],200)
        self.assertEqual(self.request("/api/settings")[0],401)
        self.assertEqual(self.store.execute("SELECT * FROM profiles"),[])

    def test_login_rate_limit(self):
        for _ in range(10): self.assertEqual(self.request("/api/login",{"key":"wrong"})[0],400)
        self.assertEqual(self.request("/api/login",{"key":"wrong"})[0],429)

    def test_path_traversal_cannot_read_private_state(self):
        self.assertEqual(self.request("/../state.sqlite3")[0],404)
        self.assertEqual(self.request("/api/unknown")[0],401)
        self.login(); self.assertEqual(self.request("/api/unknown")[0],404)

    def test_network_listen_requires_https(self):
        with self.assertRaises(ValueError): make_server(self.service,"0.0.0.0",0)

    def test_proxy_https_uses_secure_cookie(self):
        self.server.public_url="https://inventory.example"; self.server.allowed_host="inventory.example"
        self.assertIn("Secure",self.login()["Set-Cookie"])
