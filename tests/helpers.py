"""In-memory GitHub REST contract with real Git DAG fast-forward semantics."""
import base64
import copy
import json
from urllib.parse import parse_qs, unquote, urlsplit
from kosuzu.github import GitHub
from kosuzu.http import RemoteError
from kosuzu.model import component, canonical, empty_inventory


def part(mpn="R-10K"):
    return component({"manufacturer": "Example Components", "mpn": mpn, "description": "10 kohm 1% resistor", "supplier": "lcsc", "supplier_code": "C25804", "source_url": "https://www.lcsc.com/product-detail/C25804.html", "image_url": "https://www.lcsc.com/example.png", "attributes": {"Resistance": "10 kohm"}, "review": {"model": "test-model", "checked_at": "2026-10-03T00:00:00Z", "warnings": [], "confirmed": True}})


class FakeGitHub:
    def __init__(self, initialized=True):
        self.counter = 0
        self.trees, self.commits, self.refs, self.prs = {}, {}, {}, {}
        self.calls = []
        self.before_update = None
        self.fail_after = None
        self.offline = False
        tree = self.new_tree({"README.md": "Inventory database\n", **({"inventory.json": canonical(empty_inventory())} if initialized else {})})
        self.refs["main"] = self.new_commit(tree, [])

    def ident(self):
        self.counter += 1
        return f"{self.counter:040x}"

    def new_tree(self, files):
        ident = self.ident(); self.trees[ident] = copy.deepcopy(files); return ident

    def new_commit(self, tree, parents):
        ident = self.ident(); self.commits[ident] = {"tree": {"sha": tree}, "parents": [{"sha": p} for p in parents]}; return ident

    def ancestor(self, older, newer):
        if older == newer: return True
        return any(self.ancestor(older, p["sha"]) for p in self.commits[newer]["parents"])

    def files(self, commit):
        return self.trees[self.commits[commit]["tree"]["sha"]]

    def factory(self, token, repo, branch="main"):
        return GitHub(token, repo, branch, self)

    def request(self, method, url, data=None, headers=None, raw=False):
        if self.offline: raise RemoteError("Network unavailable")
        parsed = urlsplit(url)
        path = unquote(parsed.path.split("/", 4)[4]); query = parse_qs(parsed.query)
        self.calls.append((method, path, copy.deepcopy(data)))
        result = self.route(method, path, query, data)
        if self.fail_after == (method, path):
            self.fail_after = None
            raise RemoteError("Response lost after successful write")
        return copy.deepcopy(result)

    def route(self, method, path, query, data):
        if method == "GET" and path.startswith("git/ref/heads/"):
            branch = path.removeprefix("git/ref/heads/")
            if branch not in self.refs: raise RemoteError("Not found",404)
            return {"object": {"sha": self.refs[branch]}}
        if method == "POST" and path == "git/refs":
            branch = data["ref"].removeprefix("refs/heads/")
            if branch in self.refs: raise RemoteError("Already exists",422)
            self.refs[branch] = data["sha"]; return {"object": {"sha": data["sha"]}}
        if method == "GET" and path.startswith("git/commits/"): return self.commits[path.split("/")[-1]]
        if method == "POST" and path == "git/trees":
            files = copy.deepcopy(self.trees[data["base_tree"]])
            for entry in data["tree"]: files[entry["path"]] = entry["content"]
            return {"sha": self.new_tree(files)}
        if method == "POST" and path == "git/commits": return {"sha": self.new_commit(data["tree"],data["parents"])}
        if method == "PATCH" and path.startswith("git/refs/heads/"):
            branch = path.removeprefix("git/refs/heads/")
            if branch == "main" and self.before_update:
                hook,self.before_update = self.before_update,None; hook()
            if data.get("force") is not False: raise AssertionError("Force updates are forbidden")
            if not self.ancestor(self.refs[branch],data["sha"]): raise RemoteError("Not fast-forward",422)
            self.refs[branch] = data["sha"]
            if branch == "main":
                for pr in self.prs.values():
                    if self.ancestor(pr["head"]["sha"], data["sha"]): pr["state"]="closed"; pr["merged_at"]="2026-10-03T00:00:00Z"
            return {"object": {"sha": data["sha"]}}
        if method == "GET" and path.startswith("contents/"):
            ref = query["ref"][0]; ref=self.refs.get(ref,ref)
            files = self.files(ref); name=path.removeprefix("contents/")
            if name not in files: raise RemoteError("Not found",404)
            return {"encoding":"base64","content":base64.b64encode(files[name].encode()).decode()}
        if method == "GET" and path == "pulls":
            results=list(self.prs.values())
            if query.get("state",["open"])[0] != "all": results=[pr for pr in results if pr["state"]==query["state"][0]]
            if "head" in query: results=[pr for pr in results if pr["head"]["ref"]==query["head"][0].split(":",1)[1]]
            if "base" in query: results=[pr for pr in results if pr["base"]["ref"]==query["base"][0]]
            start=(int(query.get("page",[1])[0])-1)*100; return results[start:start+100]
        if method == "POST" and path == "pulls":
            num=len(self.prs)+1
            pr={"number":num,"state":"open","draft":False,"merged_at":None,"html_url":f"https://github.com/test/library/pull/{num}","base":{"ref":data["base"],"sha":self.refs[data["base"]]},"head":{"ref":data["head"],"sha":self.refs[data["head"]],"repo":{"full_name":"test/library"}}}
            self.prs[num]=pr; return pr
        if path.startswith("pulls/"):
            num=int(path.split("/")[1]); pr=self.prs[num]
            if method == "GET" and path.endswith("/files"):
                base,head=self.files(pr["base"]["sha"]),self.files(pr["head"]["sha"])
                files=[{"filename":name,"status":"added" if name not in base else "modified"} for name in head if name not in base or head[name]!=base[name]]
                files.extend({"filename":name,"status":"removed"} for name in base if name not in head)
                start=(int(query.get("page",[1])[0])-1)*100; return files[start:start+100]
            if method == "GET": return pr
            if method == "PATCH": pr.update(data); return pr
        raise AssertionError(f"Unhandled fake API route: {method} {path}")


class ResponseTransport:
    def __init__(self, response): self.response=response; self.calls=[]
    def request(self,*args,**kwargs): self.calls.append((args,kwargs)); return copy.deepcopy(self.response)
    def oauth(self,req): return {"access_token":"oauth-token"}
