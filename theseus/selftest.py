# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""Containment self-test: try to get out of the lockbox and prove we can't."""
import json
import os
import hashlib
import shlex
import tempfile

from . import parts as P

from .lockbox import Lockbox

PROBE = r'''
import json, os, socket, sys
home = sys.argv[1]
r = {}
def attempt(name, fn):
    try:
        r[name] = "ESCAPED: " + str(fn())
    except Exception as e:
        r[name] = "blocked (" + type(e).__name__ + ": " + str(e)[:80] + ")"
def net_ip():
    s = socket.create_connection(("1.1.1.1", 443), timeout=3); s.close(); return "connected"
def net_dns():
    return socket.getaddrinfo("github.com", 443)[0][4]
def read_home():
    return os.listdir(home)[:3]
def write_system():
    open("/usr/theseus_probe", "w").write("x"); return "wrote /usr"
attempt("network_ip_1.1.1.1:443", net_ip)
attempt("network_dns_github.com", net_dns)
attempt("read_host_home", read_home)
attempt("write_/usr", write_system)
secrets = [k for k in os.environ if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASS"))]
r["secret_like_env_vars"] = secrets
r["env_var_names"] = sorted(os.environ)
r["can_see_work"] = os.listdir("/work")
r["fixed_env"] = {k: os.environ.get(k) for k in ("TZ", "LC_ALL", "LANG", "HOME", "PYTHONHASHSEED", "CI")}
h = os.environ.get("HOME", "")
r["home_empty_and_writable"] = (os.listdir(h) == [] and bool(open(os.path.join(h, ".x"), "w").write("x")))
import time
r["tz_offset"] = time.strftime("%z")

print(json.dumps(r))
'''


def run(memory="2G"):
    box = Lockbox(memory=memory)
    home = os.path.expanduser("~")
    with tempfile.TemporaryDirectory(prefix="theseus-probe-") as d:
        with open(os.path.join(d, "probe.py"), "w") as f:
            f.write(PROBE)
        res = box.run(d, "python3 probe.py " + shlex.quote(home), timeout_s=30)
    try:
        r = json.loads(res["output"].strip().splitlines()[-1])
    except Exception:
        return {"passed": False, "raw": res["output"]}
    escaped = [k for k, v in r.items() if isinstance(v, str) and v.startswith("ESCAPED")]
    fe = r["fixed_env"]
    r["deterministic_env"] = ("ok (TZ=UTC, C.UTF-8, clean empty HOME, PYTHONHASHSEED=0)"
                              if fe == {"TZ": "UTC", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "HOME": "/home/theseus",
                                        "PYTHONHASHSEED": "0", "CI": "1"} and r["home_empty_and_writable"]
                              and r["tz_offset"] == "+0000" else f"FAIL: {fe} home_ok={r['home_empty_and_writable']}")
    cases = {
        "memory_cap": (memory_case, "ok"),
        "symlink_write_through": (symlink_case, "blocked"),
        "keep_copies_and_wipe_guard": (keep_case, "ok"),
        "recheck_repeatability": (recheck_case, "ok"),
        "timeout_label_and_recheck": (timeout_case, "ok"),
        "count_parsers": (counts_case, "ok"),
        "broken_rules_count_drop_skip_rise_load_crash": (broken_case, "ok"),
        "flaky_baseline_guard": (flaky_case, "ok"),
        "host_secret_dirs_masked": (mask_case, "ok"),
        "failed_baseline_cleanup": (failed_baseline_case, "ok"),
        "coverage_python_untested_vs_bloat": (py_coverage_case, "ok"),
        "coverage_node_untested_vs_bloat": (node_coverage_case, "ok"),
        "short_names_and_blast_paths": (names_case, "ok"),
        "config_as_code_kind": (config_case, "ok"),
        "hang_partial_failure_note": (hang_note_case, "ok"),
        "recheck_hung_both_times_label": (recheck_label_case, "ok"),
        "silent_runner_warning": (silent_runner_case, "ok"),
        "kill_cleanup_and_clean": (kill_cleanup_case, "ok"),
        "agent_plans_explains_never_judges": (agent_case, "ok"),
    }
    failed = []
    if escaped or r["secret_like_env_vars"]:
        failed.append("containment probe")
    if not r["deterministic_env"].startswith("ok"):
        failed.append("deterministic_env")
    for name, (fn, want) in cases.items():
        try:
            r[name] = fn()
        except Exception as e:  # a crashing case is a failing case
            r[name] = f"FAIL: crashed: {type(e).__name__}: {e}"
        if not r[name].startswith(want) or r[name].startswith("ok-weak"):
            failed.append(name)
    r["failed_cases"] = failed
    r["passed"] = not failed
    r["lockbox"] = box.describe()
    return r


def symlink_case():
    """A link inside the copy that points outside must be skipped and never written."""
    with tempfile.TemporaryDirectory(prefix="theseus-link-") as t:
        outside = os.path.join(t, "outside")
        copy = os.path.join(t, "copy")
        os.makedirs(outside)
        os.makedirs(copy)
        victim = os.path.join(outside, "victim.js")
        with open(victim, "w") as f:
            f.write("function a() {\n  return 1\n}\nfunction b() {\n  return 2\n}\n")
        before = hashlib.sha256(open(victim, "rb").read()).hexdigest()
        os.symlink(victim, os.path.join(copy, "lib.js"))
        os.symlink(outside, os.path.join(copy, "linkdir"))
        listed = P.list_files(copy)
        refused = 0
        for part in ({"level": "block", "file": "lib.js", "first": 0, "last": 2},
                     {"level": "block", "file": "linkdir/victim.js", "first": 0, "last": 2},
                     {"level": "file", "file": "lib.js"}):
            try:
                P.apply_removal(copy, part)
            except PermissionError:
                refused += 1
        after = hashlib.sha256(open(victim, "rb").read()).hexdigest()
        if listed or refused != 3 or before != after:
            return f"ESCAPED: listed={listed} refused={refused}/3 victim_changed={before != after}"
        return "blocked (links not listed, 3/3 writes refused, outside file unchanged)"


def _mini(t):
    tgt = os.path.join(t, "proj")
    os.makedirs(tgt)
    with open(os.path.join(tgt, "need.txt"), "w") as f:
        f.write("x\n")
    with open(os.path.join(tgt, "extra.txt"), "w") as f:
        f.write("y\n")
    return tgt, {"steps": [{"name": "need", "cmd": "test -f need.txt", "timeout": 20}]}


def keep_case():
    """--keep-copies keeps one named copy per cut; default wipes; wipe refuses outside paths."""
    from .engine import Scan, wipe
    with tempfile.TemporaryDirectory(prefix="theseus-keep-") as t:
        tgt, orc = _mini(t)
        s = Scan(tgt, orc, os.path.join(t, "out1"), workers=1, blocks=False, log=lambda *_: None)
        s.run()
        left = os.listdir(os.path.join(t, "out1"))
        if "work" in left and os.listdir(os.path.join(t, "out1", "work")):
            return "FAIL: default run left copies behind"
        k = Scan(tgt, orc, os.path.join(t, "out2"), workers=1, blocks=False, log=lambda *_: None,
                 keep_copies=True)
        k.run()
        kept = sorted(os.listdir(os.path.join(t, "out2", "copies")))
        if len(kept) != 2 or not all(x.startswith("remove-") for x in kept):
            return f"FAIL: kept copies {kept}"
        try:
            wipe(tgt, inside=os.path.join(t, "out2"))
            return "FAIL: wipe of the original was allowed"
        except PermissionError:
            pass
        if not os.path.exists(os.path.join(tgt, "need.txt")):
            return "FAIL: original changed"
        return f"ok (default wiped all copies; kept {len(kept)} named copies; wipe outside refused)"


def recheck_case():
    """Repeat the same cut: a steady check matches, a coin-flip check is caught as flaky."""
    from .engine import Scan
    with tempfile.TemporaryDirectory(prefix="theseus-rep-") as t:
        tgt, orc = _mini(t)
        s = Scan(tgt, orc, os.path.join(t, "o1"), workers=1, blocks=False, log=lambda *_: None, repeat=3)
        recs = s.run()
        if not all(r["repeat"]["repeatable"] for r in recs):
            return "FAIL: steady check marked flaky"
        flip = {"steps": [{"name": "coin", "timeout": 20,
                           "cmd": "exit $(( $(od -An -N1 -tu1 /dev/urandom) % 2 ))"}]}
        f = Scan(tgt, flip, os.path.join(t, "o2"), workers=1, blocks=False, log=lambda *_: None, repeat=12)
        recs = [f.check(P.file_part("extra.txt"))]
        rep = recs[0]["repeat"]
        if rep["repeatable"]:
            return f"ok-weak (coin flip happened to repeat: {rep['results']})"
        return "ok (steady cuts repeatable; coin-flip check flagged 'flaky, not repeatable')"


def timeout_case():
    """v0.2.3: a cut that hangs is rerun (long cap after a hang), at most 5 runs.
    A cut that hangs once then passes is mixed: flaky, kept load-bearing.
    A cut that hangs every time stays inconclusive. Never more than 5 runs."""
    from .engine import Scan, INCONCLUSIVE
    from . import runend as RE
    with tempfile.TemporaryDirectory(prefix="theseus-to-") as t:
        tgt, _ = _mini(t)
        # sleeps 4s only when extra.txt is gone: 2s cap times out, the 8s rerun cap passes
        slow = {"steps": [{"name": "slow", "timeout": 2,
                           "cmd": "test -f extra.txt || sleep 4; test -f need.txt"}]}
        s = Scan(tgt, slow, os.path.join(t, "o1"), workers=2, blocks=False, log=lambda *_: None,
                 recheck_cap_s=8)
        recs = {r["part"]["id"]: r for r in s.run()}
        e = recs["extra.txt"]
        ro = e.get("runs") or {}
        if (e["verdict"] != "LOAD-BEARING" or e.get("end_rule") != RE.FLAKY_LB or ro.get("n") != RE.MAX_RUNS
                or ro.get("outcomes", [None])[0] != RE.HUNG or RE.PASS not in ro.get("outcomes", [])):
            return f"FAIL: slow cut gave {e['verdict']} {e.get('end_rule')} {ro}"
        if recs["need.txt"].get("runs", {}).get("n") != 1:
            return "FAIL: a sure cut was rerun"
        hang = {"steps": [{"name": "hang", "timeout": 1, "cmd": "test -f extra.txt || sleep 30"}]}
        h = Scan(tgt, hang, os.path.join(t, "o2"), workers=2, blocks=False, log=lambda *_: None,
                 recheck_cap_s=2)
        recs = {r["part"]["id"]: r for r in h.run()}
        x = recs["extra.txt"]
        if x["verdict"] != INCONCLUSIVE or x["runs"]["n"] != RE.MAX_RUNS:
            return f"FAIL: hang gave {x['verdict']} after {x.get('runs')}"
        return ("ok (hang then pass: flaky, kept load-bearing after 5 runs; hang every time: inconclusive "
                "after 5 runs; a sure cut ran once; never more than 5 runs)")


def _proj(t, files):
    tgt = os.path.join(t, "proj")
    for rel, body in files.items():
        fp = os.path.join(tgt, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w") as f:
            f.write(body)
    return tgt


def _scan(t, tgt, oracle, name, **kw):
    from .engine import Scan
    kw.setdefault("workers", 4)
    s = Scan(tgt, oracle, os.path.join(t, name), log=lambda *_: None, **kw)
    recs = s.run()
    return s, {r["part"]["id"]: r for r in recs}


def counts_case():
    """Every runner summary we claim to read, plus 'unknown' when nothing parses."""
    from .counts import parse
    cases = [
        ("mocha", "\n  12 passing (30ms)\n  2 pending\n  1 failing\n", (12, 1, 2)),
        ("mocha all fail", "\n  3 failing\n", (0, 3, 0)),
        ("jasmine", "10 specs, 1 failure, 2 pending specs\n", (7, 1, 2)),
        ("jasmine no pending", "5 specs, 0 failures\n", (5, 0, 0)),
        ("jasmine-node", "8 tests, 20 assertions, 1 failure, 2 skipped\n", (5, 1, 2)),
        ("jest", "Tests:       1 failed, 2 skipped, 10 passed, 13 total\n", (10, 1, 2)),
        ("vitest", "      Tests  1 failed | 10 passed | 2 skipped (13)\n", (10, 1, 2)),
        ("tap", "# tests 6\n# pass 4\n# fail 1\n# skip 1\n", (4, 1, 1)),
        ("node:test", "\u2139 tests 6\n\u2139 pass 5\n\u2139 fail 0\n\u2139 skipped 1\n\u2139 todo 0\n", (5, 0, 1)),
        ("pytest", "===== 5 passed, 1 skipped, 2 failed in 0.12s =====\n", (5, 2, 1)),
        ("go test", "--- PASS: TestA (0.00s)\n--- FAIL: TestB (0.00s)\n--- SKIP: TestC (0.00s)\n", (1, 1, 1)),
        ("unittest ok", "Ran 7 tests in 0.010s\n\nOK (skipped=2)\n", (5, 0, 2)),
        ("unittest failed", "Ran 4 tests in 0.010s\n\nFAILED (failures=1, errors=1)\n", (2, 2, 0)),
        ("ansi colors", "\x1b[32m  9 passing\x1b[0m\x1b[90m (4ms)\x1b[0m\n", (9, 0, 0)),
        ("two frameworks add up", "  3 passing (1ms)\n# pass 2\n", (5, 0, 0)),
    ]
    bad = []
    for name, out, want in cases:
        c = parse(out)
        got = None if c is None else (c["passed"], c["failed"], c["skipped"])
        if got != want:
            bad.append(f"{name}: got {got} want {want}")
    if parse("lint ok, nothing to report\n") is not None or parse("") is not None:
        bad.append("no summary should be unknown (None), not 0")
    if bad:
        return "FAIL: " + "; ".join(bad)
    return f"ok ({len(cases)} summaries read right across 11 runners; no summary = unknown)"


def broken_case():
    """Exit 0 is not enough: count drop, skip rise and a swallowed load crash all count as BROKEN."""
    files = {
        "app.py": "def add(a, b):\n    return a + b\n",
        "extra.py": "FLAG = 1\n",
        "plugin.py": "X = 1\n",
        "tests/test_a.py": "import unittest\nfrom app import add\nclass A(unittest.TestCase):\n"
                           "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n",
        "tests/test_b.py": "import unittest\ntry:\n    import extra\nexcept ImportError:\n    extra = None\n"
                           "class B(unittest.TestCase):\n    def test_flag(self):\n"
                           "        if extra is None:\n            self.skipTest('extra missing')\n"
                           "        self.assertEqual(extra.FLAG, 1)\n",
        "run.py": "import sys, traceback\nsys.path.insert(0, '.')\ntry:\n    import plugin\nexcept Exception:\n"
                  "    traceback.print_exc()\n",
    }
    with tempfile.TemporaryDirectory(prefix="theseus-broken-") as t:
        tgt = _proj(t, files)
        orc = {"steps": [{"name": "unit", "timeout": 30, "cmd": "python3 -m unittest discover -s tests 2>&1"},
                         {"name": "plugins", "timeout": 30, "cmd": "python3 run.py"}]}
        s, recs = _scan(t, tgt, orc, "o", blocks=False)
        bad = []
        b = recs["tests/test_b.py"]
        if b["verdict"] != "LOAD-BEARING" or not b.get("is_test") or s.base_tests != 2:
            bad.append(f"test file cut that drops count: {b['verdict']} base={s.base_tests}")
        e = recs["extra.py"]
        why = " ".join(e.get("what_broke", {}).get("why", {}).get("unit", []))
        if e["verdict"] != "LOAD-BEARING" or "skipped/pending rose" not in why:
            bad.append(f"skip rise: {e['verdict']} {why}")
        pl = recs["plugin.py"]
        why = " ".join(pl.get("what_broke", {}).get("why", {}).get("plugins", []))
        if pl["verdict"] != "LOAD-BEARING" or "new load crash" not in why or pl["steps"][1]["rc"] != 0:
            bad.append(f"swallowed import crash: {pl['verdict']} {why}")
        if bad:
            return "FAIL: " + "; ".join(bad)
        return ("ok (test cut 2->1 passing = LOAD-BEARING test; extra.py cut: exit 0 but skip rose = BROKEN; "
                "plugin.py cut: exit 0 but new ModuleNotFoundError = BROKEN)")


def mask_case():
    """Secret folders under /etc (Theseus's own key file, ssh keys, wifi passwords) are empty inside the lockbox."""
    from .lockbox import mask_dirs
    dirs = mask_dirs()
    if not dirs:
        return "ok (none of the secret folders exist on this host)"
    with tempfile.TemporaryDirectory(prefix="theseus-mask-") as t:
        cmd = "; ".join(f"echo {shlex.quote(d)}=$(ls -A {shlex.quote(d)} 2>/dev/null | wc -l); touch {shlex.quote(d)}/x 2>/dev/null && echo WROTE" for d in dirs)
        r = Lockbox(memory="256M").run(t, cmd, 20)
    seen = [ln for ln in r["output"].splitlines() if "=" in ln and not ln.endswith("=0")]
    if seen or "WROTE" in r["output"]:
        return "FAIL: " + r["output"][-300:]
    return f"ok ({len(dirs)} host secret folders under /etc are empty and read-only inside: {', '.join(dirs)})"


def flaky_case():
    """Baseline runs that disagree: the scan stops and makes no bloat calls."""
    with tempfile.TemporaryDirectory(prefix="theseus-flaky-") as t:
        tgt, _ = _mini(t)
        orc = {"steps": [{"name": "wobbly", "timeout": 20,
                          "cmd": "python3 -c \"import os; print('  %d passing (1ms)' % int.from_bytes(os.urandom(3), 'big'))\""}]}
        s, recs = _scan(t, tgt, orc, "o", blocks=False, baseline_runs=5)
        if s.stopped != "flaky baseline" or recs or not s.base.get("flaky"):
            return f"FAIL: stopped={s.stopped} recs={len(recs)}"
        s2, recs2 = _scan(t, tgt, orc, "o2", blocks=False, baseline_runs=3, allow_flaky=True)
        bloat = [r for r in recs2.values() if r.get("coverage") == "proven-not-needed"]
        if bloat or not recs2:
            return f"FAIL: --allow-flaky still made bloat calls: {len(bloat)}"
        if os.path.exists(os.path.join(t, "o", "work")):
            return "FAIL: flaky stop left a work folder"
        return "ok (5 disagreeing baseline runs: scan stopped; --allow-flaky maps parts but 0 bloat calls)"


def failed_baseline_case():
    """Fix 16: a failing baseline stops cleanly and leaves no empty work folder."""
    with tempfile.TemporaryDirectory(prefix="theseus-fail-") as t:
        tgt, _ = _mini(t)
        s, recs = _scan(t, tgt, {"steps": [{"name": "nope", "timeout": 10, "cmd": "exit 1"}]}, "o", blocks=False)
        if s.stopped != "baseline failed" or recs or os.path.exists(os.path.join(t, "o", "work")):
            return f"FAIL: stopped={s.stopped} work_left={os.path.exists(os.path.join(t, 'o', 'work'))}"
        return "ok (baseline exit 1: stopped, no cuts, work folder removed)"


def py_coverage_case():
    """Coverage honesty in Python: never-run code is UNTESTED, only code the tests ran can be bloat."""
    files = {
        "lib.py": "import os\n\n\ndef used():\n    return 1\n\n\ndef unused():\n    return 2\n\n\n"
                  "def optional():\n    return 3\n\n\ndef helper():\n    return 5\n\n\n"
                  "if os.environ.get('CI') != '1':\n    helper()\n",
        "dead.py": "def nobody():\n    return 4\n",
        "warm.py": "CACHE = {i: i for i in range(3)}\n",
        "tests/test_lib.py": "import unittest\nimport lib\ntry:\n    import warm\nexcept ImportError:\n    pass\n"
                             "class T(unittest.TestCase):\n    def test_used(self):\n        self.assertEqual(lib.used(), 1)\n"
                             "        if hasattr(lib, 'optional'):\n            lib.optional()\n"
                             "        getattr(lib, 'helper', lambda: 5)()\n",
    }
    with tempfile.TemporaryDirectory(prefix="theseus-pycov-") as t:
        tgt = _proj(t, files)
        orc = {"steps": [{"name": "unit", "timeout": 30, "cmd": "python3 -m unittest discover -s tests 2>&1"}]}
        s, recs = _scan(t, tgt, orc, "o")
        got = {k.split("@")[0]: (r["verdict"], r.get("coverage")) for k, r in recs.items()}
        want = {
            "lib.py": ("LOAD-BEARING", None),
            "lib.py::used": ("LOAD-BEARING", None),
            "lib.py::unused": ("NOT NEEDED", "UNTESTED: human call"),
            "lib.py::optional": ("NOT NEEDED", "proven-not-needed"),
            "lib.py::helper": ("NOT NEEDED", "UNTESTED: human call"),
            "dead.py": ("NOT NEEDED", "UNTESTED: human call"),
            "warm.py": ("NOT NEEDED", "proven-not-needed"),
            "tests/test_lib.py": ("LOAD-BEARING", None),
        }
        bad = [f"{k}: got {got.get(k)} want {v}" for k, v in want.items() if got.get(k) != v]
        if "python" not in s.cov_note:
            bad.append("no python coverage: " + s.cov_note)
        if bad:
            return "FAIL: " + "; ".join(bad)
        return ("ok (python sys.monitoring: never-called unused() and never-imported dead.py = UNTESTED; "
                "optional() and warm.py ran at baseline and nothing changed without them = proven bloat; "
                "helper() ran but lib.py also calls it in a branch the tests never took = UNTESTED)")


def node_coverage_case():
    """Coverage honesty in Node (V8 coverage, no installs): same rules as Python."""
    import shutil
    if not shutil.which("node"):
        return "ok-weak (no node on this machine)"
    files = {
        "lib.js": "function used() {\n  return 1\n}\n\nfunction unused() {\n  return 2\n}\n\n"
                  "function optional() {\n  return 3\n}\n\nmodule.exports = { used }\n"
                  "if (typeof optional === 'function') module.exports.optional = optional\n"
                  "var logger = String\nif (process.env.CI !== '1') logger('only outside CI')\n",
        "dead.js": "module.exports = function nobody() {\n  return 4\n}\n",
        "test/idle.test.js": "function neverCalled() {\n  return 1\n}\nmodule.exports = neverCalled\n",
        "test/lib.test.js": "const test = require('node:test')\nconst assert = require('node:assert')\n"
                            "const lib = require('../lib')\n"
                            "test('used', () => {\n  assert.strictEqual(lib.used(), 1)\n"
                            "  if (lib.optional) lib.optional()\n})\n",
    }
    with tempfile.TemporaryDirectory(prefix="theseus-nodecov-") as t:
        tgt = _proj(t, files)
        orc = {"steps": [{"name": "unit", "timeout": 60, "cmd": "node --test --test-reporter=tap"}]}
        s, recs = _scan(t, tgt, orc, "o")
        got = {k.split("@")[0]: (r["verdict"], r.get("coverage")) for k, r in recs.items()}
        want = {
            "lib.js": ("LOAD-BEARING", None),
            "lib.js::used": ("LOAD-BEARING", None),
            "lib.js::unused": ("NOT NEEDED", "UNTESTED: human call"),
            "lib.js::logger": ("NOT NEEDED", "UNTESTED: human call"),
            "dead.js": ("NOT NEEDED", "UNTESTED: human call"),
            "test/lib.test.js": ("LOAD-BEARING", None),
        }
        bad = [f"{k}: got {got.get(k)} want {v}" for k, v in want.items() if got.get(k) != v]
        if got.get("lib.js::optional") != ("NOT NEEDED", "proven-not-needed"):
            bad.append(f"lib.js::optional: got {got.get('lib.js::optional')} want proven bloat")
        if not any("test/idle.test.js" in w and "none of their functions ran" in w for w in s.warnings):
            bad.append(f"idle test file not warned: {s.warnings}")
        # node --test counts a file with no tests as one passing test, so the baseline is 2
        if "node" not in s.cov_note or s.base_tests != 2:
            bad.append(f"coverage={s.cov_note} base_tests={s.base_tests}")
        if bad:
            return "FAIL: " + "; ".join(bad)
        return ("ok (node V8 coverage: never-called unused() and never-required dead.js = UNTESTED; "
                "optional() ran and nothing changed without it = proven bloat; "
                "logger ran but its one use is in a branch on the same line the tests never took = UNTESTED; "
                "a test file that loads but runs nothing is warned; tap count read)")


def names_case():
    """Short names need an exact code use; paths need word edges."""
    with tempfile.TemporaryDirectory(prefix="theseus-names-") as t:
        tgt = _proj(t, {
            "lib/q.js": "function go() {}\nmodule.exports = go\n",
            "lib/index.js": "x\n",
            "index.js": "x\n",
            "a.js": "// the letter q and the word queue are not uses\nconst sequence = 1\n",
            "b.js": "const q = require('./lib/q')\n",
            "c.js": "// go is a word here\n",
            "d.js": "go()\n",
        })
        files = P.list_files(tgt)
        bad = []
        refs = P.references(tgt, files, P.file_part("lib/q.js"))
        if refs != ["b.js"]:
            bad.append(f"short file q.js refs {refs}")
        blk = {"level": "block", "file": "lib/q.js", "name": "go", "first": 0, "last": 0}
        refs = P.references(tgt, files, blk)
        if sorted(refs) != ["d.js"]:
            bad.append(f"short block go refs {refs}")
        if P.path_hit("index.js", "at x (/work/lib/index.js:1:2)"):
            bad.append("index.js hit by lib/index.js")
        if not P.path_hit("lib/index.js", "at x (/work/lib/index.js:1:2)"):
            bad.append("lib/index.js missed")
        if P.path_hit("a.js", "error in data.js") or P.path_hit("q.js", "see q.json"):
            bad.append("substring path hit")
        if bad:
            return "FAIL: " + "; ".join(bad)
        return "ok (short names: only exact require/call/member uses count; paths match on word edges only)"


def config_case():
    want = {"eslint.config.mjs": "config", ".eslintrc.js": "config", "Gruntfile.js": "config",
            "vite.config.ts": "config", "lib/config.js": "code", "lib/router.js": "code",
            "test/app.js": "test", "README.md": "doc", "logo.png": "asset"}
    bad = [f"{k}={P.kind_of(k)}" for k, v in want.items() if P.kind_of(k) != v]
    return "FAIL: " + ", ".join(bad) if bad else "ok (config written as code is config, never code bloat)"


def hang_note_case():
    from .engine import partial_failures
    out = "  ok\n  1) Router hangs\n  2) app.get\n  not ok 3 - thing\n"
    n = partial_failures(out)
    return f"ok (3 failures seen before the cap, label stays TIMED OUT)" if n == 3 else f"FAIL: {n}"


def memory_case():
    """A job over the memory cap is killed; a small one still runs."""
    with tempfile.TemporaryDirectory() as d:
        box = Lockbox(memory="128M")
        big = box.run(d, "python3 -c \"b = bytearray(600 * 1024 * 1024); b[::4096] = b'x' * len(b[::4096]); print('ALLOC_OK')\"", 60)
        small = box.run(d, "python3 -c \"b = bytearray(16 * 1024 * 1024); print('ALLOC_OK')\"", 60)
    big_ok = big["rc"] != 0 and "ALLOC_OK" not in big["output"]
    small_ok = small["rc"] == 0 and "ALLOC_OK" in small["output"]
    if big_ok and small_ok:
        return f"ok (128M cap: 600 MB job killed rc={big['rc']}; 16 MB job ran)"
    return f"FAIL: big rc={big['rc']} small rc={small['rc']} {big['output'][-200:]!r}"


DEMO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demos", "containment-demo")
ATTEMPTS = [
    ("network_ip", "Call out to 1.1.1.1:443"),
    ("network_dns", "Look up api.vultrinference.com"),
    ("write_host_home", "Write a file into the host user's home"),
    ("read_host_home", "List the host user's home"),
    ("read_ssh_keys", "Read ~/.ssh"),
    ("env_api_key", "Read VULTR_INFERENCE_API_KEY (set on the host)"),
    ("write_system_dir", "Write into /usr"),
    ("process_flood", "Start 600 processes (cap 256)"),
]


def containment_table(memory="256M"):
    """Containment demo: run the hostile demo project in the lockbox and check from the host side too."""
    import shutil
    import time as _t
    if not shutil.which("node"):
        return {"all_held": False, "table": "containment demo needs node", "rows": []}
    home = os.path.expanduser("~")
    canary = f"theseus-canary-{int(_t.time())}.txt"
    planted = "THESEUS_CANARY_" + hashlib.sha256(canary.encode()).hexdigest()[:24]
    had = os.environ.get("VULTR_INFERENCE_API_KEY")
    if not had:
        os.environ["VULTR_INFERENCE_API_KEY"] = planted  # a fake key, so the test proves the env is cut off
    rows = []
    try:
        box = Lockbox(memory=memory, tasks=256)
        with tempfile.TemporaryDirectory(prefix="theseus-contain-") as d:
            work = os.path.join(d, "copy")
            shutil.copytree(DEMO_DIR, work, symlinks=True)
            res = box.run(work, "FLOOD_N=600 node lib/telemetry.js " + shlex.quote(home) + " " + shlex.quote(canary), 60)
            hog = box.run(work, "node lib/hog.js 2048", 60)
        try:
            got = json.loads(res["output"].strip().splitlines()[-1])
        except Exception:
            got = {}
        for key, label in ATTEMPTS:
            v = got.get(key, "no answer (" + res["output"][-120:].replace("\n", " ") + ")")
            held = isinstance(v, str) and v.startswith("blocked")
            if key == "process_flood" and not box.scope:
                v, held = "skipped (no cgroup scope on this host; time cap still applies)", True
            rows.append({"attempt": label, "result": v, "held": held})
        on_host = os.path.exists(os.path.join(home, canary))
        rows.append({"attempt": "Host check: canary file in host home?", "result":
                     "ESCAPED: file exists" if on_host else "not there", "held": not on_host})
        hog_held = hog["rc"] != 0 and "HOG_DONE" not in hog["output"]
        held_mb = [ln for ln in hog["output"].splitlines() if ln.startswith("holding")]
        rows.append({"attempt": f"Memory hog: grab 2 GB (cap {memory})", "result":
                     (f"killed at the cap (rc {hog['rc']}" + (f", last: {held_mb[-1]}" if held_mb else "") + ")")
                     if hog_held else f"ESCAPED: rc {hog['rc']}", "held": hog_held})
    finally:
        if not had:
            os.environ.pop("VULTR_INFERENCE_API_KEY", None)
        stray = os.path.join(home, canary)
        if os.path.exists(stray):  # only if it escaped: remove our own canary, nothing else
            os.remove(stray)
    w = max(len(r["attempt"]) for r in rows)
    lines = [f"{'What the hostile code tried'.ljust(w)} | Held | What happened", "-" * (w + 40)]
    for r in rows:
        lines.append(f"{r['attempt'].ljust(w)} | {'yes ' if r['held'] else 'NO  '} | {r['result']}")
    ok = all(r["held"] for r in rows)
    lines.append("")
    lines.append("CONTAINMENT: " + ("HELD, every attempt above failed" if ok else "FAILED, see rows marked NO"))
    return {"all_held": ok, "rows": rows, "table": "\n".join(lines), "lockbox": box.describe()}


def recheck_label_case():
    """A cut that hung on the first scan and hangs again is consistent (inconclusive both times)."""
    from . import engine as E
    rows = [
        ((E.INCONCLUSIVE, [], None), (E.TIMED_OUT, [], None), E.HUNG_BOTH),
        ((E.TIMED_OUT, [], None), (E.TIMED_OUT, [], None), E.HUNG_BOTH),
        (("LOAD-BEARING", ["unit"], None), ("LOAD-BEARING", ["unit"], None), "repeatable"),
        (("NOT NEEDED", [], "proven-not-needed"), ("LOAD-BEARING", ["unit"], None), E.NOT_REPEATABLE),
    ]
    bad = [f"{w[0]}->{n[0]}: {E.recheck_result(w, n)}" for w, n, want in rows if E.recheck_result(w, n) != want]
    if E.recheck_result((E.INCONCLUSIVE, [], None), ("LOAD-BEARING", ["unit"], None)) == E.NOT_REPEATABLE:
        bad.append("hung once counted as flaky")
    return "FAIL: " + "; ".join(bad) if bad else \
        "ok (inconclusive then timed out again = inconclusive both times, not a flaky verdict)"


def silent_runner_case():
    """A runner named in the test command that prints no summary is warned about (jasmine-node on Node 22)."""
    from . import engine as E
    with tempfile.TemporaryDirectory(prefix="theseus-quiet-") as t:
        with open(os.path.join(t, "package.json"), "w") as f:
            json.dump({"scripts": {"test": "npm run unit && npm run aplus", "unit": "jasmine-node spec",
                                   "aplus": "promises-aplus-tests spec/aplus-adapter"}}, f)
        full = E.resolve_cmd(t, "npm test")
    mocha_counts = {"frameworks": ["mocha"], "passed": 872}
    q = E.silent_runners(full, mocha_counts)
    both = E.silent_runners(full, {"frameworks": ["mocha", "jasmine-node"]})
    plain = E.silent_runners("node --test --test-reporter=tap", {"frameworks": ["tap/node:test"]})
    if q == ["jasmine-node"] and both == [] and plain == [] and "jasmine-node spec" in full:
        return "ok (npm test -> jasmine-node + promises-aplus-tests; jasmine-node printed nothing: warned)"
    return f"FAIL: q={q} both={both} plain={plain} full={full!r}"


def _slow_project(t, n_files=6, sleep_s=20):
    files = {"test/slow.test.js": "const test = require('node:test')\n"
             f"test('slow', async () => {{ await new Promise(r => setTimeout(r, {sleep_s * 1000})) }})\n"}
    for i in range(n_files):
        files[f"lib/m{i}.js"] = f"module.exports = {i}\n"
    return _proj(t, files)


def kill_cleanup_case():
    """SIGTERM mid-scan kills the lockbox jobs and wipes every work copy; clean handles kill -9."""
    import signal
    import subprocess
    import sys
    import time as _t
    from . import engine as E
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory(prefix="theseus-kill-") as t:
        tgt = _slow_project(t, sleep_s=2)
        orc = os.path.join(t, "o.json")
        with open(orc, "w") as f:
            json.dump({"steps": [{"name": "unit", "timeout": 60, "cmd": "node --test --test-reporter=tap"}]}, f)
        out = os.path.join(t, "run")
        p = subprocess.Popen([sys.executable, os.path.join(root, "theseus.py"), "scan", tgt, "--oracle", orc,
                              "--out", out, "--workers", "4", "--no-coverage", "--baseline-runs", "3"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=root)
        work = os.path.join(out, "work")
        seen = 0
        for _ in range(200):
            _t.sleep(0.1)
            if os.path.isdir(work):
                seen = max(seen, len(os.listdir(work)))
                if seen >= 2 and any(n.startswith("w") for n in os.listdir(work)):
                    break
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=60)
        left = os.listdir(work) if os.path.isdir(work) else []
        _t.sleep(0.5)
        stray = subprocess.run(["pgrep", "-f", "bwrap.*" + out], capture_output=True, text=True).stdout.split()
        # kill -9 leaves copies; clean removes only OUT/work, and refuses a folder that is not a run
        fake = os.path.join(t, "fake")
        os.makedirs(os.path.join(fake, "runs"))
        os.makedirs(os.path.join(fake, "work", "w0001_0_x"))
        n = E.clean_run(fake)
        refused = False
        try:
            E.clean_run(tgt)
        except SystemExit:
            refused = True
        ok = rc == 143 and not left and not stray and seen >= 1 and n == 1 \
            and not os.path.exists(os.path.join(fake, "work")) and os.path.isdir(os.path.join(fake, "runs")) and refused
        if ok:
            return f"ok (SIGTERM with {seen} copies live: exit 143, 0 copies and 0 lockbox jobs left; clean removed 1, refused a non-run folder)"
        return f"FAIL: rc={rc} seen={seen} left={left} stray={stray} cleaned={n} refused={refused}"


class _MockLLM:
    """Local stand-in for Vultr Serverless Inference (OpenAI-compatible), to test the agent offline."""

    def __init__(self, mode="ok"):
        import http.server
        import threading
        self.mode = mode
        self.requests = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                outer.requests.append({"auth": self.headers.get("Authorization"), "body": body, "path": self.path})
                if outer.mode == "fail":
                    self.send_response(500)
                    self.end_headers()
                    return
                req = json.loads(body)
                user = req["messages"][-1]["content"]
                if "Parts:" in user:
                    ids = [r["id"] for r in json.loads(user.split("Parts:\n", 1)[1])]
                    text = json.dumps({"order": ["not/a/part.js"] + ids[::-1],
                                       "reasons": {ids[-1]: "looks like a leftover"}})
                else:
                    text = "This part is used by the code the tests run, so the tests broke without it."
                out = json.dumps({"model": req["model"], "choices": [{"message": {"content": text}}],
                                  "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def agent_case():
    """The agent orders and explains; it never changes a verdict, and secrets never reach it."""
    from . import agent as A
    from . import engine as E
    secret = "sk-" + "Z" * 30
    plant = "ghp_" + "Q" * 36
    files = {
        "lib.js": "function used() {\n  return 1\n}\n\nfunction spare() {\n  return 2\n}\n\nmodule.exports = { used }\n"
                  f"// token = {plant}\n",
        "extra.js": "module.exports = 5\n",
        "test/lib.test.js": "const test = require('node:test')\nconst assert = require('node:assert')\n"
                            "const lib = require('../lib')\ntest('used', () => { assert.strictEqual(lib.used(), 1) })\n",
    }
    orc = {"steps": [{"name": "unit", "timeout": 60, "cmd": "node --test --test-reporter=tap"}]}
    bad = []
    with tempfile.TemporaryDirectory(prefix="theseus-agent-") as t:
        tgt = _proj(t, files)
        _s0, plain = _scan(t, tgt, orc, "plain")
        mock = _MockLLM()
        try:
            bot = A.Agent(key=secret, base=mock.url, model="mock-model")
            ev = os.path.join(t, "ev.jsonl")
            s = E.Scan(tgt, orc, os.path.join(t, "agent"), workers=4, events=ev, planner=A.make_planner(bot))
            recs = s.run()
            A.explain_all(bot, recs, tgt, max_n=5)
            with_agent = {r["part"]["id"]: r for r in recs}
            v0 = {k: (r["verdict"], r.get("coverage")) for k, r in plain.items()}
            v1 = {k: (r["verdict"], r.get("coverage")) for k, r in with_agent.items()}
            if v0 != v1:
                bad.append(f"verdicts changed: {v0} vs {v1}")
            if not s.plan_info or not s.plan_info[0]["source"].startswith("agent"):
                bad.append(f"plan not from agent: {s.plan_info}")
            sent = " ".join(r["body"] for r in mock.requests)
            if secret in sent or plant in sent:
                bad.append("secret reached the model")
            if not all(r["auth"] == "Bearer " + secret for r in mock.requests):
                bad.append("key not sent as bearer header")
            lb = [r for r in recs if r["verdict"] == "LOAD-BEARING"]
            if not lb or not all((r.get("explain") or {}).get("ai") for r in lb[:5]):
                bad.append("no AI explanation on load-bearing parts")
            kinds = [json.loads(x)["kind"] for x in open(ev)]
            if kinds[:2] != ["start", "baseline"] or "plan" not in kinds or kinds.count("part") != len(recs):
                bad.append(f"events: {kinds}")
            d = bot.describe()
            if d["calls"] < 2 or d["tokens_in"] <= 0:
                bad.append(f"usage not tracked: {d}")
        finally:
            mock.close()
        # inference down: plain rule order, same verdicts, no crash
        down = _MockLLM("fail")
        try:
            bot2 = A.Agent(key=secret, base=down.url, model="mock-model")
            s2 = E.Scan(tgt, orc, os.path.join(t, "down"), workers=4, planner=A.make_planner(bot2))
            r2 = s2.run()
            if {r["part"]["id"]: r["verdict"] for r in r2} != {k: r["verdict"] for k, r in plain.items()}:
                bad.append("verdicts changed when inference was down")
            if "rule order" not in s2.plan_info[0]["source"]:
                bad.append(f"no fallback: {s2.plan_info}")
        finally:
            down.close()
    red = A.redact(f"Authorization: Bearer {secret} key={plant} mail me@example.com " + os.path.expanduser("~") + "/x")
    if secret in red or plant in red or "me@example.com" in red or os.path.expanduser("~") + "/" in red:
        bad.append("redact missed: " + red)
    return "FAIL: " + "; ".join(bad) if bad else (
        "ok (mock Vultr inference: order planned with a bad id dropped, same verdicts as no agent, "
        "AI explanations on breaks, key only in the header, planted tokens never sent, falls back when down)")
