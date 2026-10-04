"""Explicit opt-in real GitHub protocol test in an isolated temporary branch."""
import argparse
import getpass
import subprocess
import uuid
import time
from kosuzu.github import GitHub
from kosuzu.http import RemoteError
from kosuzu.model import ValidationError, new_event, new_transfer, new_box_event
from tests.helpers import part


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--repo",required=True); parser.add_argument("--run",action="store_true",help="Authorize temporary branch/PR writes in this repository"); parser.add_argument("--token-prompt",action="store_true",help="Read a dedicated test token without echoing or saving it"); args=parser.parse_args()
    if not args.run: parser.error("Pass --run to create scratch branches and PRs")
    token=getpass.getpass("GitHub test token (not saved): ") if args.token_prompt else subprocess.check_output(["gh","auth","token"],text=True).strip()
    base=GitHub(token,args.repo)
    metadata=base.repository()
    base.branch=metadata["default_branch"]
    try: base.head()
    except RemoteError as exc:
        if exc.status!=409: raise
        base.initialize()
        print("Initialized the empty test repository with an empty inventory snapshot")
    default_head=base.head()
    branch="integration/kosuzu-"+uuid.uuid4().hex
    gh=GitHub(token,args.repo,branch)
    events=[]; numbers=[]; branch_created=False
    try:
        base.call("POST","git/refs",{"ref":"refs/heads/"+branch,"sha":base.head()})
        branch_created=True
        gh.initialize()
        p=part("SMOKE-"+uuid.uuid4().hex[:8])
        p["review"]["model"]="manual"
        p["review"]["warnings"]=["Manually reviewed live test component; no LLM request was made."]
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
        gh.report(removals[1]["number"], "Insufficient stock: available 3, requested 7. Submit a smaller removal")
        assert gh.submit(events[2])["status"]=="blocked"
        assert gh.submit(events[1])["status"]=="applied"
        gh.reject(removals[1]["number"])
        def apply(event):
            events.append(event); result=gh.submit(event); numbers.append(result["number"])
            gh.merge(result["number"])
            return result
        a={"id":uuid.uuid4().hex,"name":"SMOKE BOXA","description":"Temporary test box","image_url":"https://example.com/box.jpg"}
        b={**a,"id":uuid.uuid4().hex,"name":"SMOKE BOXB"}
        apply(new_box_event(a)); apply(new_box_event(b))
        apply(new_transfer(p["id"],3,"",a["id"]))
        apply(new_event("adjust",p["id"],20,box_id=b["id"]))
        unqualified=new_event("adjust",p["id"],1); events.append(unqualified)
        result=gh.submit(unqualified); numbers.append(result["number"])
        try: gh.merge(result["number"])
        except ValidationError as exc: assert "multiple boxes" in str(exc)
        else: raise AssertionError("Ambiguous multi-box adjustment merged")
        gh.reject(result["number"])
        apply(new_transfer(p["id"],1,a["id"],b["id"]))
        apply(new_box_event({**b,"description":"Reviewed box edit"},b))
        row=gh.inventory()["components"][p["id"]]
        assert row["quantity"]==23 and row["boxes"]=={a["id"]:2,b["id"]:21}
        assert base.head()==default_head,"Repository default branch changed during scratch test"
        print(f"PASS real GitHub: initialization, manual review, atomic merge, concurrent removal conflict, idempotent retry, client status, boxes/edit/transfers, and mandatory multi-box selection. PRs: {numbers}")
    finally:
        failures=[]
        for number in numbers:
            try:
                pr=gh.call("GET",f"pulls/{number}")
                if pr["state"]=="open": gh.reject(number)
            except RemoteError as exc: failures.append(str(exc))
        for event in events:
            try: gh.call("DELETE","git/refs/heads/kosuzu/"+event["id"])
            except RemoteError as exc:
                if exc.status!=404: failures.append(str(exc))
        if branch_created:
            try: gh.call("DELETE","git/refs/heads/"+branch)
            except RemoteError as exc: failures.append(str(exc))
        token=""
        if failures: raise RemoteError("Temporary branch cleanup needs attention: "+"; ".join(failures))
        print("Temporary branches cleaned; repository default branch unchanged")


if __name__=="__main__": main()
