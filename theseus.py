#!/usr/bin/env python3
# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""Theseus: blast radius that leaves only what's load-bearing.

  theseus.py doctor                        can this machine run the lockbox?
  theseus.py selftest                      prove the lockbox and every rule hold
  theseus.py scan TARGET --oracle O.json   map every part's blast radius
  theseus.py scan TARGET --oracle O.json --agent    same, with the Vultr agent planning and explaining
  theseus.py recheck --report R --all      rerun exact cuts on fresh copies: are verdicts repeatable?
  theseus.py contain                       containment demo: what the lockbox blocks, as a table
  theseus.py clean --out RUNDIR            remove work copies a kill -9 left behind
  theseus.py apply --report R --dest NEW --approve   trimmed copy, human yes only
"""
import argparse
import json
import os
import signal
import subprocess
import sys

from theseus import __version__
from theseus import agent as AG
from theseus import engine, report, selftest
from theseus.apply import apply


def commit_of(target):
    try:
        return subprocess.run(["git", "-C", target, "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=20).stdout.strip() or None
    except Exception:
        return None


def cmd_scan(a):
    oracle = json.load(open(a.oracle))
    out = a.out or os.path.join(os.getcwd(), "theseus-scan-" + engine.stamp(False))
    s = engine.Scan(a.target, oracle, out, workers=a.workers, blocks=not a.files_only,
                    keep_copies=a.keep_copies, repeat=a.repeat, max_minutes=a.max_minutes,
                    recheck_cap_s=a.recheck_cap, baseline_runs=a.baseline_runs,
                    allow_flaky=a.allow_flaky, coverage=not a.no_coverage, events=a.events)
    bot = AG.Agent() if a.agent else None
    if bot:
        s.planner = AG.make_planner(bot)
        s.log("agent: " + ("Vultr Serverless Inference, model " + bot.model if bot.enabled
                           else "no inference key set, so rule order and rule wording"))

    def _stop(*_):
        engine.stop_and_wipe(s)  # kill lockbox jobs, wipe copies: a stopped scan leaves nothing behind
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    recs = s.run()
    agent_meta = None
    if bot:
        s.emit("explain", parts=min(a.explain_max, sum(r["verdict"] == "LOAD-BEARING" for r in recs)))
        AG.explain_all(bot, recs, s.target, max_n=a.explain_max)
        agent_meta = bot.describe()
        agent_meta["plan"] = s.plan_info
    # no absolute host paths in reports: store them relative to the report folder
    pub_oracle = dict(oracle)
    if pub_oracle.get("env_source"):
        pub_oracle["env_source"] = os.path.relpath(os.path.abspath(pub_oracle["env_source"]), s.outdir)
    meta = {
        "theseus": __version__,
        "target": os.path.relpath(s.target, s.outdir),
        "target_credit": a.credit or oracle.get("credit"),
        "target_name": os.path.basename(s.target.rstrip("/")),
        "commit": commit_of(s.target),
        "finished": engine.now_zoned(),
        "stopped": s.stopped,
        "warnings": s.warnings,
        "coverage_dir": "coverage" if os.path.isdir(os.path.join(s.outdir, "coverage")) else None,
        "oracle": pub_oracle,
        "baseline": s.base,
        "baseline_tests": s.base_tests,
        "timing": s.timing,
        "lockbox": s.box.describe(),
        "agent": agent_meta,
    }
    summ = report.write(s.outdir, meta, recs)
    if bot and bot.enabled:
        meta["agent_brief"] = AG.brief(bot, summ, recs)
        meta["agent"] = bot.describe()
        meta["agent"]["plan"] = s.plan_info
        summ = report.write(s.outdir, meta, recs)
    s.emit("done", summary=summ, stopped=s.stopped)
    print(json.dumps({"out": s.outdir, "summary": summ, "timing": s.timing}, indent=2))
    if s.stopped:
        sys.exit(3)


def cmd_recheck(a):
    rep = json.load(open(a.report))
    rdir = os.path.dirname(os.path.abspath(a.report))
    meta = rep["meta"]
    target = os.path.realpath(os.path.join(rdir, meta["target"]))
    oracle = dict(meta["oracle"])
    if oracle.get("env_source") and not os.path.isabs(oracle["env_source"]):
        oracle["env_source"] = os.path.realpath(os.path.join(rdir, oracle["env_source"]))
    first = {r["part"]["id"]: r for r in rep["parts"]}
    ids = list(first) if a.all else a.parts
    missing = [i for i in ids if i not in first]
    if missing or not ids:
        raise SystemExit("theseus: not in the report: " + ", ".join(missing) if missing else "theseus: name parts or --all")
    out = a.out or os.path.join(rdir, "recheck-" + engine.stamp(False))
    s = engine.Scan(target, oracle, out, workers=a.workers, blocks=False, keep_copies=a.keep_copies)
    # judge against the first scan's baseline, with its coverage
    s.base_tests = meta.get("baseline_tests")
    s.base = meta["baseline"]
    s.base_steps = {st["name"]: st for st in meta["baseline"]["steps"]}
    s.flaky = meta["baseline"].get("flaky")
    if meta.get("coverage_dir"):
        s.load_coverage(os.path.join(rdir, meta["coverage_dir"]))

    def _stop(*_):
        engine.stop_and_wipe(s)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    recs = s._pool([first[i]["part"] for i in ids])
    rows = []
    for r in recs:
        o = first[r["part"]["id"]]
        was = (o["verdict"], sorted(o.get("what_broke", {}).get("steps", [])), o.get("coverage"))
        now = (r["verdict"], sorted(r.get("what_broke", {}).get("steps", [])), r.get("coverage"))
        rows.append({"part": r["part"]["id"], "first": {"verdict": was[0], "failing": was[1], "label": was[2]},
                     "recheck": {"verdict": now[0], "failing": now[1], "label": now[2]},
                     "result": recheck_result(was, now),
                     "end_rule": r.get("end_rule"), "seconds": r.get("seconds"),
                     "left_running": r.get("left_running", 0),
                     "why": (r.get("what_broke") or {}).get("why") or r.get("notes"),
                     "evidence": r.get("evidence")})
    try:
        os.rmdir(s.workdir)
    except OSError:
        pass
    summ = {"rechecked": len(rows), "repeatable": sum(x["result"] == "repeatable" for x in rows),
            "inconclusive_both_times": sum(x["result"] == HUNG_BOTH for x in rows),
            "not_repeatable": sum(x["result"] == NOT_REPEATABLE for x in rows),
            "end_rules": {k: sum(1 for x in rows if x.get("end_rule") == k) for k in sorted({x.get("end_rule") or "none" for x in rows})},
            "left_running": sum(x.get("left_running", 0) for x in rows),
            "finished": engine.now_zoned()}
    with open(os.path.join(s.outdir, "RECHECK.json"), "w") as f:
        json.dump({"report": os.path.relpath(os.path.abspath(a.report), s.outdir),
                   "summary": summ, "parts": rows}, f, indent=2)
    for x in rows:
        print(f"{x['result']:22s} {x['part']}: first {x['first']['verdict']} {x['first']['failing']}"
              f" / now {x['recheck']['verdict']} {x['recheck']['failing']} [{x.get('end_rule')}, {x.get('seconds')}s]")
    print(json.dumps(summ))
    # a cut that hangs both times is consistent (inconclusive, never a verdict), not flaky
    sys.exit(0 if summ["not_repeatable"] == 0 else 1)


HUNG_BOTH = engine.HUNG_BOTH
NOT_REPEATABLE = engine.NOT_REPEATABLE
recheck_result = engine.recheck_result


def cmd_clean(a):
    n = engine.clean_run(a.out)
    print(f"theseus: removed {n} leftover work copies from {os.path.join(a.out, 'work')}" if n
          else "theseus: no leftover work copies")


def cmd_contain(a):
    r = selftest.containment_table()
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(r["table"])
    sys.exit(0 if r["all_held"] else 1)


def cmd_selftest(a):
    r = selftest.run()
    print(json.dumps(r, indent=2))
    print("SELFTEST: " + ("PASS, every case above" if r.get("passed") else
                          "FAIL: " + ", ".join(r.get("failed_cases", []))))
    sys.exit(0 if r.get("passed") else 1)


def cmd_doctor(a):
    from theseus.lockbox import doctor
    r = doctor()
    r["note"] = "Checked on this machine only. A cloud host needs its own doctor run."
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["ok"] else 1)


def cmd_apply(a):
    sys.exit(apply(a.report, a.dest, a.approve, scope=a.scope, verify=not a.no_verify))


def main():
    p = argparse.ArgumentParser(prog="theseus", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("target")
    s.add_argument("--oracle", required=True, help="JSON: the still-the-ship checks")
    s.add_argument("--out", help="new folder for runs and report (must not exist)")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--files-only", action="store_true")
    s.add_argument("--keep-copies", action="store_true",
                   help="keep each run's cut copy in OUT/copies/remove-<part>_<stamp>")
    s.add_argument("--max-minutes", type=float, default=None,
                   help="stop starting new cuts after this many minutes (default: off, run until done)")
    s.add_argument("--recheck-cap", type=int, default=engine.RECHECK_CAP_S,
                   help="per-cut cap in seconds for the timed-out recheck pass (default 600)")
    s.add_argument("--repeat", type=int, default=1, help="run every cut N times; flag flaky results")
    s.add_argument("--credit", help="credit line for the target project (shown in the report header)")
    s.add_argument("--baseline-runs", type=int, default=3,
                   help="run the untouched copy this many times (min 3); if they disagree the project is flaky")
    s.add_argument("--allow-flaky", action="store_true",
                   help="scan a flaky project anyway; still no bloat calls")
    s.add_argument("--no-coverage", action="store_true",
                   help="skip the coverage run (then nothing can be proven bloat)")
    s.add_argument("--events", help="append one JSON line per event to this file (live progress)")
    s.add_argument("--agent", action="store_true",
                   help="Vultr Serverless Inference plans the cut order and explains breaks "
                        "(key from VULTR_INFERENCE_API_KEY; verdicts still come from the tests)")
    s.add_argument("--explain-max", type=int, default=25, help="AI explanations for at most N breaks")
    s.set_defaults(fn=cmd_scan)
    cl = sub.add_parser("clean", help="remove work copies a kill -9 left in a run folder")
    cl.add_argument("--out", required=True)
    cl.set_defaults(fn=cmd_clean)
    ct = sub.add_parser("contain", help="containment demo: network, host home, env key, memory hog")
    ct.add_argument("--json", action="store_true")
    ct.set_defaults(fn=cmd_contain)
    rc = sub.add_parser("recheck", help="rerun exact cuts from a report on fresh copies")
    rc.add_argument("--report", required=True)
    rc.add_argument("parts", nargs="*", help="part ids from the report")
    rc.add_argument("--all", action="store_true")
    rc.add_argument("--out", help="new folder (must not exist)")
    rc.add_argument("--workers", type=int, default=4)
    rc.add_argument("--keep-copies", action="store_true")
    rc.set_defaults(fn=cmd_recheck)
    dr = sub.add_parser("doctor", help="pre-flight: bwrap version, user namespaces, memory cap")
    dr.set_defaults(fn=cmd_doctor)
    t = sub.add_parser("selftest")
    t.set_defaults(fn=cmd_selftest)
    ap = sub.add_parser("apply")
    ap.add_argument("--report", required=True)
    ap.add_argument("--dest", required=True, help="new folder for the trimmed copy")
    ap.add_argument("--approve", action="store_true", help="the human yes")
    ap.add_argument("--scope", choices=["bloat", "all"], default="bloat")
    ap.add_argument("--no-verify", action="store_true")
    ap.set_defaults(fn=cmd_apply)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
