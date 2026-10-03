"""Verify the actual frozen binary contains its web UI and starts correctly."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError


def smoke(executable, mode):
    with tempfile.TemporaryDirectory() as directory:
        log=Path(directory)/"startup.log"
        with log.open("w") as output:
            process=subprocess.Popen([executable,mode,"--state-dir",str(Path(directory)/"state"),"--port","0","--no-browser"],stdout=output,stderr=output)
        try:
            url=None
            for _ in range(100):
                match=re.search(rf"Kosuzu 0\.1\.0 {mode}: (http://[^\s]+)",log.read_text())
                if match: url=match[1]; break
                if process.poll() is not None: raise RuntimeError("Frozen server exited before startup")
                time.sleep(.2)
            if not url: raise RuntimeError("Frozen server did not start within 20 seconds")
            with urlopen(url+"/api/health",timeout=5) as response: assert json.load(response)["mode"]==mode
            for path in ("/","/app.js","/style.css","/manifest.webmanifest","/icon-192.png","/icon-512.png"):
                with urlopen(url+path,timeout=5) as response: assert response.status==200 and len(response.read())>50
            if "--network" in sys.argv:
                # Deliberately invalid credentials exercise frozen HTTPS trust
                # without using or persisting any real user API token.
                key=re.search(r"Administrator access key: ([^\s]+)",log.read_text())[1]
                def post(path,data,cookie=""):
                    return urlopen(Request(url+path,data=json.dumps(data).encode(),headers={"Content-Type":"application/json","X-Kosuzu":"1","Cookie":cookie}),timeout=30)
                with post("/api/login",{"key":key}) as response:
                    cookie=next(c.split(";",1)[0] for c in response.headers.get_all("Set-Cookie") if c.startswith("kosuzu_session="))
                with post("/api/settings",{"repo":"AL-255/kosuzu","client_token":"invalid-smoke-token"},cookie): pass
                try:
                    urlopen(Request(url+"/api/inventory",headers={"Cookie":cookie}),timeout=30)
                except HTTPError as exc:
                    message=json.load(exc)["error"]
                    assert "Authentication failed" in message,message
                else: raise AssertionError("Expected GitHub to reject the deliberately invalid token")
                print("Frozen HTTPS certificate trust verified against GitHub")
            print(f"Frozen {mode} startup, health, and bundled PWA assets passed")
        finally:
            if os.name=="nt":
                # PyInstaller onefile has a bootloader parent and app child.
                # TerminateProcess on only the parent leaves the child running.
                subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
            else:
                process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


def main():
    executable=str(Path(sys.argv[1]).resolve())
    assert subprocess.check_output([executable,"--version"],text=True).strip()=="0.1.0"
    for mode in ("server", "client"):
        smoke(executable, mode)


if __name__=="__main__": main()
