#!/usr/bin/env python3
# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""Theseus web app: live scans, a blast radius map, and an Approve button.

Python standard library only. Run behind Caddy (TLS) on a Vultr VM:
    THESEUS_DATA=/var/lib/theseus THESEUS_ADMIN_TOKEN=... python3 web/server.py --port 8080

Public visitors can run the built-in demo targets, watch progress, read every
receipt and export a trimmed copy. The admin token unlocks heavy targets and,
only if THESEUS_ALLOW_REPO_URL=1, scanning a public GitHub repo.
Nothing is ever deleted: Approve writes a NEW trimmed copy and re-runs the checks on it.
No telemetry, no third-party scripts, no cookies except the session and CSRF cookies.
"""
import argparse
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from theseus import __version__  # noqa: E402
from theseus.agent import redact  # noqa: E402

STATIC = os.path.join(ROOT, "web", "static")
CLI = os.path.join(ROOT, "theseus.py")
RUN_ID = re.compile(r"^(r-)?[a-z0-9][a-z0-9-]{6,80}$")
REPO_URL = re.compile(r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})/?$")
MAX_BODY = 16 * 1024
SESSION_TTL = 12 * 3600

PRESETS = {
    "orderflow": {"name": "orderflow (demo: an order pricing pipeline with real bloat)",
                  "path": os.path.join(ROOT, "demos", "orderflow"),
                  "oracle": os.path.join(ROOT, "demos", "orderflow", "oracle.json"),
                  "credit": "orderflow demo, Copyright (c) 2026 @Nefarious_Trey on X, MIT License.",
                  "admin": False, "minutes": 10},
    "containment-demo": {"name": "containment-demo (a hostile dependency tries to get out)",
                         "path": os.path.join(ROOT, "demos", "containment-demo"),
                         "oracle": os.path.join(ROOT, "demos", "containment-demo", "oracle.json"),
                         "credit": "containment demo, Copyright (c) 2026 @Nefarious_Trey on X, MIT License.",
                         "admin": False, "minutes": 10},
}

EXPRESS_CREDIT = ("Express (https://github.com/expressjs/express), MIT License, Copyright (c) 2009-2014 TJ Holowaychuk, "
                  "(c) 2013-2014 Roman Shtylman, (c) 2014-2015 Douglas Christopher Wilson. "
                  "Used only as the demo target; not part of Theseus.")


def load_extra_presets(path):
    """Extra targets (Express, ...) from a JSON file: {"id": {"name","path","oracle","credit","admin","minutes"}}."""
    if not path or not os.path.isfile(path):
        return
    for k, v in json.load(open(path)).items():
        if re.fullmatch(r"[a-z0-9-]{2,40}", k) and os.path.isdir(v.get("path", "")):
            PRESETS[k] = {"name": str(v.get("name", k)), "path": v["path"], "oracle": v["oracle"],
                          "credit": str(v.get("credit", "")), "admin": bool(v.get("admin", True)),
                          "minutes": int(v.get("minutes", 60)), "workers": v.get("workers")}


class Limiter:
    """Per-IP sliding windows. Buckets: (max events, window seconds)."""
    RULES = {"req": (240, 60), "login": (8, 900), "scan": (8, 3600), "contain": (12, 3600),
             "approve": (12, 3600), "repo": (4, 3600)}

    def __init__(self):
        self.hits = {}
        self.lock = threading.Lock()

    def ok(self, ip, kind):
        cap, win = self.RULES[kind]
        now = time.time()
        with self.lock:
            q = [t for t in self.hits.get((ip, kind), []) if now - t < win]
            if len(q) >= cap:
                self.hits[(ip, kind)] = q
                return False
            q.append(now)
            self.hits[(ip, kind)] = q
            if len(self.hits) > 20000:  # keep memory bounded under a flood
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < 3600}
            return True


class App:
    def __init__(self, data, admin_token, allow_repo=False, trust_proxy=False, agent_daily=60, max_queue=1):
        self.data = os.path.abspath(data)
        self.runs = os.path.join(self.data, "runs")
        self.receipts = os.path.join(self.data, "receipts")
        self.exports = os.path.join(self.data, "exports")
        self.targets = os.path.join(self.data, "targets")
        for d in (self.runs, self.receipts, self.exports, self.targets):
            os.makedirs(d, exist_ok=True)
        self.admin_token = admin_token or ""
        self.allow_repo = allow_repo
        self.trust_proxy = trust_proxy
        self.secret = secrets.token_bytes(32)
        self.sessions = {}
        self.limiter = Limiter()
        self.lock = threading.Lock()
        self.active = {}  # run id -> Popen
        self.max_active = max_queue
        self.agent_daily = agent_daily
        self.agent_used = {}  # yyyy-mm-dd -> scans with the agent
        self.contain_lock = threading.Lock()

    # ---- sessions and CSRF ----
    def new_session(self, admin=False):
        sid = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v["exp"] > now}
            self.sessions[sid] = {"admin": admin, "exp": now + SESSION_TTL}
        return sid

    def session(self, sid):
        s = self.sessions.get(sid or "")
        return s if s and s["exp"] > time.time() else None

    def csrf_for(self, cid):
        return hmac.new(self.secret, (cid or "").encode(), hashlib.sha256).hexdigest()

    def check_token(self, token):
        if not self.admin_token or len(self.admin_token) < 16:
            return False
        return hmac.compare_digest(hashlib.sha256(str(token).encode()).digest(),
                                   hashlib.sha256(self.admin_token.encode()).digest())

    # ---- runs ----
    def run_dir(self, rid):
        if not RUN_ID.match(rid or ""):
            return None
        base = self.receipts if rid.startswith("r-") else self.runs
        d = os.path.realpath(os.path.join(base, rid))
        if not d.startswith(os.path.realpath(base) + os.sep) or not os.path.isdir(d):
            return None
        return d

    def meta(self, d):
        try:
            return json.load(open(os.path.join(d, "meta.json")))
        except (OSError, ValueError):
            return {}

    def write_meta(self, d, m):
        tmp = os.path.join(d, ".meta.tmp")
        with open(tmp, "w") as f:
            json.dump(m, f)
        os.replace(tmp, os.path.join(d, "meta.json"))

    def hidden_runs(self):
        """Run ids kept on disk but left off the public list (see CHANGELOG.md). Never deleted."""
        f = os.path.join(os.path.dirname(self.runs), "hidden_runs.txt")
        try:
            with open(f) as fh:
                return {ln.split("#")[0].strip() for ln in fh if ln.split("#")[0].strip()}
        except OSError:
            return set()

    def list_runs(self):
        out = []
        hidden = self.hidden_runs()
        for base, kind in ((self.receipts, "receipt"), (self.runs, "live")):
            for rid in sorted(os.listdir(base), reverse=True):
                if rid in hidden:
                    continue
                d = self.run_dir(rid)
                if not d:
                    continue
                m = self.meta(d)
                out.append({"id": rid, "kind": kind, "preset": m.get("preset"), "name": m.get("name"),
                            "status": m.get("status"), "started": m.get("started"), "agent": m.get("agent"),
                            "summary": m.get("summary")})
        return out[:200]

    def start_scan(self, preset_id, agent, admin, repo=None):
        with self.lock:
            live = {k: p for k, p in self.active.items() if p.poll() is None}
            self.active = live
            if len(live) >= self.max_active:
                return None, "a scan is already running; watch it, or try again in a minute"
        if repo:
            p = self.prepare_repo(repo)
            if isinstance(p, str):
                return None, p
        else:
            p = PRESETS.get(preset_id)
            if not p:
                return None, "unknown target"
            if p["admin"] and not admin:
                return None, "this target needs the admin token"
        day = time.strftime("%Y-%m-%d")
        use_agent = bool(agent) and bool(os.environ.get("VULTR_INFERENCE_API_KEY"))
        note = None
        if agent and not use_agent:
            note = "agent asked for, but no Vultr inference key is set on the server: rule-based order and wording"
        if use_agent and self.agent_used.get(day, 0) >= self.agent_daily:
            use_agent, note = False, "the agent's daily limit on this demo server is used up: rule-based this time"
        rid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        d = os.path.join(self.runs, rid)
        os.makedirs(d)
        workers = str(p.get("workers") or max(1, min(16, os.cpu_count() or 1)))
        argv = ["timeout", "--kill-after=30", str(int(p.get("minutes", 30)) * 60), sys.executable, CLI, "scan",
                p["path"], "--oracle", p["oracle"], "--out", os.path.join(d, "scan"),
                "--events", os.path.join(d, "events.jsonl"), "--workers", workers, "--credit", p.get("credit") or ""]
        if use_agent:
            argv += ["--agent", "--explain-max", "20"]
            with self.lock:
                self.agent_used[day] = self.agent_used.get(day, 0) + 1
        env = dict(os.environ)
        env.setdefault("THESEUS_LLM_MAX_CALLS", "40")
        m = {"id": rid, "preset": preset_id if not repo else "repo", "name": p["name"], "status": "running",
             "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "agent": use_agent, "note": note}
        self.write_meta(d, m)
        log = open(os.path.join(d, "log.txt"), "wb")
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, env=env,
                                start_new_session=True)
        with self.lock:
            self.active[rid] = proc
        threading.Thread(target=self._watch, args=(rid, d, proc, log), daemon=True).start()
        return rid, note

    def _watch(self, rid, d, proc, log):
        rc = proc.wait()
        log.close()
        m = self.meta(d)
        m["rc"] = rc
        m["status"] = {0: "done", 3: "stopped (baseline failed or flaky)", 124: "timed out"}.get(rc, f"failed (rc {rc})")
        try:
            rep = json.load(open(os.path.join(d, "scan", "report.json")))
            m["summary"] = rep.get("summary")
        except (OSError, ValueError):
            pass
        self.write_meta(d, m)

    def prepare_repo(self, url):
        """Admin only, off by default. Strict URL, argv only (no shell), no prompts, no install scripts."""
        mt = REPO_URL.match(url or "")
        if not mt or mt.group(2) in (".", "..") or mt.group(2).startswith("-"):
            return "only https://github.com/OWNER/REPO URLs are accepted"
        owner, repo = mt.group(1), re.sub(r"\.git$", "", mt.group(2))
        dest = os.path.join(self.targets, f"{owner}-{repo}-{time.strftime('%Y%m%d%H%M%S')}".lower())
        env = {"PATH": "/usr/bin:/bin", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
               "HOME": self.targets, "GIT_ALLOW_PROTOCOL": "https"}
        r = subprocess.run(["timeout", "180", "git", "-c", "protocol.file.allow=never", "-c", "core.symlinks=false",
                            "clone", "--depth", "1", "--single-branch", "--no-tags", "--",
                            f"https://github.com/{owner}/{repo}.git", dest],
                           capture_output=True, env=env)
        if r.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            return "clone failed (public repos only)"
        size = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(dest) for f in fs
                   if not os.path.islink(os.path.join(a, f)))
        if size > 200 * 1024 * 1024:
            shutil.rmtree(dest, ignore_errors=True)
            return "repo is over 200 MB"
        if os.path.isfile(os.path.join(dest, "package-lock.json")):
            subprocess.run(["timeout", "600", "npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                           cwd=dest, capture_output=True, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": self.targets})
        oracle = os.path.join(dest + ".oracle.json")
        cmd = "npm test" if os.path.isfile(os.path.join(dest, "package.json")) else "python3 -m pytest -q"
        with open(oracle, "w") as f:
            json.dump({"steps": [{"name": "test", "timeout": 300, "cmd": cmd}], "env_dirs": ["node_modules"]}, f)
        return {"name": f"github.com/{owner}/{repo}", "path": dest, "oracle": oracle,
                "credit": f"{owner}/{repo} (https://github.com/{owner}/{repo}); see its own LICENSE.",
                "minutes": 60, "admin": True}

    def approve(self, rid, scope):
        d = self.run_dir(rid)
        if not d:
            return 404, {"error": "no such run"}
        rep = os.path.join(d, "scan", "report.json")
        if not os.path.isfile(rep):
            return 409, {"error": "this run has no finished report yet"}
        rdir = os.path.dirname(rep)
        repj = json.load(open(rep))
        stopped = (repj.get("summary") or {}).get("stopped")
        if stopped:
            return 409, {"error": f"this scan stopped early ({stopped}), so there is nothing proven to trim"}
        target = os.path.realpath(os.path.join(rdir, repj["meta"]["target"]))
        if not os.path.isdir(target):
            return 409, {"error": "the scanned project is not on this server, so a trimmed copy can't be made here"}
        name = f"{rid}-trim-{scope}-{time.strftime('%Y%m%d%H%M%S')}"
        dest = os.path.join(self.exports, name)
        r = subprocess.run(["timeout", "900", sys.executable, CLI, "apply", "--report", rep, "--dest", dest,
                            "--approve", "--scope", scope], capture_output=True, text=True, cwd=ROOT)
        out = redact(r.stdout + r.stderr, 6000).replace(self.data, "DATA")
        if not os.path.isdir(dest):
            return 500, {"error": "apply wrote nothing", "log": out}
        tgz = dest + ".tar.gz"
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(dest, arcname=name, filter=_safe_member)
        shutil.rmtree(dest, ignore_errors=True)
        return 200, {"verified": r.returncode == 0, "rc": r.returncode, "log": out,
                     "download": f"/api/exports/{name}.tar.gz"}

    def contain(self):
        if not self.contain_lock.acquire(blocking=False):
            return 429, {"error": "a containment demo is already running"}
        try:
            r = subprocess.run(["timeout", "180", sys.executable, CLI, "contain", "--json"], capture_output=True,
                               text=True, cwd=ROOT)
            res = json.loads(r.stdout)
            home = os.path.expanduser("~")
            for row in res["rows"]:
                row["result"] = redact(row["result"]).replace(home, "~")
            return 200, {"all_held": res["all_held"], "rows": res["rows"]}
        except (ValueError, KeyError):
            return 500, {"error": "containment demo failed to run"}
        finally:
            self.contain_lock.release()


def _safe_member(ti):
    """Tar filter: regular files and dirs only (no links, devices), no owner names, sane modes."""
    if not (ti.isfile() or ti.isdir()):
        return None
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mode = 0o755 if ti.isdir() else (ti.mode & 0o755)
    return ti


def slim_report(path):
    rep = json.load(open(path))
    parts = []
    for r in rep.get("parts", []):
        p = r["part"]
        wb = r.get("what_broke") or {}
        why = [x for v in (wb.get("why") or {}).values() for x in v]
        ex = r.get("explain") or {}
        parts.append({
            "id": p["id"], "file": p["file"], "level": p["level"], "kind": p.get("kind"),
            "name": p.get("name"), "first": p.get("first"), "last": p.get("last"),
            "verdict": r["verdict"], "coverage": r.get("coverage"), "is_test": r.get("is_test"),
            "why": [redact(x, 200) for x in why][:6],
            "radius": (r.get("blast_radius_files") or [])[:30],
            "error": redact((wb.get("error_lines") or [""])[0], 300) if wb else None,
            "notes": [redact(x, 300) for x in (r.get("notes") or [])][:5],
            "explain": {k: redact(v, 800) for k, v in ex.items()} if ex else None,
            "seconds": r.get("seconds"),
        })
    m = rep.get("meta", {})
    return {"summary": rep.get("summary"), "parts": parts,
            "meta": {"target_name": m.get("target_name"), "target_credit": m.get("target_credit"),
                     "commit": m.get("commit"), "finished": m.get("finished"), "theseus": m.get("theseus"),
                     "warnings": [redact(w, 400) for w in m.get("warnings") or []],
                     "baseline_tests": m.get("baseline_tests"), "timing": m.get("timing"),
                     "lockbox": m.get("lockbox"), "agent": m.get("agent"),
                     "agent_brief": redact(m.get("agent_brief") or "", 1500) or None}}


CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
      ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        server_version = "theseus"
        timeout = 20  # slow or stalled clients are dropped
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # no IPs, no query strings in logs
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), redact(fmt % args, 200).split("?")[0]))

        # ---- plumbing ----
        def ip(self):
            peer = self.client_address[0]
            if app.trust_proxy and peer in ("127.0.0.1", "::1"):
                xff = self.headers.get("X-Forwarded-For", "")
                if xff:
                    return xff.split(",")[-1].strip()[:64]
            return peer

        def cookies(self):
            out = {}
            for part in (self.headers.get("Cookie") or "").split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    out[k] = v
            return out

        def secure(self):
            return self.headers.get("X-Forwarded-Proto") == "https"

        def send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def err(self, code, msg):
            self.send(code, {"error": msg})

        def client_id(self):
            """A CSRF id cookie for every visitor (HttpOnly; the token comes from /api/session)."""
            c = self.cookies().get("thc")
            if c and re.fullmatch(r"[A-Za-z0-9_-]{20,64}", c):
                return c, None
            c = secrets.token_urlsafe(24)
            return c, self.cookie("thc", c, SESSION_TTL)

        def cookie(self, k, v, age):
            return f"{k}={v}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}" + ("; Secure" if self.secure() else "")

        def is_admin(self):
            s = app.session(self.cookies().get("ths"))
            return bool(s and s["admin"])

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("body too large")
            raw = self.rfile.read(n) if n else b"{}"
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                raise ValueError("JSON only")
            v = json.loads(raw or b"{}")
            if not isinstance(v, dict):
                raise ValueError("JSON object only")
            return v

        def csrf_ok(self):
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlparse(origin).netloc != host:
                return False
            c = self.cookies().get("thc")
            tok = self.headers.get("X-Theseus-CSRF", "")
            return bool(c) and hmac.compare_digest(app.csrf_for(c), tok)

        # ---- routes ----
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not app.limiter.ok(self.ip(), "req"):
                return self.err(429, "slow down")
            u = urlparse(self.path)
            path = u.path
            q = parse_qs(u.query)
            if path in ("/", "/index.html"):
                cid, setc = self.client_id()
                html = open(os.path.join(STATIC, "index.html"), "rb").read()
                return self.send(200, html, CT[".html"], {"Set-Cookie": setc} if setc else None)
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if not re.fullmatch(r"[a-z0-9_-]{1,40}\.(js|css|svg|png|ico|txt)", name):
                    return self.err(404, "not found")
                f = os.path.join(STATIC, name)
                if not os.path.isfile(f):
                    return self.err(404, "not found")
                return self.send(200, open(f, "rb").read(), CT[os.path.splitext(f)[1]])
            if path == "/healthz":
                return self.send(200, {"ok": True, "theseus": __version__})
            if path == "/api/session":
                cid, setc = self.client_id()
                return self.send(200, {"csrf": app.csrf_for(cid), "admin": self.is_admin(),
                                       "agent_ready": bool(os.environ.get("VULTR_INFERENCE_API_KEY")),
                                       "repo_urls": app.allow_repo and self.is_admin(), "version": __version__,
                                       "host": os.environ.get("THESEUS_HOST_LABEL", "")},
                                 headers={"Set-Cookie": setc} if setc else None)
            if path == "/api/targets":
                adm = self.is_admin()
                return self.send(200, [{"id": k, "name": v["name"], "admin": v["admin"], "credit": v["credit"],
                                        "locked": v["admin"] and not adm} for k, v in PRESETS.items()])
            if path == "/api/runs":
                return self.send(200, app.list_runs())
            m = re.fullmatch(r"/api/runs/([^/]+)(?:/(events|report|markdown))?", path)
            if m:
                d = app.run_dir(m.group(1))
                if not d:
                    return self.err(404, "no such run")
                what = m.group(2)
                if what is None:
                    meta = app.meta(d)
                    log = ""
                    try:
                        with open(os.path.join(d, "log.txt"), "rb") as f:
                            f.seek(max(0, os.path.getsize(f.name) - 4000))
                            log = f.read().decode("utf-8", "replace")
                    except OSError:
                        pass
                    meta["log_tail"] = redact(log, 4000).replace(ROOT, "THESEUS").replace(app.data, "DATA")
                    return self.send(200, meta)
                if what == "events":
                    try:
                        after = max(0, int((q.get("after") or ["0"])[0]))
                    except ValueError:
                        after = 0
                    ev = []
                    try:
                        with open(os.path.join(d, "events.jsonl")) as f:
                            for i, line in enumerate(f):
                                if i >= after and len(ev) < 400:
                                    try:
                                        ev.append(json.loads(line))
                                    except ValueError:
                                        pass
                    except OSError:
                        pass
                    return self.send(200, {"after": after, "events": ev, "status": app.meta(d).get("status")})
                rp = os.path.join(d, "scan", "report.json")
                if not os.path.isfile(rp):
                    return self.err(404, "report not ready")
                if what == "report":
                    return self.send(200, slim_report(rp))
                md = open(os.path.join(d, "scan", "BLAST_RADIUS.md"), encoding="utf-8").read()
                return self.send(200, redact(md).replace(ROOT, "THESEUS"), CT[".txt"])
            m = re.fullmatch(r"/api/exports/([A-Za-z0-9-]{8,160})\.tar\.gz", path)
            if m:
                f = os.path.join(app.exports, m.group(1) + ".tar.gz")
                if not os.path.isfile(f):
                    return self.err(404, "no such export")
                return self.send(200, open(f, "rb").read(), "application/gzip",
                                 {"Content-Disposition": f'attachment; filename="{m.group(1)}.tar.gz"'})
            return self.err(404, "not found")

        def do_POST(self):
            ip = self.ip()
            if not app.limiter.ok(ip, "req"):
                return self.err(429, "slow down")
            if not self.csrf_ok():
                return self.err(403, "missing or bad CSRF token; reload the page")
            try:
                b = self.body()
            except (ValueError, UnicodeDecodeError):
                return self.err(400, "bad request")
            path = urlparse(self.path).path
            if path == "/api/login":
                if not app.limiter.ok(ip, "login"):
                    return self.err(429, "too many tries; wait 15 minutes")
                if not app.check_token(b.get("token", "")):
                    time.sleep(0.5)
                    return self.err(401, "wrong token")
                sid = app.new_session(admin=True)
                return self.send(200, {"admin": True}, headers={"Set-Cookie": self.cookie("ths", sid, SESSION_TTL)})
            if path == "/api/logout":
                app.sessions.pop(self.cookies().get("ths", ""), None)
                return self.send(200, {"admin": False}, headers={"Set-Cookie": self.cookie("ths", "", 0)})
            if path == "/api/scan":
                adm = self.is_admin()
                repo = b.get("repo")
                if repo:
                    if not (adm and app.allow_repo):
                        return self.err(403, "repo URLs are off on this server")
                    if not app.limiter.ok(ip, "repo"):
                        return self.err(429, "repo scan limit reached")
                if not app.limiter.ok(ip, "scan"):
                    return self.err(429, "scan limit reached for now; read the receipts meanwhile")
                rid, note = app.start_scan(str(b.get("target", "")), bool(b.get("agent")), adm,
                                           repo=str(repo) if repo else None)
                if not rid:
                    return self.err(409, note)
                return self.send(202, {"id": rid, "note": note})
            if path == "/api/contain":
                if not app.limiter.ok(ip, "contain"):
                    return self.err(429, "containment demo limit reached for now")
                code, res = app.contain()
                return self.send(code, res)
            m = re.fullmatch(r"/api/runs/([^/]+)/approve", path)
            if m:
                scope = b.get("scope", "bloat")
                if scope not in ("bloat", "all"):
                    return self.err(400, "scope is bloat or all")
                if b.get("confirm") is not True:
                    return self.err(400, "approve needs confirm: true")
                if not app.limiter.ok(ip, "approve"):
                    return self.err(429, "export limit reached for now")
                code, res = app.approve(m.group(1), scope)
                return self.send(code, res)
            return self.err(404, "not found")

    return H


def main():
    ap = argparse.ArgumentParser(description="Theseus web app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data", default=os.environ.get("THESEUS_DATA", os.path.expanduser("~/.theseus-web")))
    a = ap.parse_args()
    load_extra_presets(os.environ.get("THESEUS_PRESETS"))
    app = App(a.data, os.environ.get("THESEUS_ADMIN_TOKEN"), allow_repo=os.environ.get("THESEUS_ALLOW_REPO_URL") == "1",
              trust_proxy=os.environ.get("THESEUS_TRUST_PROXY") == "1",
              agent_daily=int(os.environ.get("THESEUS_AGENT_DAILY", "60")))
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    srv.daemon_threads = True
    srv.request_queue_size = 64
    print(f"theseus web {__version__} on http://{a.host}:{a.port} (data in {a.data})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
