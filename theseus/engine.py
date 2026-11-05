# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""The loop: copy, pull one plank, does it still sail, record, wipe, repeat."""
import json
import os
import re
import shutil
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import counts as C
from . import covmap
from . import parts as P
from . import runend as RE
from . import lockbox as LB
from .lockbox import Lockbox

TAIL = 40


# Test runners we can name in a command, and the summary family each one prints.
# Longest names first, so "jasmine-node" is not also read as "jasmine".
RUNNERS = [
    ("promises-aplus-tests", "mocha"), ("jasmine-node", "jasmine-node"), ("jasmine", "jasmine"),
    ("mocha", "mocha"), ("jest", "jest/vitest"), ("vitest", "jest/vitest"), ("pytest", "pytest"),
    ("node --test", "tap/node:test"), ("tap ", "tap/node:test"), ("go test", "go test"),
    ("unittest", "unittest"),
]
FAMILY_ALIASES = {"jasmine-node": {"jasmine-node", "jasmine"}, "jasmine": {"jasmine", "jasmine-node"}}


def resolve_cmd(target, cmd):
    """The command plus any npm script it calls (npm test / npm run X), read from package.json."""
    text = cmd
    try:
        scripts = json.load(open(os.path.join(target, "package.json"))).get("scripts", {})
    except (OSError, ValueError, AttributeError):
        return text
    seen = set()
    for _ in range(3):  # follow npm run chains a few levels
        names = re.findall(r"\bnpm\s+(?:run(?:-script)?\s+([\w:.-]+)|(test|t)\b)", text)
        added = False
        for run_name, test_name in names:
            k = run_name or "test"
            if k in scripts and k not in seen:
                seen.add(k)
                text += " && " + str(scripts[k])
                added = True
        if not added:
            break
    return text


def silent_runners(full_cmd, counts):
    """Runners named in the command whose summary never showed up (Q's jasmine-node on Node 22)."""
    rest = " " + full_cmd + " "
    named = []
    for token, fam in RUNNERS:
        if token in rest:
            named.append((token.strip(), fam))
            rest = rest.replace(token, " ")
    seen = set((counts or {}).get("frameworks") or [])
    out = []
    for token, fam in named:
        if not (FAMILY_ALIASES.get(fam, {fam}) & seen):
            out.append(token)
    return out


def named_runners(full_cmd):
    """Runner families named in a (resolved) command."""
    rest = " " + full_cmd + " "
    fams = set()
    for token, fam in RUNNERS:
        if token in rest:
            fams.add(fam)
            rest = rest.replace(token, " ")
    return fams


def stamp(seconds=True):
    return datetime.now().strftime("%m%d%Y%H%M%S" if seconds else "%m%d%Y%H%M")


def now_zoned():
    """Local time with its zone (fix 11)."""
    t = datetime.now().astimezone()
    s = t.strftime("%Y-%m-%d %H:%M %Z (UTC%z)")
    try:  # also say US Eastern time, so a UTC date is not misread
        from zoneinfo import ZoneInfo
        e = t.astimezone(ZoneInfo("America/New_York"))
        if e.utcoffset() != t.utcoffset():
            s += e.strftime(" = %b %-d, %-I:%M %p ET")
    except Exception:
        pass
    return s


def sanitize(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:120]


def _writable(root):
    for dp, dns, fns in os.walk(root):
        for n in dns + fns:
            p = os.path.join(dp, n)
            if not os.path.islink(p):
                os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)
    os.chmod(root, os.stat(root).st_mode | stat.S_IWUSR)


def wipe(path, inside=None):
    """Remove a copy Theseus made. With inside=, refuse unless realpath sits under it."""
    if inside is not None:
        real, root = os.path.realpath(path), os.path.realpath(inside)
        if os.path.islink(path) or not real.startswith(root + os.sep):
            raise PermissionError(f"theseus: refusing to wipe {path}: not a copy inside {inside}")
    if os.path.exists(path):
        _writable(path)
        shutil.rmtree(path)


def test_count(output):
    """Passing tests in one step's output, or None when no runner summary parses (unknown, never 0)."""
    c = C.parse(output)
    return None if c is None else c["passed"]


def judge(r, b):
    """Why one step counts as BROKEN against its baseline step b (empty list = it held).

    BROKEN means any of: non-zero exit, fewer passing tests, more skipped or
    pending, a test summary that vanished, more failing tests, or a new crash
    while loading code. A hang is not judged here (TIMED OUT).
    """
    why = []
    if r["rc"] != 0:
        why.append(f"exit code {r['rc']}")
    c = C.parse(r["output"])
    bc = (b or {}).get("counts")
    if bc:
        if c is None:
            why.append("test summary missing (baseline had one)")
        else:
            if c["passed"] < bc["passed"]:
                why.append(f"passing tests dropped {bc['passed']} -> {c['passed']}")
            if c["skipped"] > bc["skipped"]:
                why.append(f"skipped/pending rose {bc['skipped']} -> {c['skipped']}")
            if c["failed"] > bc["failed"]:
                why.append(f"failing tests rose {bc['failed']} -> {c['failed']}")
    le = C.load_errors(r["output"])
    if le > (b or {}).get("load_errors", 0):
        why.append("new load crash: " + (C.first_load_error(r["output"]) or "?"))
    return why, c


def signature(res):
    """What a result means: the verdict and which checks broke."""
    failing = sorted(r["name"] for r in res if not r["passed"])
    if any(r.get("timed_out") for r in res):
        return (TIMED_OUT, failing)
    return ("LOAD-BEARING" if failing else "NOT NEEDED", failing)


def outcome(res):
    """v0.2.3: what one run of a cut showed: pass, broken, silent, timed out, or inconclusive."""
    failing = [r for r in res if not r["passed"] and not r.get("early_inconclusive")]
    if any(not r.get("timed_out") and r.get("early_end") != RE.SILENT for r in failing):
        return RE.BROKEN
    if any(r.get("early_end") == RE.SILENT for r in failing):
        return RE.QUIET
    if any(r.get("timed_out") for r in res):
        return RE.HUNG
    if any(r.get("early_inconclusive") for r in res):
        return RE.UNSURE
    return RE.PASS


def partial_failures(output):
    """In a hung run, failures already printed before the cap (the label stays TIMED OUT)."""
    t = C.ANSI.sub("", output or "")
    n = len(re.findall(r"^\s+\d+\) \S", t, re.M)) + len(re.findall(r"^\s*not ok \d+", t, re.M))
    return n


TIMED_OUT = "TIMED OUT: needs recheck"
BASELINE_UNUSABLE = "baseline unusable: went silent or hung"
INCONCLUSIVE = "still timed out: inconclusive"
NOT_RUN = "NOT RUN: --max-minutes reached"
RECHECK_CAP_S = 600  # per-cut cap on the timed-out recheck pass (10 min)
COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*|#)")
PROVEN = "proven-not-needed"
UNTESTED = "UNTESTED: human call"
BASELINE_FAILED = "baseline failed"
FLAKY = "flaky baseline"


class MemWatch(threading.Thread):
    """Samples host memory in use so we can size a cloud box for the same job."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.start_used = self._used()
        self.peak_used = self.start_used

    @staticmethod
    def _used():
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0])
        return (info["MemTotal"] - info["MemAvailable"]) * 1024

    def run(self):
        while not self.stop.wait(1.0):
            self.peak_used = max(self.peak_used, self._used())


class Scan:
    def __init__(self, target, oracle, outdir, workers=8, blocks=True, log=print,
                 keep_copies=False, repeat=1, max_minutes=None, recheck_cap_s=RECHECK_CAP_S,
                 baseline_runs=3, allow_flaky=False, coverage=True, events=None, planner=None):
        self.target = os.path.realpath(target)
        self.oracle = oracle
        self.outdir = os.path.abspath(outdir)
        if os.path.exists(self.outdir):
            raise SystemExit(f"theseus: {self.outdir} already exists, refusing to overwrite")
        real_out = os.path.join(os.path.realpath(os.path.dirname(self.outdir)), os.path.basename(self.outdir))
        if real_out == self.target or real_out.startswith(self.target + os.sep):
            raise SystemExit("theseus: output must live outside the target")
        self.baseline_runs = max(3, int(baseline_runs))
        self.allow_flaky = allow_flaky
        self.use_coverage = coverage
        self.flaky = None
        self.cov = covmap.CoverageMap()
        self.cov_note = "not collected"
        self.base_steps = {}
        self.warnings = []
        os.makedirs(os.path.join(self.outdir, "runs"))
        self.workdir = os.path.join(self.outdir, "work")
        os.makedirs(self.workdir)
        self.workers = workers
        self.do_blocks = blocks
        self.keep_copies = keep_copies
        self.repeat = max(1, int(repeat))
        self.base_tests = 0
        self.cap_s = None  # when set, every oracle step gets this cap (recheck pass)
        self.recheck_cap_s = recheck_cap_s
        self.max_minutes = max_minutes  # None = run until done
        self.deadline = None
        if keep_copies:
            self.keepdir = os.path.join(self.outdir, "copies")
            os.makedirs(self.keepdir)
        self.log = log
        env_src = oracle.get("env_source")
        self.env_dirs = oracle.get("env_dirs", [])
        overlays = [(os.path.join(env_src, d), "/work/" + d) for d in self.env_dirs] if env_src else []
        self.box = Lockbox(memory=oracle.get("memory", "2G"), env=oracle.get("env"), overlays=overlays)
        self.copy_skip = set([".git"] + self.env_dirs + oracle.get("copy_skip", []))
        self.env_names = set(P.ENV_DEFAULT + self.env_dirs + oracle.get("env_extra", []))
        self.files = P.list_files(self.target, self.env_names)
        self._lock = threading.Lock()
        self._n = 0
        self.events_path = events
        self.planner = planner  # agent: orders the cuts only; never decides a verdict
        self.plan_info = []
        self.total_planned = 0
        self._watch_cache = {}

    def emit(self, kind, **data):
        """One JSON line per event, for the web app's live view. Paths stay relative."""
        if not self.events_path:
            return
        data["kind"] = kind
        data["t"] = round(time.time(), 2)
        line = json.dumps(data, default=str)
        with self._lock:
            with open(self.events_path, "a") as f:
                f.write(line + "\n")

    def _plan(self, plist, phase):
        if not self.planner or len(plist) < 2:
            return plist
        try:
            ordered, info = self.planner(plist, phase, self)
        except Exception as e:  # the plan is advice; a failed plan keeps the plain order
            ordered, info = plist, {"source": "rule order (planner error: " + type(e).__name__ + ")", "fallback": True}
        if sorted(p["id"] for p in ordered) != sorted(p["id"] for p in plist):
            ordered, info = plist, {"source": "rule order (planner answer did not match the parts list)", "fallback": True}
        info["phase"] = phase
        self.plan_info.append(info)
        self.emit("plan", phase=phase, source=info.get("source"), fallback=bool(info.get("fallback")),
                  fallback_chunks=info.get("fallback_chunks", 0), agent_tries=info.get("agent_tries"), first=[p["id"] for p in ordered[:10]],
                  reasons=info.get("reasons", {}))
        return ordered

    # one fresh copy per run; the target is only ever read
    def _copy(self, tag, where=None):
        if LB.STOPPING.is_set():
            raise RuntimeError("theseus: stopping, no new copies")
        d = os.path.join(where or self.workdir, tag)
        shutil.copytree(self.target, d, symlinks=True,
                        ignore=lambda _dir, names: [n for n in names if n in self.copy_skip])
        _writable(d)
        return d

    def watch_for(self, step):
        """Run-end rules for one oracle step (v0.2.3), or None for hard cap only.

        v0.2.3: the quiet limit is 3x the longest output gap in this step's baseline runs,
        between 15 s and 60 s (runend.quiet_limit).

        The quiet rule runs on test commands: steps whose command names a known test runner,
        or that set "quiet_rule": true. "quiet_rule": false turns it off for a step.
        The mocha summary rules run only when mocha is the only runner the step names.
        """
        name = step["name"]
        if name in self._watch_cache:
            return self._watch_cache[name]
        fams = named_runners(resolve_cmd(self.target, step["cmd"]))
        qr = step.get("quiet_rule")
        use_quiet = bool(fams) if qr is None else bool(qr)
        mocha = fams == {"mocha"}
        b = self.base_steps.get(name) or {}
        gap = b.get("max_quiet_s") or 0
        limit = RE.quiet_limit(gap)
        w = None
        if use_quiet or mocha:
            w = {"mocha": mocha, "quiet_s": limit if use_quiet else None, "no_summary_s": limit,
                 "runners": sorted(fams), "baseline_max_gap_s": round(float(gap), 2)}
        self._watch_cache[name] = w
        return w

    def _end_early(self, r, b):
        """Judge a step that the run-end rules ended early. Returns (broken_because, inconclusive_note)."""
        why = r["early_end"]
        b = b or {}
        bc = b.get("counts") or {}
        base_titles = b.get("failing_titles")
        if base_titles is None and bc and bc.get("failed", 0) == 0:
            base_titles = []  # the baseline had zero failing tests: its failing set is empty, exactly
        if why == RE.SILENT:
            return ([RE.SILENT + f": no output for {self.watch_for({'name': r['name'], 'cmd': r['cmd']})['quiet_s']:.0f} s "
                     "(counted broken under the quiet rule, not proven by a failing test)"], None)
        new, basis, det = RE.guard(r["output"], base_titles, bc.get("failed", 0))
        r["guard"] = {"basis": basis, **det}
        if new:
            if basis == "titles":
                msg = f"{why}: {det['new']} failing tests not failing at baseline (matched by full title)"
            else:
                msg = f"{why}: {det['failing']} failing vs {det['baseline_failing']} at baseline ({basis})"
            return [msg], None
        return [], f"{why}, but no failure beyond the baseline's own: inconclusive"

    def _oracle(self, d, judged=True, cap_s=None, **box_kw):
        results = []
        for step in self.oracle["steps"]:
            cap = cap_s or self.cap_s or step.get("timeout", 300)
            watch = self.watch_for(step) if judged else None  # the baseline is never cut short
            r = self.box.run(d, step["cmd"], cap, watch=watch, **box_kw)
            r["name"] = step["name"]
            if judged:
                b = self.base_steps.get(step["name"])
                r["broken_because"], r["counts"] = judge(r, b)
                if r.get("early_end"):
                    r["other_signals"] = [x for x in r["broken_because"] if not x.startswith("exit code")]
                    r["broken_because"], note = self._end_early(r, b)
                    if note:
                        r["early_inconclusive"] = note
                    r["evidence"] = RE.evidence(r["output"])
            else:
                r["broken_because"], r["counts"] = ([f"exit code {r['rc']}"] if r["rc"] else []), C.parse(r["output"])
            r["passed"] = not r["broken_because"] and not r.get("timed_out") and not r.get("early_inconclusive")
            results.append(r)
            if not r["passed"] and self.oracle.get("stop_on_fail", False):
                break
        return results

    def _one_baseline(self, i, cov=False):
        d = self._copy(f"baseline{i}_" + stamp())
        kw = {}
        try:
            if cov:
                covdir = os.path.join(self.outdir, "coverage")
                hook = os.path.join(self.outdir, "pyhook")
                os.makedirs(os.path.join(covdir, "node"), exist_ok=True)
                os.makedirs(os.path.join(covdir, "py"), exist_ok=True)
                covmap.write_py_hook(hook)
                kw = {"extra_env": covmap.env_for_coverage(), "binds": [(covdir, covmap.COV_INSIDE)],
                      "ro_binds": [(hook, covmap.PY_HOOK_INSIDE)]}
            return self._oracle(d, judged=False, **kw)
        finally:
            wipe(d, inside=self.workdir)

    @staticmethod
    def _shape(res):
        return [(r["name"], r["rc"] == 0, bool(r.get("timed_out")),
                 None if r["counts"] is None else (r["counts"]["passed"], r["counts"]["failed"], r["counts"]["skipped"]))
                for r in res]

    def baseline(self):
        """Run the untouched copy several times side by side, plus once with coverage on.

        If the plain runs disagree, the project is flaky: no bloat calls.
        """
        t0 = time.time()
        n = self.baseline_runs
        with ThreadPoolExecutor(n + (1 if self.use_coverage else 0)) as ex:
            futs = [ex.submit(self._one_baseline, i) for i in range(n)]
            cfut = ex.submit(self._one_baseline, n, True) if self.use_coverage else None
            runs = [f.result() for f in futs]
            cres = cfut.result() if cfut else None
        res = runs[0]
        shapes = [self._shape(r) for r in runs]
        self.flaky = None
        if any(sh != shapes[0] for sh in shapes):
            self.flaky = {"runs": n, "shapes": [[list(x) for x in sh] for sh in shapes]}
        passed = all(r["rc"] == 0 and not r.get("timed_out") for run in runs for r in run)
        steps = []
        for k, r in enumerate(res):
            le = max(C.load_errors(run[k]["output"]) for run in runs if k < len(run))
            steps.append({"name": r["name"], "cmd": r["cmd"], "rc": r["rc"], "seconds": r["seconds"],
                          "seconds_all_runs": [run[k]["seconds"] for run in runs if k < len(run)],
                          "max_quiet_s": max(run[k].get("max_quiet_s", 0) for run in runs if k < len(run)),
                          "timed_out_any": any(run[k].get("timed_out") for run in runs if k < len(run)),
                          "failing_titles": sorted(RE.failing_titles(r["output"]) or []),
                          "tests": None if r["counts"] is None else r["counts"]["passed"],
                          "counts": r["counts"], "load_errors": le,
                          "tail": r["output"].splitlines()[-TAIL:] if r["rc"] != 0 else []})
        self.base_steps = {st["name"]: st for st in steps}
        self._watch_cache = {}
        self.baseline_unusable = []
        for st in steps:
            w = self.watch_for(st)
            if st.get("timed_out_any"):
                self.baseline_unusable.append(f"step {st['name']}: the baseline hit its time cap (hung)")
            elif w and w.get("quiet_s") and st.get("max_quiet_s", 0) >= w["quiet_s"]:
                self.baseline_unusable.append(f"step {st['name']}: the baseline went silent for {st['max_quiet_s']:.0f} s, "
                                              "so the quiet rule cannot be trusted on it")
            elif w and w.get("quiet_s") and RE.QUIET_GAP_MULT * st.get("max_quiet_s", 0) > RE.QUIET_CEIL_S:
                self.warnings.append(f"step {st['name']}: the longest baseline output gap was {st['max_quiet_s']:.1f} s, "
                                     f"so the {RE.QUIET_CEIL_S:.0f} s ceiling gives less than a 3x margin")
        self.quiet_limits = {st["name"]: {"quiet_limit_s": (self.watch_for(st) or {}).get("quiet_s"),
                                          "baseline_max_gap_s": round(float(st.get("max_quiet_s") or 0), 2)}
                             for st in steps}
        for st in steps:
            quiet = silent_runners(resolve_cmd(self.target, st["cmd"]), st["counts"])
            if quiet:
                st["silent_runners"] = quiet
                self.warnings.append(
                    f"step {st['name']}: {', '.join(quiet)} is in the command but printed no test summary we can read. "
                    + ("Its tests may not be running; the count comes from the other runners only."
                       if st["counts"] else "This step is judged on exit code and load crashes only."))
        # coverage: only trusted if the coverage run matched the plain runs
        if cres is not None:
            if self._shape(cres) == shapes[0]:
                self.cov.add_node(os.path.join(self.outdir, "coverage", "node"), self.target)
                self.cov.add_py(os.path.join(self.outdir, "coverage", "py"))
                self.cov_note = ("; ".join(self.cov.tools) + f"; {len(self.cov.loaded)} project files ran"
                                 if self.cov.tools else "collected, but no project code reported (no Node or Python ran)")
            else:
                self.cov_note = "coverage run disagreed with the plain runs: coverage not used"
                self.warnings.append(self.cov_note)
        elif not self.use_coverage:
            self.cov_note = "off (--no-coverage)"
        self._never_ran_tests()
        known = [st["tests"] for st in steps if st["tests"] is not None]
        self.base = {
            "passed": passed,
            "runs": n,
            "flaky": self.flaky,
            "seconds": round(time.time() - t0, 2),
            "coverage": self.cov_note,
            "steps": steps,
            "unusable": self.baseline_unusable,
            "quiet_limits": self.quiet_limits,
            "max_runs_per_part": RE.MAX_RUNS,
        }
        self.base_tests = sum(known) if known else None
        return self.base

    def _never_ran_tests(self):
        """Warn about test files the baseline never executed (they guard nothing)."""
        if not self.cov.tools:
            return
        dead = [f for f in self.files if P.is_test_file(f) and self.cov.available_for(f)
                and not self.cov.file_ran(f) and P.kind_of(f) == "test"
                and os.path.splitext(f)[1].lower() in P.CODE_EXT]
        self.never_ran_tests = dead
        if dead:
            self.warnings.append(f"{len(dead)} test code files never ran at baseline, so they guard nothing: "
                                 + ", ".join(dead[:8]) + (" ..." if len(dead) > 8 else ""))
        idle = [f for f in self.files if P.is_test_file(f) and P.kind_of(f) == "test"
                and os.path.splitext(f)[1].lower() in P.CODE_EXT and f not in dead
                and self.cov.file_ran(f) and self.cov.bodies_ran(f) is False]
        self.never_ran_tests = dead + idle
        if idle:
            self.warnings.append(f"{len(idle)} test files loaded but none of their functions ran, so their tests "
                                 "may never execute: " + ", ".join(idle[:8]) + (" ..." if len(idle) > 8 else ""))

    def load_coverage(self, covdir):
        self.cov.add_node(os.path.join(covdir, "node"), self.target)
        self.cov.add_py(os.path.join(covdir, "py"))
        self.cov_note = "; ".join(self.cov.tools) if self.cov.tools else "none found"

    def _blast(self, part, failing):
        hit = set()
        text = "\n".join(r["output"] for r in failing)
        for f in self.files:
            if f != part["file"] and P.path_hit(f, text):
                hit.add(f)
        errs = [ln.strip() for ln in text.splitlines()
                if re.search(r"error|Error|not ok|Could not|Cannot|failed", ln)][:12]
        return sorted(hit), errs

    def check(self, part):
        with self._lock:
            self._n += 1
            n = self._n
        started = datetime.now()
        if self.deadline and time.time() > self.deadline:
            rec = {"part": part, "removed": part["id"], "verdict": NOT_RUN, "steps": [],
                   "started": started.astimezone().isoformat(timespec="seconds"), "seconds": 0}
            self.log(f"[{n}] {NOT_RUN} {part['id']}")
            return rec
        # v0.2.3: rerun an unsure cut (silent, hung, inconclusive, mixed) up to RE.MAX_RUNS runs, never more
        want = min(max(self.repeat, 1), RE.MAX_RUNS)

        seen = []

        def once(i):
            # after a hang, the rerun gets the long recheck cap, so a slow cut is not mistaken for a hang
            cap = self.recheck_cap_s if RE.HUNG in seen else None
            got = self._cut_once(part, n, i, cap_s=cap)
            seen.append(outcome(got[0]))
            return seen[-1], got

        outs, tries = RE.run_until_sure(once, RE.MAX_RUNS)
        while len(tries) < want:  # --repeat asks for more runs (still capped at MAX_RUNS)
            o, got = once(len(tries))
            outs.append(o)
            tries.append(got)
        assert len(tries) <= RE.MAX_RUNS
        cls, settled = RE.settle(outs)
        pick = {"load_bearing": RE.BROKEN, "silent": RE.QUIET}.get(cls)
        if pick:
            k = outs.index(pick)
        elif cls == "flaky":
            k = next(i for i, o in enumerate(outs) if o != RE.PASS)
        else:
            k = 0
        res, kept = tries[k]
        sigs = [signature(r) for r, _ in tries]
        failing = [r for r in res if not r["passed"] and not r.get("early_inconclusive")]
        timed = [r["name"] for r in res if r.get("timed_out")]
        # v0.2.1: a proven break in any step is load-bearing even if another step hung
        proven = [r for r in failing if not r.get("timed_out") and r.get("early_end") != RE.SILENT]
        if proven:
            timed = []
        early_inc = [r for r in res if r.get("early_inconclusive")]
        rec = {
            "part": part,
            "removed": part["id"],
            "started": started.astimezone().isoformat(timespec="seconds"),
            "seconds": round((datetime.now() - started).total_seconds(), 2),
            "steps": [{"name": r["name"], "rc": r["rc"], "passed": r["passed"],
                       "seconds": r["seconds"], "timed_out": r["timed_out"],
                       "killed": r["killed"], "counts": r.get("counts"),
                       "early_end": r.get("early_end"), "left_running": r.get("left_running", 0),
                       "max_quiet_s": r.get("max_quiet_s"), "guard": r.get("guard"),
                       "evidence": r.get("evidence"), "other_signals": r.get("other_signals"),
                       "broken_because": r.get("broken_because", [])} for r in res],
        }
        rec["left_running"] = sum(r.get("left_running", 0) for rr, _ in tries for r in rr)
        rec["runs"] = {"n": len(outs), "max": RE.MAX_RUNS, "outcomes": outs, "settled": settled,
                       "shown_run": k + 1}
        if kept:
            rec["kept_copy"] = os.path.relpath(kept, self.outdir)
        if self.repeat > 1:
            same = all(s == sigs[0] for s in sigs)
            rec["repeat"] = {"n": self.repeat, "repeatable": same,
                             "results": [{"verdict": v, "failing": f} for v, f in sigs]}
            if not same:
                rec["flaky"] = "flaky, not repeatable"
        if cls in ("load_bearing", "silent", "flaky") and not failing:
            failing = [r for r in res if not r["passed"]] or res[:1]
        if cls in ("load_bearing", "silent", "flaky"):
            timed, early_inc = [], []
        if cls == "inconclusive" and timed:
            rec.update({
                "timed_out_steps": timed,
                "cap_seconds": self.cap_s or max(s.get("timeout", 300) for s in self.oracle["steps"]),
                "verdict": INCONCLUSIVE,
                "end_rule": f"no sure result in {len(outs)} runs",
                "notes": [f"{settled}: inconclusive, neither load-bearing nor not needed"],
                "tail": res[0]["output"].splitlines()[-TAIL:] if res else [],
            })
            pf = sum(partial_failures(r["output"]) for r in res)
            if pf:
                rec["partial_failures_before_cap"] = pf
                rec["notes"].append(f"{pf} test failures were already printed before the cap "
                                    "(label stays TIMED OUT; a hang is not a verdict)")
        elif failing:
            radius, errs = self._blast(part, failing)
            first = failing[0]
            ends = [r.get("early_end") for r in failing if r.get("early_end")]
            if proven and not proven[0].get("early_end"):
                end_rule = RE.CLEAN
            else:
                end_rule = (proven[0].get("early_end") if proven else None) or (ends[0] if ends else RE.CLEAN)
            rec["end_rule"] = end_rule
            if cls == "silent":
                end_rule = RE.SILENT
            elif cls == "flaky":
                end_rule = RE.FLAKY_LB
                rec["flaky"] = settled
            rec["end_rule"] = end_rule
            if end_rule == RE.SILENT:
                rec["notes"] = ["counted broken under the quiet rule, not proven by a failing test", settled]
            elif cls == "flaky":
                rec["notes"] = [settled]
            elif len(outs) > 1:
                rec["notes"] = [f"unsure on run 1, {settled} (run {k + 1} shown)"]
            ev = next((r.get("evidence") for r in failing if r.get("evidence")), None)
            if ev:
                rec["evidence"] = ev
            rec.update({
                "verdict": "LOAD-BEARING",
                "still_the_ship": False,
                "what_broke": {
                    "steps": [r["name"] for r in failing],
                    "why": {r["name"]: r.get("broken_because", []) for r in failing},
                    "first_failing_step": first["name"],
                    "error_lines": errs,
                    "tail": first["output"].splitlines()[-TAIL:],
                },
                "blast_radius_files": radius,
                "put_back": True,
                "is_test": part["kind"] == "test" or P.is_test_file(part["file"]),
            })
        elif cls == "inconclusive":
            rec.update({
                "verdict": INCONCLUSIVE,
                "end_rule": "killed early, inconclusive",
                "notes": [r["early_inconclusive"] for r in early_inc] + [settled],
                "evidence": early_inc[0].get("evidence") if early_inc else None,
                "tail": [RE.strip_home(x) for x in (early_inc or res)[0]["output"].splitlines()[-TAIL:]],
            })
        else:
            refs = P.references(self.target, self.files, part)
            cover, notes = self._classify(part, refs)
            rec.update({
                "verdict": "NOT NEEDED",
                "still_the_ship": True,
                "coverage": cover,
                "referenced_by": refs,
                "notes": notes,
            })
        name = f"run_remove-{sanitize(part['id'])}_{started.strftime('%m%d%Y%H%M%S')}"
        path = os.path.join(self.outdir, "runs", name + ".json")
        k = 2
        while os.path.exists(path):
            path = os.path.join(self.outdir, "runs", f"{name}_{k}.json")
            k += 1
        rec["run_name"] = os.path.basename(path)[:-5]
        with open(path, "w") as f:
            json.dump(rec, f, indent=2)
        self.log(f"[{n}] {rec['verdict']:12s} {part['id']}  ({rec['seconds']}s)")
        self.emit("part", n=n, total=self.total_planned, id=part["id"], level=part["level"], file=part["file"],
                  verdict=rec["verdict"], coverage=rec.get("coverage"), seconds=rec["seconds"],
                  why=(rec.get("what_broke") or {}).get("why"), radius=rec.get("blast_radius_files", [])[:20])
        return rec

    def _classify(self, part, refs):
        """Every check held. Is that proof, or just silence? Only proof is bloat."""
        notes = []
        f = part["file"]
        if part["kind"] == "test" or P.is_test_file(f):
            notes.append("test file: a check, not cargo; never bloat")
            if f in getattr(self, "never_ran_tests", []):
                notes.append("this test file never ran, or loaded but ran none of its functions, at baseline")
            return "is-a-check", notes
        if os.path.basename(f).upper().startswith(("LICENSE", "README", "NOTICE")) and part["level"] == "file":
            notes.append("keep for humans/legal even though the build does not need it")
        if part["kind"] in ("doc", "asset", "config"):
            notes.insert(0, f"{part['kind']}: the checks cannot judge it")
            return UNTESTED, notes
        if self.flaky:
            notes.insert(0, "baseline is flaky: no bloat calls")
            return UNTESTED, notes
        if not self.cov.available_for(f):
            notes.insert(0, "no coverage data for this file type, so no proof tests reach it")
            return UNTESTED, notes
        if part["level"] == "file":
            ran = self.cov.file_ran(f)
        else:
            try:
                lines = open(os.path.join(self.target, f), encoding="utf-8").read().split("\n")
            except (OSError, UnicodeDecodeError):
                lines = None
            ran = self.cov.block_ran(f, part["first"], part["last"], lines)
        if part["level"] == "file" and P.convention_loaded(f):
            notes.append("framework may load it by location (route/page/plugin)")
        if refs:
            notes.append("still named in " + ", ".join(refs[:5]) + (" ..." if len(refs) > 5 else ""))
        else:
            notes.append("nothing else names it")
        if ran:
            cold = []
            for rel, line, off in P.use_sites(self.target, self.files, part, refs):
                try:
                    if COMMENT_LINE.match(open(os.path.join(self.target, rel), encoding="utf-8").read().split("\n")[line]):
                        continue
                except (OSError, UnicodeDecodeError, IndexError):
                    continue
                if self.cov.site_ran(rel, line, off) is False:
                    cold.append(f"{rel}:{line + 1}")
            if cold:
                notes.insert(0, "the tests ran this code, but it is still used by code the tests never ran ("
                                + ", ".join(cold[:5]) + (" ..." if len(cold) > 5 else "")
                                + "): passing proves nothing about that use")
                return UNTESTED, notes
        if ran:
            notes.insert(0, "baseline tests ran this code, and every check still held after the cut "
                            "(same exit, same passing and skipped counts, no new load crash)")
            return PROVEN, notes
        notes.insert(0, "no test ran this code at baseline: passing proves nothing")
        return UNTESTED, notes

    def _cut_once(self, part, n, i, cap_s=None):
        """Fresh clean copy of the original, cut one part, run the checks."""
        when = stamp()
        if self.keep_copies:
            tag = f"remove-{sanitize(part['id'])[:80]}_{when}" + (f"_try{i + 1}" if self.repeat > 1 else "")
            d = self._copy(tag, where=self.keepdir)
            P.apply_removal(d, part)
            return self._oracle(d, cap_s=cap_s), d
        tag = f"w{n:04d}_{i}_{sanitize(part['id'])[:60]}"
        d = self._copy(tag)
        try:
            P.apply_removal(d, part)
            res = self._oracle(d, cap_s=cap_s)
        finally:
            wipe(d, inside=self.workdir)  # the copy is gone after every run; only the record stays
        return res, None

    def recheck_timeouts(self, recs):
        """Every timed-out cut gets one more fresh copy with a long cap (RECHECK_CAP_S)."""
        idx = [i for i, r in enumerate(recs) if r["verdict"] == TIMED_OUT]
        info = {"timed_out_first_pass": len(idx), "cap_seconds": self.recheck_cap_s,
                "confirmed": 0, "inconclusive": 0}
        if not idx:
            return recs, info
        self.log(f"recheck: {len(idx)} timed-out parts, cap {self.recheck_cap_s}s each")
        self.cap_s, saved_dl, self.deadline = self.recheck_cap_s, self.deadline, None
        try:
            again = self._pool([recs[i]["part"] for i in idx])
        finally:
            self.cap_s, self.deadline = None, saved_dl
        for i, r2 in zip(idx, again):
            first = {"verdict": TIMED_OUT, "steps": recs[i]["steps"], "run_name": recs[i].get("run_name")}
            r2["first_pass"] = first
            if r2["verdict"] == TIMED_OUT:
                r2["verdict"] = INCONCLUSIVE
                r2["end_rule"] = RE.HIT_CAP if self.recheck_cap_s == RECHECK_CAP_S else f"hit {self.recheck_cap_s} s cap"
                r2["notes"] = [f"timed out again at {self.recheck_cap_s}s: inconclusive"]
                info["inconclusive"] += 1
            else:
                r2.setdefault("notes", []).append("confirmed on recheck")
                r2["confirmed_on_recheck"] = True
                info["confirmed"] += 1
            recs[i] = r2
        return recs, info

    def _pool(self, plist):
        with ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(self.check, plist))

    def run(self):
        watch = MemWatch()
        watch.start()
        t0 = time.time()
        if self.max_minutes:
            self.deadline = t0 + 60 * float(self.max_minutes)
        self.log(f"theseus: {len(self.files)} parts at file level in {self.target}")
        self.emit("start", parts=len(self.files), workers=self.workers)
        base = self.baseline()
        self.emit("baseline", passed=base["passed"], runs=base["runs"], tests=self.base_tests,
                  flaky=bool(self.flaky), coverage=self.cov_note, warnings=self.warnings)
        stop = None
        if self.baseline_unusable:
            stop = BASELINE_UNUSABLE
            for x in self.baseline_unusable:
                self.warnings.append(x)
        elif not base["passed"]:
            stop = BASELINE_FAILED
        elif self.flaky and not self.allow_flaky:
            stop = FLAKY
        if stop:
            watch.stop.set()
            self.stopped = stop
            self.timing = {"total_seconds": round(time.time() - t0, 1), "phase1_seconds": 0, "runs": 0,
                           "workers": self.workers, "timeout_recheck": {}, "max_minutes": self.max_minutes,
                           "peak_host_mem_used_gb": round(watch.peak_used / 1024 ** 3, 2),
                           "extra_host_mem_during_scan_gb": round((watch.peak_used - watch.start_used) / 1024 ** 3, 2)}
            wipe(self.workdir, inside=self.outdir)  # fix 16: no empty work folder left behind
            self.log(f"theseus: stopped: {stop}. No cuts were made.")
            return []
        self.stopped = None
        self.log(f"baseline ok x{base['runs']} in {base['seconds']}s, tests {self.base_tests if self.base_tests is not None else 'unknown'}"
                 + (", FLAKY (bloat calls refused)" if self.flaky else "") + f"; coverage: {self.cov_note}")
        plist = self._plan([P.file_part(f) for f in self.files], "files")
        self.total_planned = len(plist)
        recs = self._pool(plist)
        phase1 = time.time() - t0
        if self.do_blocks:
            bearing = [r["part"]["file"] for r in recs
                       if r["verdict"] == "LOAD-BEARING" and r["part"]["kind"] == "code"
                       and not P.is_test_file(r["part"]["file"])]
            blist = []
            for f in bearing:
                blist += P.block_parts(self.target, f)
            self.log(f"phase 2: {len(blist)} top-level blocks inside {len(bearing)} load-bearing files")
            blist = self._plan(blist, "blocks")
            self.total_planned += len(blist)
            self.emit("phase", phase="blocks", parts=len(blist))
            recs += self._pool(blist)
        recs, rc_info = self.recheck_timeouts(recs)
        watch.stop.set()
        self.timing = {
            "total_seconds": round(time.time() - t0, 1),
            "phase1_seconds": round(phase1, 1),
            "runs": len(recs),
            "workers": self.workers,
            "timeout_recheck": rc_info,
            "reruns_per_part_max": RE.MAX_RUNS,
            "rerun_runs": sum(max(0, (r.get("runs") or {}).get("n", 1) - 1) for r in recs),
            "max_minutes": self.max_minutes,
            "peak_host_mem_used_gb": round(watch.peak_used / 1024 ** 3, 2),
            "extra_host_mem_during_scan_gb": round((watch.peak_used - watch.start_used) / 1024 ** 3, 2),
        }
        try:
            os.rmdir(self.workdir)
        except OSError:
            pass
        return recs


def stop_and_wipe(scan, code=143):
    """SIGTERM/SIGINT: kill every lockbox job, wipe this run's work copies, exit.

    Copies kept on purpose (--keep-copies) stay. Only OUT/work is wiped, and only inside OUT.
    """
    LB.kill_all()
    for _ in range(5):  # worker threads may still be finishing a copy
        try:
            if os.path.isdir(scan.workdir):
                wipe(scan.workdir, inside=scan.outdir)
            break
        except (OSError, shutil.Error):
            time.sleep(0.5)
    try:
        scan.emit("stopped", reason="signal", work_copies_left=os.path.isdir(scan.workdir))
    except Exception:
        pass
    os._exit(code)


def clean_run(outdir):
    """After a kill -9 (no handler runs): remove OUT/work left behind. Never anything else."""
    out = os.path.realpath(outdir)
    work = os.path.join(out, "work")
    if not os.path.isfile(os.path.join(out, "report.json")) and not os.path.isdir(os.path.join(out, "runs")):
        raise SystemExit("theseus: not a Theseus run folder (no runs/ or report.json): " + outdir)
    if not os.path.isdir(work):
        return 0
    n = len(os.listdir(work))
    wipe(work, inside=out)
    return n


HUNG_BOTH = "inconclusive both times"
NOT_REPEATABLE = "flaky, not repeatable"


def recheck_result(was, now):
    """Compare a first-scan row (verdict, failing, label) with its recheck.

    A first-scan "still timed out: inconclusive" and a recheck "TIMED OUT: needs recheck"
    are the same outcome: the cut hung both times. That is consistent, not flaky.
    """
    hung = (TIMED_OUT, INCONCLUSIVE)
    if was[0] in hung and now[0] in hung:
        return HUNG_BOTH
    if was[0] in hung or now[0] in hung:
        return "hung once, " + (now[0] if was[0] in hung else was[0]) + " the other time"
    return "repeatable" if tuple(was) == tuple(now) else NOT_REPEATABLE
