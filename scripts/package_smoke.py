"""Verify the actual frozen binary contains its web UI and starts correctly."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen


def main():
    executable=str(Path(sys.argv[1]).resolve())
    assert subprocess.check_output([executable,"--version"],text=True).strip()=="0.1.0"
    with tempfile.TemporaryDirectory() as directory:
        log=Path(directory)/"startup.log"
        with log.open("w") as output:
            process=subprocess.Popen([executable,"server","--state-dir",str(Path(directory)/"state"),"--port","0","--no-browser"],stdout=output,stderr=output)
        try:
            url=None
            for _ in range(100):
                match=re.search(r"Kosuzu 0\.1\.0 server: (http://[^\s]+)",log.read_text())
                if match: url=match[1]; break
                if process.poll() is not None: raise RuntimeError("Frozen server exited before startup")
                time.sleep(.2)
            if not url: raise RuntimeError("Frozen server did not start within 20 seconds")
            with urlopen(url+"/api/health",timeout=5) as response: assert json.load(response)["mode"]=="server"
            for path in ("/","/app.js","/style.css","/manifest.webmanifest","/icon-192.png","/icon-512.png"):
                with urlopen(url+path,timeout=5) as response: assert response.status==200 and len(response.read())>50
            print("Frozen executable startup, health, and bundled PWA assets passed")
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


if __name__=="__main__": main()
