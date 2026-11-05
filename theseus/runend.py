# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""Run-end rules (v0.2.3): when a test run has done its job, end it and move on.

Trey's rules:
  (a) if a run breaks, it did its job: end that part and move on.
  (b) if a run goes quiet for about 60 s, count it as broken.
      v0.2.3 note: 60 s of silence is still always counted broken; the limit
      below can end it sooner (3x the longest baseline gap, 15 s floor).

Three early ends, checked while the test command runs:
  PRINTED_FAILURE  mocha printed its summary with M failing (M > 0). Wait a 3 s
                   grace, then kill. Mocha only.
  NO_SUMMARY       mocha printed a failure line, but no summary came within the
                   quiet limit after the first failure line (v0.2.3: 15-60 s).
                   Kill. Mocha only.
  SILENT           no new output for the quiet limit. v0.2.3: the limit is 3x the
                   longest output gap seen in the baseline runs of that step,
                   never under 15 s and never over 60 s. Kill. Counted broken
                   under the quiet rule, not proven by a failing test.

v0.2.3 reruns (Trey's rule): a cut whose run was silent, timed out, or inconclusive
is run again on a fresh copy, up to MAX_RUNS (5) runs in total for that part, never more.
  - every run passes cleanly                 -> not needed (the only way to get it)
  - a proven failure, and no run passed      -> load-bearing
  - silent (or hung) every time, none passed -> load-bearing, "silent, counted broken"
  - some runs pass and some do not           -> "flaky", kept load-bearing (never safe to cut)
  - hung or inconclusive every time          -> inconclusive (a hang alone is not a verdict)
The hard cap (the step timeout, or 600 s on recheck) stays as a backstop.
The baseline is never cut short by these rules.
"""
import re

PRINTED_FAILURE = "printed failure, killed early"
NO_SUMMARY = "failed, summary never printed"
SILENT = "silent, counted broken"
HIT_CAP = "hit 600 s cap"
CLEAN = "clean load-bearing"

GRACE_S = 3.0        # after the mocha summary
NO_SUMMARY_S = 60.0  # after the first failure line, if no summary
QUIET_FLOOR_S = 15.0    # v0.2.3: shortest quiet limit
QUIET_CEIL_S = 60.0     # v0.2.3: longest quiet limit
QUIET_GAP_MULT = 3.0    # v0.2.3: limit = 3x the longest baseline output gap
MAX_RUNS = 5            # v0.2.3: hard cap on runs per part, first run included
FLAKY_LB = "flaky"
FLOWING_S = 1.0      # do not kill while the failure list is still printing
GRACE_MAX_S = 30.0   # but never wait longer than this after the summary
EVIDENCE_MAX = 20

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")
FAIL_LINE = re.compile(r"^\s+\d+\) \S")
SUMMARY_FAILING = re.compile(r"^\s*(\d+) failing\s*$")
# /home/theseus is the empty sandbox HOME inside the lockbox, not a host path, so it stays
HOME_PATH = re.compile(r"/home/(?!theseus(?:[/\s'\"`<>:)]|$))[^/\s'\"`<>:)]+")


def strip_home(s):
    """Replace /home/<name> with ~ so no host home path leaves the box."""
    return HOME_PATH.sub("~", s or "")


def quiet_limit(baseline_gap_s):
    """v0.2.3: 3x the longest output gap in the baseline runs, kept between 15 s and 60 s."""
    try:
        g = float(baseline_gap_s or 0)
    except (TypeError, ValueError):
        g = 0.0
    if g != g or g < 0:  # NaN or negative: treat as no gap seen
        g = 0.0
    return round(min(QUIET_CEIL_S, max(QUIET_FLOOR_S, QUIET_GAP_MULT * g)), 1)


# v0.2.3: one run of a cut ends in one of these
PASS, BROKEN, QUIET, HUNG, UNSURE = "pass", "broken", "silent", "timed out", "inconclusive"
SURE = (PASS, BROKEN)


def need_rerun(outcomes, max_runs=MAX_RUNS):
    """Run this part again? Never once MAX_RUNS runs are done, whatever max_runs says."""
    cap = min(int(max_runs), MAX_RUNS)
    if len(outcomes) >= cap:
        return False
    if all(o == PASS for o in outcomes):
        return False  # clean pass: done
    if PASS not in outcomes and BROKEN in outcomes:
        return False  # a proven failure and no pass: load-bearing, done
    return True       # silent, hung, inconclusive, or mixed: run again


def run_until_sure(run_once, max_runs=MAX_RUNS):
    """Call run_once(i) until the outcome is sure or MAX_RUNS runs are done.

    run_once returns (outcome, payload). The loop is a bounded for loop, so it cannot spin.
    """
    outs, pay = [], []
    for i in range(min(int(max_runs), MAX_RUNS)):
        o, p = run_once(i)
        outs.append(o)
        pay.append(p)
        if not need_rerun(outs, max_runs):
            break
    return outs, pay


def settle(outcomes):
    """(class, why) for a part from all its run outcomes. Class is one of
    not_needed, load_bearing, silent, flaky, inconclusive."""
    n = len(outcomes)
    if n and all(o == PASS for o in outcomes):
        return "not_needed", f"passed cleanly in {n} of {n} runs"
    if PASS in outcomes:
        k = outcomes.count(PASS)
        return "flaky", f"{FLAKY_LB}: passed in {k} of {n} runs, not the others; kept load-bearing (never safe to cut)"
    if BROKEN in outcomes:
        return "load_bearing", f"proven failure in {outcomes.count(BROKEN)} of {n} runs"
    if QUIET in outcomes:
        return "silent", f"{SILENT} in {outcomes.count(QUIET)} of {n} runs" + (
            f", hung in {outcomes.count(HUNG)}" if HUNG in outcomes else "")
    return "inconclusive", f"no sure result in {n} runs (hung or inconclusive every time)"


class RunWatch:
    """Watches one test command's output. Pure logic: the caller feeds bytes and times.

    mocha: use the mocha summary and failure-line rules (mocha only).
    quiet_s: the quiet limit, or None for no quiet rule.
    """

    def __init__(self, start_t, mocha=False, quiet_s=None, grace_s=GRACE_S, no_summary_s=NO_SUMMARY_S):
        self.mocha = bool(mocha)
        self.quiet_s = quiet_s
        self.grace_s = grace_s
        self.no_summary_s = no_summary_s
        self.start_t = start_t
        self.last_out_t = start_t
        self.max_gap_s = 0.0
        self.summary_t = None
        self.summary_failing = None
        self.first_fail_t = None
        self._partial = ""

    def feed(self, text, t):
        if not text:
            return
        self.max_gap_s = max(self.max_gap_s, t - self.last_out_t)
        self.last_out_t = t
        if not self.mocha:
            return
        buf = self._partial + ANSI.sub("", text)
        lines = buf.split("\n")
        self._partial = lines.pop()  # last piece may be an unfinished line
        for ln in lines:
            self._line(ln, t)

    def _line(self, ln, t):
        if self.summary_t is None:
            m = SUMMARY_FAILING.match(ln)
            if m and int(m.group(1)) > 0:
                self.summary_t = t
                self.summary_failing = int(m.group(1))
                return
        if self.first_fail_t is None and FAIL_LINE.match(ln):
            self.first_fail_t = t

    def gap(self, t):
        return max(self.max_gap_s, t - self.last_out_t)

    def check(self, t):
        """The early-end reason now, or None to keep running."""
        if self.mocha and self.summary_t is not None:
            since = t - self.summary_t
            if (since >= self.grace_s and t - self.last_out_t >= FLOWING_S) or since >= GRACE_MAX_S:
                return PRINTED_FAILURE
            return None  # summary seen: only the grace decides now
        if self.mocha and self.first_fail_t is not None and t - self.first_fail_t >= self.no_summary_s:
            return NO_SUMMARY
        if self.quiet_s and t - self.last_out_t >= self.quiet_s:
            return SILENT
        return None


def _after_summary(text):
    """Text after mocha's 'M failing' line, where it lists each failure with its full title."""
    t = ANSI.sub("", text or "")
    m = None
    for m in re.finditer(r"^\s*(\d+) failing\s*$", t, re.M):
        pass
    return t[m.end():] if m else None


def failing_titles(text):
    """Full titles of mocha's failing tests, from the list after the summary.

    Mocha prints '  1) suite' then nested lines, and the last title line ends with ':'.
    Returns a set of titles, or None if there is no summary or no title parsed.
    """
    tail = _after_summary(text)
    if tail is None:
        return None
    titles = set()
    lines = tail.split("\n")
    i = 0
    while i < len(lines):
        m = re.match(r"^\s+\d+\) (.*\S)\s*$", lines[i])
        if not m:
            i += 1
            continue
        parts = [m.group(1)]
        j = i
        while not parts[-1].endswith(":") and j + 1 < len(lines) and j - i < 12:
            j += 1
            nxt = lines[j].strip()
            if not nxt:
                break
            parts.append(nxt)
        if parts[-1].endswith(":"):
            parts[-1] = parts[-1][:-1]
            titles.add(" ".join(parts))
        i = j + 1
    return titles or None


def failure_lines_count(text):
    """Failure markers mocha printed ('  N) title'), with or without a summary."""
    t = ANSI.sub("", text or "")
    return len(set(int(x) for x in re.findall(r"^\s+(\d+)\) \S", t, re.M)))


def guard(text, base_titles, base_failed):
    """Baseline guard for a killed-early run.

    Match failing tests by full title against the baseline's failing set. If the titles
    cannot be parsed (or the baseline has none recorded), fall back to a count above the
    baseline. Returns (new_failures_found, basis, detail).
    """
    titles = failing_titles(text)
    if titles is not None and base_titles is not None:
        new = sorted(titles - set(base_titles))
        return bool(new), "titles", {"failing": len(titles), "new": len(new), "new_titles": new[:EVIDENCE_MAX]}
    tail = _after_summary(text)
    if tail is not None:
        m = None
        for m in re.finditer(r"^\s*(\d+) failing\s*$", ANSI.sub("", text), re.M):
            pass
        n = int(m.group(1))
    else:
        n = failure_lines_count(text)
    bf = int(base_failed or 0)
    return n > bf, "count above baseline (titles not parsed)", {"failing": n, "baseline_failing": bf}


def evidence(text, limit=EVIDENCE_MAX):
    """At most ~20 captured failure lines, with /home paths stripped."""
    t = ANSI.sub("", text or "")
    keep = []
    for ln in t.split("\n"):
        s = ln.rstrip()
        if not s.strip():
            continue
        if (FAIL_LINE.match(s) or SUMMARY_FAILING.match(s) or re.match(r"^\s*\d+ passing \(", s)
                or re.search(r"\w*Error\b|^\s*not ok\b", s)):
            keep.append(strip_home(s.strip())[:240])
        if len(keep) >= limit:
            break
    return keep
