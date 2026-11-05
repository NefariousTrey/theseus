# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""Which parts did the baseline tests actually run?

No installs needed:
- Node: NODE_V8_COVERAGE (built into Node 10.12+), the same data c8 reads.
- Python 3.12+: a tiny sitecustomize that records lines with sys.monitoring.
Data goes to a separate folder bound at /theseus-cov, never into the copy.
"""
import json
import os
import re

COV_INSIDE = "/theseus-cov"
PY_HOOK_INSIDE = "/theseus-pyhook"

SITECUSTOMIZE = r'''
# Theseus coverage hook (read-only, only active when THESEUS_PYCOV is set).
import os, sys
_d = os.environ.get("THESEUS_PYCOV")
if _d and hasattr(sys, "monitoring"):
    import atexit, json
    _M = sys.monitoring
    _T = _M.COVERAGE_ID
    _hits = {}
    def _line(code, line):
        f = code.co_filename
        if f.startswith("/work/"):
            _hits.setdefault(f, set()).add(line)
        return _M.DISABLE
    try:
        _M.use_tool_id(_T, "theseus")
        _M.register_callback(_T, _M.events.LINE, _line)
        _M.set_events(_T, _M.events.LINE)
        def _dump():
            try:
                os.makedirs(_d, exist_ok=True)
                p = os.path.join(_d, "py-%d-%s.json" % (os.getpid(), os.urandom(4).hex()))
                with open(p, "w") as fh:
                    json.dump({k: sorted(v) for k, v in _hits.items()}, fh)
            except Exception:
                pass
        atexit.register(_dump)
    except Exception:
        pass
'''

NODE_EXT = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx"}
HEADER = re.compile(r"^\s*(@|def\s|async\s+def\s|class\s)")
COMMENT = re.compile(r"^\s*(//|/\*|\*|#)")


def write_py_hook(d):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "sitecustomize.py"), "w") as f:
        f.write(SITECUSTOMIZE)


def env_for_coverage():
    return {"NODE_V8_COVERAGE": COV_INSIDE + "/node", "THESEUS_PYCOV": COV_INSIDE + "/py",
            "PYTHONPATH": PY_HOOK_INSIDE}


def _rel(url):
    if url.startswith("file://"):
        url = url[7:]
    if not url.startswith("/work/"):
        return None
    return url[len("/work/"):]


def _line_starts(text):
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _line_of(starts, off):
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= off:
            lo = mid
        else:
            hi = mid - 1
    return lo


class CoverageMap:
    """lines_run[file] = set of 0-based lines that ran; funcs[file] = [(first_line, last_line, count)]."""

    def __init__(self):
        self.lines_run = {}
        self.funcs = {}
        self.loaded = set()
        self.tools = []
        self.chars_run = {}  # node only: per-character "ran" paint, so a branch on a line that ran still counts

    def to_json(self):
        return {"tools": self.tools, "files_seen": len(self.loaded)}

    # ---- Node -------------------------------------------------------
    def add_node(self, covdir, target):
        if not os.path.isdir(covdir):
            return
        n = 0
        for fn in sorted(os.listdir(covdir)):
            if not fn.endswith(".json"):
                continue
            try:
                data = json.load(open(os.path.join(covdir, fn)))
            except Exception:
                continue
            n += 1
            for script in data.get("result", []):
                rel = _rel(script.get("url", ""))
                if not rel or "node_modules/" in rel:
                    continue
                self._node_script(rel, script, target)
        if n:
            self.tools.append(f"node V8 coverage ({n} processes)")

    def _node_script(self, rel, script, target):
        try:
            text = open(os.path.join(target, rel), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            return
        funcs = script.get("functions", [])
        ranges = []
        for k, fn in enumerate(funcs):
            for j, r in enumerate(fn.get("ranges", [])):
                ranges.append((r["startOffset"], r["endOffset"], r["count"]))
        if not ranges:
            return
        if any(c > 0 for _s, _e, c in ranges):
            self.loaded.add(rel)
        # innermost range wins: paint biggest first, smaller on top
        paint = bytearray(len(text) + 1)
        known = bytearray(len(text) + 1)
        for s, e, c in sorted(ranges, key=lambda x: -(x[1] - x[0])):
            s, e = max(0, s), min(len(text), e)
            paint[s:e] = (b"\x01" if c > 0 else b"\x00") * (e - s)
            known[s:e] = b"\x01" * (e - s)
        if rel in self.chars_run:  # several processes: a char ran if it ran in any of them
            old = self.chars_run[rel]
            paint = bytearray(max(a, b) for a, b in zip(paint, old)) if len(old) == len(paint) else paint
        self.chars_run[rel] = paint
        starts = _line_starts(text)
        lines = text.split("\n")
        run = self.lines_run.setdefault(rel, set())
        for i, ln in enumerate(lines):
            if not ln.strip() or COMMENT.match(ln):
                continue
            a = starts[i]
            b = a + len(ln)
            if any(paint[x] and not ln[x - a].isspace() for x in range(a, b)):
                run.add(i)
        fl = self.funcs.setdefault(rel, [])
        for fn in funcs:
            rs = fn.get("ranges", [])
            if not rs:
                continue
            s, e, c = rs[0]["startOffset"], rs[0]["endOffset"], rs[0]["count"]
            if s == 0 and e >= len(text) - 1:
                continue  # the module wrapper itself
            fl.append((_line_of(starts, s), _line_of(starts, max(s, e - 1)), c))

    # ---- Python -----------------------------------------------------
    def add_py(self, covdir):
        if not os.path.isdir(covdir):
            return
        n = 0
        for fn in sorted(os.listdir(covdir)):
            try:
                data = json.load(open(os.path.join(covdir, fn)))
            except Exception:
                continue
            n += 1
            for path, lines in data.items():
                rel = _rel(path)
                if not rel:
                    continue
                self.loaded.add(rel)
                self.lines_run.setdefault(rel, set()).update(x - 1 for x in lines)
        if n:
            self.tools.append(f"python sys.monitoring ({n} processes)")

    # ---- questions --------------------------------------------------
    def available_for(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".py":
            return any(t.startswith("python") for t in self.tools)
        if ext in NODE_EXT:
            return any(t.startswith("node") for t in self.tools)
        return False

    def site_ran(self, path, line, offset):
        """Did the code at this exact spot run at baseline? None if coverage cannot see this file type."""
        if not self.available_for(path):
            return None
        if path not in self.loaded:
            return False
        if path.endswith(".py"):
            return line in self.lines_run.get(path, set())
        paint = self.chars_run.get(path)
        if paint is None or offset >= len(paint):
            return line in self.lines_run.get(path, set())
        return bool(paint[offset])

    def bodies_ran(self, path):
        """Node: did any function in this file run (not counting the module wrapper)? None if unknown."""
        fl = self.funcs.get(path)
        if path.endswith(".py") or not fl:
            return None
        return any(c > 0 for _a, _b, c in fl)

    def file_ran(self, path):
        return path in self.loaded

    def block_ran(self, path, first, last, text_lines=None):
        """Did the tests run code inside lines first..last (0-based, inclusive)?

        Node: if the block holds functions, at least one must have been called.
        Python: a def/class header runs at import, so only body lines count.
        """
        if path not in self.loaded:
            return False
        if path.endswith(".py"):
            run = self.lines_run.get(path, set())
            for i in range(first, last + 1):
                if i in run and not (text_lines and HEADER.match(text_lines[i])):
                    return True
            return False
        inside = [f for f in self.funcs.get(path, []) if first <= f[0] <= last]
        if inside:
            return any(c > 0 for _a, _b, c in inside)
        run = self.lines_run.get(path, set())
        return any(first <= i <= last for i in run)
