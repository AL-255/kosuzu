"""Build a standalone desktop/server executable and platform archive."""
import argparse
import platform
import shutil
import subprocess
import sys
import certifi
from pathlib import Path
from kosuzu import __version__

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--platform-label",default=f"{platform.system().lower()}-{platform.machine().lower()}"); args=parser.parse_args()
    subprocess.run([sys.executable,"-m","PyInstaller","--noconfirm","--clean","--onefile","--name","kosuzu","--collect-data","kosuzu","--add-data",certifi.where()+":kosuzu",str(ROOT/"scripts"/"desktop_entry.py")],cwd=ROOT,check=True)
    executable=ROOT/"dist"/("kosuzu.exe" if sys.platform=="win32" else "kosuzu")
    subprocess.run([sys.executable,str(ROOT/"scripts"/"package_smoke.py"),str(executable)],check=True,cwd=ROOT)
    staging=ROOT/"build"/f"kosuzu-{__version__}-{args.platform_label}"
    staging.mkdir(parents=True,exist_ok=True)
    shutil.copy2(executable,staging/executable.name)
    for name in ("README.md","LICENSE"):
        shutil.copy2(ROOT/name,staging/name)
    # Preserve the CA bundle's Mozilla Public License notice in distributions.
    import importlib.metadata
    distribution=importlib.metadata.distribution("certifi")
    for file in distribution.files:
        if "LICENSE" in str(file).upper():
            shutil.copy2(distribution.locate_file(file),staging/"CERTIFI-LICENSE.txt")
            break
    shutil.copytree(ROOT/"docs",staging/"docs",dirs_exist_ok=True)
    shutil.copytree(ROOT/"deploy",staging/"deploy",dirs_exist_ok=True)
    archive=ROOT/"dist"/staging.name
    result=shutil.make_archive(str(archive),"zip" if sys.platform=="win32" else "gztar",root_dir=staging.parent,base_dir=staging.name)
    print(f"Distribution built and HTTP smoke-tested: {result}")


if __name__=="__main__": main()
