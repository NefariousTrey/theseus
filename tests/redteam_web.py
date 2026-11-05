# Red team for the web app. Start: THESEUS_ADMIN_TOKEN=... VULTR_INFERENCE_API_KEY=FAKE-sk-redteamcanary... python3 web/server.py --port 18080
# then run from the repo root: python3 tests/redteam_web.py
import json, os, socket, time, urllib.request, http.cookiejar, sys
sys.path.insert(0, ".")
B="http://127.0.0.1:18080"; R=[]
def check(name, ok, info=""):
    R.append((name, ok)); print(("PASS " if ok else "FAIL ")+name+(f"  [{info}]" if info else ""))
def mk():
    cj=http.cookiejar.CookieJar(); return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op=mk()
def req(p, body=None, csrf=None, hdr=None, o=None, raw=None, method=None):
    h={"Content-Type":"application/json"} if body is not None or raw is not None else {}
    if csrf: h["X-Theseus-CSRF"]=csrf
    h.update(hdr or {})
    data=raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r=urllib.request.Request(B+p, data=data, headers=h, method=method)
    try:
        x=(o or op).open(r, timeout=200); return x.status, x.headers, x.read()
    except urllib.error.HTTPError as e: return e.code, e.headers, e.read()
def rawget(path):
    s=socket.create_connection(("127.0.0.1",18080)); s.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    d=b""
    while True:
        c=s.recv(65536)
        if not c: break
        d+=c
    return int(d.split(b" ")[1]), d
csrf=json.loads(req("/api/session")[2])["csrf"]
# path traversal
for p in ["/static/../server.py","/static/..%2fserver.py","/static/%2e%2e/server.py","/api/runs/..%2f..%2fetc","/api/runs/../../etc/passwd/report",
          "/api/runs/r-..%2f..%2f/report","/api/exports/..%2f..%2fetc%2fpasswd.tar.gz","/api/runs/%2e%2e/events","//etc/passwd","/static/app.js%00.css"]:
    code, d = rawget(p)
    check(f"traversal {p} -> {code}", code in (400,404) and b"root:" not in d and b"def main" not in d)
# CSRF
check("POST without CSRF header refused", req("/api/scan", {"target":"orderflow"})[0]==403)
check("POST with wrong CSRF refused", req("/api/scan", {"target":"orderflow"}, "0"*64)[0]==403)
check("POST cross-origin refused", req("/api/scan", {"target":"orderflow"}, csrf, {"Origin":"https://evil.example"})[0]==403)
o2=mk(); req("/api/session", o=o2)
check("CSRF token from another visitor refused", req("/api/scan", {"target":"orderflow"}, csrf, o=o2)[0]==403)
check("form-encoded body refused", req("/api/login", raw=b"token=x", csrf=csrf, hdr={"Content-Type":"application/x-www-form-urlencoded"})[0]==400)
check("body over 16 KB refused", req("/api/login", raw=json.dumps({"token":"a"*20000}).encode(), csrf=csrf)[0]==400)
check("JSON array body refused", req("/api/login", raw=b"[1]", csrf=csrf)[0]==400)
# auth
check("admin target hidden/locked when not admin", req("/api/scan", {"target":"express"}, csrf)[0] in (403,409))
check("repo URL refused when the feature is off", req("/api/scan", {"repo":"https://github.com/expressjs/express"}, csrf)[0]==403)
codes=[req("/api/login", {"token":f"wrong{i}"}, csrf)[0] for i in range(10)]
check("login brute force rate limited", codes[:8]==[401]*8 and 429 in codes[8:], str(codes))
check("approve needs confirm:true", req("/api/runs/20261104-000000-abcdef/approve", {"scope":"bloat"}, csrf)[0] in (400,404))
check("approve scope must be bloat|all", req("/api/runs/x/approve", {"scope":"../../","confirm":True}, csrf)[0]==400)
# headers on every response
s,h,_=req("/does-not-exist")
check("security headers on 404", h.get("X-Frame-Options")=="DENY" and "frame-ancestors 'none'" in h.get("Content-Security-Policy","") and h.get("X-Content-Type-Options")=="nosniff")
s,h,_=req("/", o=mk())
ck=h.get("Set-Cookie","")
check("cookie HttpOnly + SameSite=Strict", "HttpOnly" in ck and "SameSite=Strict" in ck, ck[:80])
check("no server version leaked", "Python" not in (h.get("Server") or ""), h.get("Server"))
# secrets: start a scan and a contain with a fake key in the server env, then look for it everywhere
s,h,b=req("/api/scan", {"target":"orderflow","agent":False}, csrf); rid=json.loads(b).get("id")
for _ in range(120):
    if json.loads(req(f"/api/runs/{rid}/events")[2])["status"]!="running": break
    time.sleep(1)
blob=b"".join(req(p)[2] for p in [f"/api/runs/{rid}", f"/api/runs/{rid}/events", f"/api/runs/{rid}/report", f"/api/runs/{rid}/markdown", "/api/runs", "/api/session"])
blob+=req("/api/contain", {}, csrf)[2]
check("fake inference key never appears in any API output", b"redteamcanary" not in blob)
check("host home path never appears in API output", os.path.expanduser("~").encode() not in blob)
# rate limit flood
codes=[req("/healthz")[0] for _ in range(260)]
check("request flood gets 429", 429 in codes, f"{codes.count(429)} of 260 limited")
# XSS: the UI never uses innerHTML / eval
js=open("web/static/app.js").read()
check("UI has no innerHTML/outerHTML/eval/document.write", not any(x in js for x in ("innerHTML","outerHTML","eval(","document.write","insertAdjacentHTML")))
# repo URL validator (feature is admin-only and off by default)
from web.server import REPO_URL
bad=["https://github.com/a/b;rm -rf ~","https://github.com/a/b --upload-pack=touch /tmp/x","https://github.com/-x/y","file:///etc/passwd",
     "https://github.com/a/../../c","https://github.com.evil.com/a/b","https://github.com/a/b?x=1","https://github.com/a/b\nfoo",
     "ssh://git@github.com/a/b","https://user:pw@github.com/a/b","https://github.com/a/b#x","https://github.com/a/$(id)","https://github.com/a/`id`","http://github.com/a/b"]
check("repo URL regex rejects injection forms", all(not REPO_URL.match(u) for u in bad), str([u for u in bad if REPO_URL.match(u)]))
check("repo URL regex accepts a normal repo", bool(REPO_URL.match("https://github.com/expressjs/express")))
print(f"\nRED TEAM (web): {sum(o for _,o in R)}/{len(R)} pass")
