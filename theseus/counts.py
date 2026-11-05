# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""Read test counts from runner output.

Knows mocha, jasmine (and jasmine-node), jest, vitest, tap, node:test,
pytest, go test (-v) and unittest. Every summary found is added up.
If nothing parses, the count is unknown (None), never 0.
"""
import re

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")


def _n(s):
    return int(s) if s else 0


def parse(output):
    """Return {"passed", "failed", "skipped", "frameworks"} or None if no runner summary was found."""
    text = ANSI.sub("", output or "")
    found = []  # (framework, passed, failed, skipped)

    # mocha: "  12 passing (30ms)", "  2 pending", "  1 failing"
    for m in re.finditer(r"^\s*(\d+) passing \(", text, re.M):
        tail = text[m.end():m.end() + 400]
        pend = re.match(r"[^\n]*\n\s*(\d+) pending\s*$", tail, re.M)
        fail = re.search(r"^\s*(\d+) failing\s*$", tail, re.M)
        found.append(("mocha", int(m.group(1)), _n(fail and fail.group(1)), _n(pend and pend.group(1))))
    if not any(f[0] == "mocha" for f in found):  # mocha with zero passing prints only "N failing"
        for m in re.finditer(r"^\s*(\d+) failing\s*$", text, re.M):
            found.append(("mocha", 0, int(m.group(1)), 0))

    # jasmine 2+: "10 specs, 1 failure, 2 pending specs"
    for m in re.finditer(r"^\s*(\d+) specs?, (\d+) failures?(?:, (\d+) pending specs?)?", text, re.M):
        specs, fails, pend = int(m.group(1)), int(m.group(2)), _n(m.group(3))
        found.append(("jasmine", specs - fails - pend, fails, pend))
    # jasmine-node 1.x: "10 tests, 25 assertions, 1 failure, 0 skipped"
    for m in re.finditer(r"^\s*(\d+) tests?, \d+ assertions?, (\d+) failures?, (\d+) skipped", text, re.M):
        tests, fails, skip = int(m.group(1)), int(m.group(2)), int(m.group(3))
        found.append(("jasmine-node", tests - fails - skip, fails, skip))

    # jest: "Tests:       1 failed, 2 skipped, 10 passed, 13 total"
    # vitest: "      Tests  1 failed | 10 passed | 2 skipped (13)"
    for m in re.finditer(r"^\s*Tests:?\s+((?:\d+ (?:passed|failed|skipped|todo|pending)[,| ]*)+)(?:\d+ total|\(\d+\))",
                         text, re.M):
        d = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|todo|pending)", m.group(1))}
        found.append(("jest/vitest", d.get("passed", 0), d.get("failed", 0),
                      d.get("skipped", 0) + d.get("todo", 0) + d.get("pending", 0)))

    # tap and node:test: "# pass 5" / "ℹ pass 5"
    tap = {}
    for k, v in re.findall(r"^(?:#|ℹ) (tests|pass|fail|skip|skipped|todo|cancelled) (\d+)\s*$", text, re.M):
        tap.setdefault(k, []).append(int(v))
    if tap:
        fail = sum(tap.get("fail", [])) + sum(tap.get("cancelled", []))
        skip = sum(tap.get("skip", [])) + sum(tap.get("skipped", [])) + sum(tap.get("todo", []))
        if "pass" in tap:
            passed = sum(tap["pass"])
        else:
            passed = max(0, sum(tap.get("tests", [])) - fail - skip)
        found.append(("tap/node:test", passed, fail, skip))

    # pytest: "==== 5 passed, 1 skipped in 0.12s ====" or "5 passed in 0.1s"
    for m in re.finditer(r"^=*\s*((?:\d+ (?:passed|failed|skipped|errors?|xfailed|xpassed|deselected|warnings?)(?:, )?)+)"
                         r" in [\d.]+s", text, re.M):
        d = {}
        for v, k in re.findall(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)", m.group(1)):
            d[k.rstrip("s") if k.startswith("error") else k] = d.get(k, 0) + int(v)
        found.append(("pytest", d.get("passed", 0) + d.get("xpassed", 0), d.get("failed", 0) + d.get("error", 0),
                      d.get("skipped", 0) + d.get("xfailed", 0)))

    # go test -v: "--- PASS: TestX (0.00s)"
    g = re.findall(r"^\s*--- (PASS|FAIL|SKIP): ", text, re.M)
    if g:
        found.append(("go test", g.count("PASS"), g.count("FAIL"), g.count("SKIP")))

    # unittest: "Ran 7 tests in 0.01s" then "OK (skipped=1)" or "FAILED (failures=1, errors=1)"
    for m in re.finditer(r"^Ran (\d+) tests? in [\d.]+s\s*\n\s*\n?(OK|FAILED)(?: \(([^)]*)\))?", text, re.M):
        d = {k: int(v) for k, v in re.findall(r"(\w[\w ]*?)=(\d+)", m.group(3) or "")}
        fail = d.get("failures", 0) + d.get("errors", 0) + d.get("unexpected successes", 0)
        skip = d.get("skipped", 0) + d.get("expected failures", 0)
        found.append(("unittest", int(m.group(1)) - fail - skip, fail, skip))

    if not found:
        return None
    return {
        "passed": sum(f[1] for f in found),
        "failed": sum(f[2] for f in found),
        "skipped": sum(f[3] for f in found),
        "frameworks": sorted({f[0] for f in found}),
    }


# A crash while loading code: rc may still be 0 if a runner swallows it.
LOAD_ERRORS = re.compile(
    r"Cannot find module|ERR_MODULE_NOT_FOUND|ERR_REQUIRE_ESM|ModuleNotFoundError|ImportError:|"
    r"SyntaxError:|ReferenceError: \S+ is not defined|"
    r"cannot import name|No module named")


def load_errors(output):
    return len(LOAD_ERRORS.findall(ANSI.sub("", output or "")))


def first_load_error(output):
    for ln in ANSI.sub("", output or "").splitlines():
        if LOAD_ERRORS.search(ln):
            return ln.strip()[:200]
    return None
