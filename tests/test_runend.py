# Theseus v0.2.3 run-end rules: unit tests. Made by Trey with Grok. MIT License.
# Run: python3 -B -m unittest tests.test_runend -v   (from the repo root)
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from theseus import runend as RE
from theseus import lockbox as LB

MOCHA_OUT = """
  app
    ✓ works
    1) should accept json

  res
    .send()
      2) should send text


  5 passing (40ms)
  1 pending
  2 failing

  1) app
       should accept json:
     ReferenceError: accepts is not defined
      at /home/someone/proj/lib/request.js:16:3

  2) res
       .send()
         should send text:
     Error: expected 200 "OK", got 500
"""


class Watcher(unittest.TestCase):
    def test_summary_grace_then_kill(self):
        w = RE.RunWatch(0.0, mocha=True, quiet_s=60)
        w.feed(MOCHA_OUT, 5.0)
        self.assertEqual(w.summary_failing, 2)
        self.assertIsNone(w.check(7.0))           # inside the 3 s grace
        self.assertEqual(w.check(8.1), RE.PRINTED_FAILURE)

    def test_grace_waits_while_output_flows_but_not_forever(self):
        w = RE.RunWatch(0.0, mocha=True, quiet_s=60)
        w.feed(MOCHA_OUT, 5.0)
        t = 5.0
        while t < 34.0:                           # failure list still printing
            t += 0.5
            w.feed("  more stack\n", t)
            if t < 35.0 - 0.01 and t - 5.0 < RE.GRACE_MAX_S:
                self.assertIsNone(w.check(t))
        self.assertEqual(w.check(35.1), RE.PRINTED_FAILURE)

    def test_zero_failing_summary_does_not_trigger(self):
        w = RE.RunWatch(0.0, mocha=True, quiet_s=60)
        w.feed("  10 passing (3ms)\n  0 failing\n", 1.0)
        self.assertIsNone(w.check(10.0))

    def test_failure_line_no_summary_60s(self):
        w = RE.RunWatch(0.0, mocha=True, quiet_s=None)
        w.feed("  suite\n    1) breaks\n", 2.0)
        for t in range(3, 62):
            w.feed("    ✓ next\n", float(t))
        self.assertIsNone(w.check(61.0))
        self.assertEqual(w.check(62.1), RE.NO_SUMMARY)

    def test_quiet_rule_from_last_byte(self):
        w = RE.RunWatch(0.0, mocha=False, quiet_s=60)
        w.feed("x\n", 30.0)
        self.assertIsNone(w.check(89.0))
        self.assertEqual(w.check(90.0), RE.SILENT)
        self.assertGreaterEqual(w.gap(90.0), 60.0)

    def test_quiet_rule_from_start_when_no_output(self):
        w = RE.RunWatch(100.0, quiet_s=60)
        self.assertIsNone(w.check(159.0))
        self.assertEqual(w.check(160.0), RE.SILENT)

    def test_non_mocha_ignores_summary(self):
        w = RE.RunWatch(0.0, mocha=False, quiet_s=None)
        w.feed(MOCHA_OUT, 1.0)
        self.assertIsNone(w.check(500.0))

    def test_split_chunks(self):
        w = RE.RunWatch(0.0, mocha=True)
        w.feed("  5 passing (1ms)\n  3 fai", 1.0)
        self.assertIsNone(w.summary_t)
        w.feed("ling\n", 1.5)
        self.assertEqual(w.summary_failing, 3)

    def test_quiet_limit(self):
        # v0.2.3: 3x the longest baseline output gap, floor 15 s, ceiling 60 s
        self.assertEqual(RE.quiet_limit(1.56), 15.0)
        self.assertEqual(RE.quiet_limit(8), 24.0)
        self.assertEqual(RE.quiet_limit(12), 36.0)
        self.assertEqual(RE.quiet_limit(25), 60.0)
        self.assertEqual(RE.quiet_limit(1e9), 60.0)
        self.assertEqual(RE.quiet_limit(None), 15.0)
        self.assertEqual(RE.quiet_limit(float("nan")), 15.0)
        self.assertEqual(RE.quiet_limit(-4), 15.0)


class Titles(unittest.TestCase):
    def test_full_titles(self):
        self.assertEqual(RE.failing_titles(MOCHA_OUT),
                         {"app should accept json", "res .send() should send text"})

    def test_no_summary(self):
        self.assertIsNone(RE.failing_titles("  1) a\n"))
        self.assertEqual(RE.failure_lines_count("  1) a\n  2) b\n"), 2)

    def test_guard_new_title(self):
        new, basis, det = RE.guard(MOCHA_OUT, ["app should accept json"], 1)
        self.assertTrue(new)
        self.assertEqual(basis, "titles")
        self.assertEqual(det["new_titles"], ["res .send() should send text"])

    def test_guard_same_titles_is_not_new(self):
        new, basis, _ = RE.guard(MOCHA_OUT, ["app should accept json", "res .send() should send text"], 2)
        self.assertFalse(new)
        self.assertEqual(basis, "titles")

    def test_guard_falls_back_to_count(self):
        out = "  3 passing (1ms)\n  4 failing\n\n  garbled\n"
        new, basis, det = RE.guard(out, [], 2)
        self.assertTrue(new)
        self.assertIn("count", basis)
        new, _, _ = RE.guard(out, None, 4)
        self.assertFalse(new)

    def test_strip_home_and_evidence(self):
        self.assertEqual(RE.strip_home("at /home/bob/x/y.js:1"), "at ~/x/y.js:1")
        self.assertEqual(RE.strip_home("HOME=/home/theseus"), "HOME=/home/theseus")  # sandbox HOME, not a host path
        ev = RE.evidence(MOCHA_OUT)
        self.assertLessEqual(len(ev), RE.EVIDENCE_MAX)
        self.assertFalse(any("/home/" in x for x in ev))
        self.assertTrue(any("ReferenceError" in x for x in ev))
        self.assertEqual(len(RE.evidence("\n".join(f"  {i}) t" for i in range(1, 99)))), 20)


class RealKill(unittest.TestCase):
    def test_kill_group_takes_sh_and_sleep(self):
        p = subprocess.Popen(["sh", "-c", "sleep 300 & sleep 300 & wait"], start_new_session=True)
        time.sleep(0.5)
        tree = LB._tree(p.pid)
        self.assertGreaterEqual(len(tree), 3)
        t0 = time.time()
        LB.kill_group(p)
        self.assertLess(time.time() - t0, 7)
        time.sleep(0.3)
        self.assertEqual(LB._still_running(tree), 0)

    def test_kill_group_sigkill_after_term_ignored(self):
        p = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 300"], start_new_session=True)
        time.sleep(0.5)
        tree = LB._tree(p.pid)
        t0 = time.time()
        LB.kill_group(p, term_wait=1.0)
        self.assertLess(time.time() - t0, 4)
        time.sleep(0.3)
        self.assertEqual(LB._still_running(tree), 0)


@unittest.skipUnless(shutil.which("bwrap"), "bwrap not installed")
class RealLockbox(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="theseus-ut-")
        self.box = LB.Lockbox(memory="512M")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_fake_mocha_hang_after_summary(self):
        with open(os.path.join(self.d, "fake.sh"), "w") as f:
            f.write("printf '  1 passing (2ms)\\n  1 failing\\n\\n  1) a\\n       b:\\n     Error: x\\n'\n"
                    "sleep 300 & sleep 300\n")
        t0 = time.time()
        r = self.box.run(self.d, "sh fake.sh", 120, watch={"mocha": True, "quiet_s": 60})
        took = time.time() - t0
        self.assertEqual(r["early_end"], RE.PRINTED_FAILURE)
        self.assertLess(took, 15)
        self.assertEqual(r["left_running"], 0)
        self.assertFalse(r["timed_out"])

    def test_silent_run(self):
        r = self.box.run(self.d, "echo start; sleep 300", 120, watch={"mocha": False, "quiet_s": 2})
        self.assertEqual(r["early_end"], RE.SILENT)
        self.assertLess(r["seconds"], 12)
        self.assertEqual(r["left_running"], 0)

    def test_no_watch_keeps_hard_cap(self):
        r = self.box.run(self.d, "echo hi; sleep 300", 3)
        self.assertIsNone(r["early_end"])
        self.assertTrue(r["timed_out"])
        self.assertEqual(r["left_running"], 0)

    def test_clean_run(self):
        r = self.box.run(self.d, "echo ok", 30, watch={"mocha": True, "quiet_s": 60})
        self.assertEqual(r["rc"], 0)
        self.assertIsNone(r["early_end"])
        self.assertIn("ok", r["output"])


class Reruns(unittest.TestCase):
    """v0.2.3: unsure cuts rerun, at most RE.MAX_RUNS (5) runs per part, never more."""

    def _drive(self, seq, max_runs=RE.MAX_RUNS):
        calls = []

        def once(i):
            calls.append(i)
            return seq(i), i
        outs, _ = RE.run_until_sure(once, max_runs)
        return outs, calls

    def test_cap_is_five(self):
        self.assertEqual(RE.MAX_RUNS, 5)

    def test_silent_forever_stops_at_five(self):
        outs, calls = self._drive(lambda i: RE.QUIET)
        self.assertEqual(len(calls), 5)
        self.assertEqual(RE.settle(outs)[0], "silent")
        self.assertIn("silent, counted broken", RE.settle(outs)[1])

    def test_cannot_loop_even_if_asked(self):
        for seq in (lambda i: RE.QUIET, lambda i: RE.HUNG, lambda i: RE.UNSURE,
                    lambda i: (RE.PASS, RE.QUIET)[i % 2]):
            for asked in (5, 6, 100, 10 ** 9):
                outs, calls = self._drive(seq, asked)
                self.assertLessEqual(len(calls), 5)
        self.assertFalse(RE.need_rerun([RE.QUIET] * 5, 10 ** 9))
        self.assertFalse(RE.need_rerun([RE.QUIET] * 7, 10 ** 9))

    def test_clean_pass_runs_once(self):
        outs, calls = self._drive(lambda i: RE.PASS)
        self.assertEqual(calls, [0])
        self.assertEqual(RE.settle(outs)[0], "not_needed")

    def test_proven_break_runs_once(self):
        outs, calls = self._drive(lambda i: RE.BROKEN)
        self.assertEqual(calls, [0])
        self.assertEqual(RE.settle(outs)[0], "load_bearing")

    def test_silent_then_broken_stops(self):
        outs, calls = self._drive(lambda i: (RE.QUIET, RE.BROKEN)[min(i, 1)])
        self.assertEqual(len(calls), 2)
        self.assertEqual(RE.settle(outs)[0], "load_bearing")

    def test_mixed_is_flaky_after_five(self):
        outs, calls = self._drive(lambda i: (RE.QUIET, RE.PASS)[min(i, 1)])
        self.assertEqual(len(calls), 5)
        cls, why = RE.settle(outs)
        self.assertEqual(cls, "flaky")
        self.assertIn("kept load-bearing", why)

    def test_not_needed_only_if_every_run_passes(self):
        self.assertEqual(RE.settle([RE.PASS] * 3)[0], "not_needed")
        for bad in (RE.QUIET, RE.HUNG, RE.UNSURE, RE.BROKEN):
            self.assertNotEqual(RE.settle([RE.PASS, RE.PASS, bad])[0], "not_needed")

    def test_hung_every_time_is_inconclusive(self):
        outs, calls = self._drive(lambda i: RE.HUNG)
        self.assertEqual(len(calls), 5)
        self.assertEqual(RE.settle(outs)[0], "inconclusive")


if __name__ == "__main__":
    unittest.main()
