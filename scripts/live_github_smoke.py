"""Explicit opt-in real GitHub protocol test in an isolated temporary branch."""
import argparse
import subprocess
import uuid
import time
from kosuzu.github import GitHub
from kosuzu.model import ValidationError, new_event
from tests.helpers import part


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--repo",required=True); parser.add_argument("--run",action="store_true",help="Authorize temporary branch/PR writes in this repository"); args=parser.parse_args()
    if not args.run: parser.error("Pass --run to create scratch branches and PRs")
    token=subprocess.check_output(["gh","auth","token"],text=True).strip()
    base=GitHub(token,args.repo)
    branch="integration/kosuzu-"+uuid.uuid4().hex
    gh=GitHub(token,args.repo,branch)
    events=[]; numbers=[]
    try:
        base.call("POST","git/refs",{"ref":"refs/heads/"+branch,"sha":base.head()})
        gh.initialize()
        p=part("SMOKE-"+uuid.uuid4().hex[:8])
        create=new_event("create",p["id"],10,p); events.append(create)
        created=gh.submit(create); numbers.append(created["number"]); gh.merge(created["number"])
        pr=gh.call("GET",f"pulls/{created['number']}")
        deadline=time.monotonic()+60
        while not pr.get("merged") and time.monotonic()<deadline:
            time.sleep(1)
            pr=gh.call("GET",f"pulls/{created['number']}")
        assert pr["merged"] is True and pr["state"]=="closed","GitHub did not recognize two-parent merge"
        print(f"GitHub recognized atomic merge of PR #{created['number']}")
        removals=[]
        for _ in range(2):
            event=new_event("adjust",p["id"],-7); events.append(event)
            result=gh.submit(event); numbers.append(result["number"]); removals.append(result)
        gh.merge(removals[0]["number"])
        try: gh.merge(removals[1]["number"])
        except ValidationError as exc: assert "available 3" in str(exc)
        else: raise AssertionError("Overspending removal unexpectedly merged")
        assert gh.inventory()["components"][p["id"]]["quantity"]==3
        assert gh.submit(events[1])["status"]=="applied"
        gh.reject(removals[1]["number"])
        print(f"PASS real GitHub: exact proposal, auto-recognized merge, concurrent removal conflict, idempotent retry. PRs: {numbers}")
    finally:
        for number in numbers:
            pr=gh.call("GET",f"pulls/{number}")
            if pr["state"]=="open": gh.reject(number)
        for event in events:
            try: gh.call("DELETE","git/refs/heads/kosuzu/"+event["id"])
            except Exception: pass
        gh.call("DELETE","git/refs/heads/"+branch)
        print("Temporary branches cleaned; application main branch unchanged")


if __name__=="__main__": main()
