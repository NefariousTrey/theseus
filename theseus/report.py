# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""The blast radius guide: Markdown for people, JSON for machines."""
import json
import os

PROVEN = "proven-not-needed"
UNTESTED = "UNTESTED: human call"
INCONCLUSIVE = "still timed out: inconclusive"


def _cell(s, n=110):
    s = str(s).replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _is_test(r):
    return bool(r.get("is_test")) or r["part"].get("kind") == "test"


def summarize(recs, meta=None):
    lb = [r for r in recs if r["verdict"] == "LOAD-BEARING"]
    nn = [r for r in recs if r["verdict"] == "NOT NEEDED"]
    meta = meta or {}
    return {
        "stopped": meta.get("stopped"),
        "flaky_baseline": bool((meta.get("baseline") or {}).get("flaky")),
        "parts_checked": len(recs),
        "load_bearing": len(lb),
        "load_bearing_code": sum(1 for r in lb if not _is_test(r)),
        "load_bearing_tests": sum(1 for r in lb if _is_test(r)),
        "not_needed": len(nn),
        "bloat_proven": sum(1 for r in nn if r.get("coverage") == PROVEN),
        "untested_human_call": sum(1 for r in nn if r.get("coverage") == UNTESTED),
        "tests_not_needed_is_a_check": sum(1 for r in nn if r.get("coverage") == "is-a-check"),
        "timed_out_inconclusive": sum(1 for r in recs if r["verdict"] == INCONCLUSIVE),
        "confirmed_on_recheck": sum(1 for r in recs if r.get("confirmed_on_recheck")),
        "not_run": sum(1 for r in recs if r["verdict"].startswith("NOT RUN")),
        "files_load_bearing": sum(1 for r in lb if r["part"]["level"] == "file"),
        "files_not_needed": sum(1 for r in nn if r["part"]["level"] == "file"),
        "blocks_load_bearing": sum(1 for r in lb if r["part"]["level"] == "block"),
        "blocks_not_needed": sum(1 for r in nn if r["part"]["level"] == "block"),
        "warnings": len(meta.get("warnings") or []),
        # v0.2.1: how each run ended
        "ended_printed_failure_killed_early": _end(recs, "printed failure, killed early"),
        "ended_failed_summary_never_printed": _end(recs, "failed, summary never printed"),
        "ended_silent_counted_broken": _end(recs, "silent, counted broken"),
        "ended_hit_600s_cap": _end(recs, "hit 600 s cap"),
        "ended_clean_load_bearing": sum(1 for r in lb if r.get("end_rule", "clean load-bearing") == "clean load-bearing"),
        "ended_not_needed": len(nn),
        "ended_inconclusive": sum(1 for r in recs if r["verdict"] == INCONCLUSIVE),
        # v0.2.3: reruns of unsure cuts (at most 5 runs per part) and the quiet limit used
        "ended_flaky_kept_load_bearing": _end(recs, "flaky"),
        "parts_rerun": sum(1 for r in recs if (r.get("runs") or {}).get("n", 1) > 1),
        "rerun_runs": sum(max(0, (r.get("runs") or {}).get("n", 1) - 1) for r in recs),
        "max_runs_per_part": (meta.get("baseline") or {}).get("max_runs_per_part"),
        "quiet_limits": (meta.get("baseline") or {}).get("quiet_limits") or {},
        "left_running": sum(r.get("left_running", 0) for r in recs),
        # v0.2.2: agent plan phases that fell back to rule order (whole phase or some chunks)
        "agent_plan_phases": len(((meta or {}).get("agent") or {}).get("plan") or []),
        "agent_plan_fallbacks": sum(1 for i in (((meta or {}).get("agent") or {}).get("plan") or [])
                                    if i.get("fallback") or not str(i.get("source", "")).startswith("agent")),
        "agent_plan_fallback_chunks": sum(int(i.get("fallback_chunks") or 0)
                                          for i in (((meta or {}).get("agent") or {}).get("plan") or [])),
    }


def _end(recs, rule):
    return sum(1 for r in recs if r.get("end_rule") == rule)


def end_rules_section(summ):
    """The 'How runs ended' section (v0.2.1, rerun and quiet-limit text v0.2.3)."""
    ql = summ.get("quiet_limits") or {}
    qtxt = "; ".join(f"`{k}`: {v['quiet_limit_s']:.0f} s (longest baseline gap {v['baseline_max_gap_s']:.1f} s)"
                     for k, v in ql.items() if v.get("quiet_limit_s")) or "no step used the quiet rule"
    return [
        "## How runs ended",
        "",
        f"- Printed failure, killed early: {summ['ended_printed_failure_killed_early']}",
        f"- Failed, summary never printed: {summ['ended_failed_summary_never_printed']}",
        f"- Silent, counted broken: {summ['ended_silent_counted_broken']}",
        f"- Flaky (mixed results), kept load-bearing: {summ.get('ended_flaky_kept_load_bearing', 0)}",
        f"- Hit 600 s cap: {summ['ended_hit_600s_cap']}",
        f"- Clean load-bearing: {summ['ended_clean_load_bearing']}",
        f"- Not needed: {summ['ended_not_needed']}",
        f"- Inconclusive: {summ['ended_inconclusive']}",
        f"- Processes left running after runs: {summ['left_running']}",
        "",
        "The summary trigger is for mocha only. Mocha prints its summary, then may never exit. "
        "Theseus waits about 3 s after the summary (longer only while output is still flowing, up to 30 s), then kills the run. Other runners keep the old time cap.",
        "",
        f"Quiet limit used: {qtxt}. The limit is 3x the longest output gap seen in that step's baseline runs, "
        "never under 15 s and never over 60 s.",
        "",
        "Silent, counted broken means no output for the quiet limit, every time the cut was run. "
        "It is counted broken under the quiet rule. It is not proven by a failing test.",
        "",
        f"Reruns: a cut that was silent, timed out, or inconclusive is run again on a fresh copy, up to "
        f"{summ.get('max_runs_per_part') or 5} runs in total, never more. A rerun after a time-out gets the long recheck cap. "
        f"{summ.get('parts_rerun', 0)} parts were rerun ({summ.get('rerun_runs', 0)} extra runs). "
        "A part is not needed only if every run passed cleanly. A part that passed some runs and not others is flaky "
        "and kept load-bearing, because it is never safe to cut.",
        "",
    ]


def _why(r):
    wb = r["what_broke"]
    out = []
    for step in wb["steps"]:
        reasons = wb.get("why", {}).get(step) or []
        out.append(f"{step}: " + ("; ".join(reasons) if reasons else "failed"))
    return " / ".join(out)


def write(outdir, meta, recs):
    summ = summarize(recs, meta)
    data = {"meta": meta, "summary": summ, "parts": recs}
    with open(os.path.join(outdir, "report.json"), "w") as f:
        json.dump(data, f, indent=2)
    lb = sorted([r for r in recs if r["verdict"] == "LOAD-BEARING"],
                key=lambda r: (_is_test(r), -len(r.get("blast_radius_files", [])), r["part"]["id"]))
    nn = sorted([r for r in recs if r["verdict"] == "NOT NEEDED"], key=lambda r: r["part"]["id"])
    base = meta["baseline"]
    L = []
    L.append("# Theseus blast radius guide")
    L.append("")
    L.append("_Blast radius that leaves only what's load-bearing._")
    L.append("")
    L.append(f"- Target: `{meta['target_name']}`" + (f" at commit `{meta['commit']}`" if meta.get("commit") else ""))
    if meta.get("target_credit"):
        L.append(f"- Target credit: {meta['target_credit']}")
    L.append(f"- Scanned: {meta['finished']} (Theseus {meta.get('theseus')})")
    L.append("- Still-the-ship check (oracle): " + "; then ".join(f"`{s['cmd']}`" for s in meta["oracle"]["steps"]))
    tests = meta.get("baseline_tests")
    L.append(f"- Baseline (nothing removed): {'PASS' if base['passed'] else 'FAIL'}, run {base.get('runs', 1)} times"
             + (", FLAKY: the runs disagreed" if base.get("flaky") else ", all runs agreed")
             + f", passing tests {tests if tests is not None else 'unknown (no test summary parsed)'}")
    for st in base["steps"]:
        c = st.get("counts")
        L.append(f"  - `{st['name']}`: rc {st['rc']}, "
                 + (f"{c['passed']} passing, {c['failed']} failing, {c['skipped']} skipped/pending ({', '.join(c['frameworks'])})"
                    if c else "test count unknown (no summary parsed; judged on exit code and load crashes only)"))
    L.append(f"- Coverage at baseline: {base.get('coverage', 'not collected')}")
    t = meta["timing"]
    L.append(f"- Runs: {t['runs']} in {t['total_seconds']} s with {t['workers']} workers")
    ql = base.get("quiet_limits") or {}
    if ql:
        L.append("- Quiet limit (v0.2.3, 3x the longest baseline output gap, 15 to 60 s): "
                 + "; ".join(f"`{k}` {v['quiet_limit_s']:.0f} s (gap {v['baseline_max_gap_s']:.1f} s)" if v.get("quiet_limit_s")
                             else f"`{k}` off" for k, v in ql.items())
                 + f". Unsure cuts rerun up to {base.get('max_runs_per_part', 5)} runs each.")
    lbx = meta["lockbox"]
    L.append(f"- Lockbox: {lbx['sandbox']}, network {lbx['network']}, home {lbx['host_home']}, memory cap {lbx['memory_cap']}")
    if lbx.get("deterministic_env"):
        L.append(f"- Fixed env inside the lockbox: {lbx['deterministic_env']}")
    L.append("- The original was only read. Every run used a fresh copy that was wiped afterward. Symlinks are never parts and never followed.")
    L.append("- Cuts are whole files and top-level blocks only (no line-level cuts).")
    ag = meta.get("agent")
    if ag and ag.get("enabled"):
        cost = ag.get("est_cost_usd")
        L.append(f"- Agent: {ag['service']}, model {ag['model']}: planned the cut order and wrote the plain-words "
                 f"explanations. {ag['calls']} calls, {ag['tokens_in']} tokens in, {ag['tokens_out']} out"
                 + (f", about ${cost:.4f}" if cost is not None else "") + ". Every verdict came from the tests, not the model.")
        L.append(f"- Agent plan phases: {summ['agent_plan_phases']}, fell back to rule order: "
                 f"{summ['agent_plan_fallbacks']} (chunks: {summ['agent_plan_fallback_chunks']})")
    elif ag:
        L.append("- Agent: on, but no Vultr inference key was set, so the order and wording are rule-based. Verdicts come from the tests.")
    else:
        L.append("- No AI was used in this scan. Verdicts come from the tests.")
    L.append("")
    if meta.get("warnings"):
        L.append("## Warnings")
        L.append("")
        for w in meta["warnings"]:
            L.append(f"- {w}")
        L.append("")
    if meta.get("stopped"):
        L.append("## Stopped before any cut")
        L.append("")
        if meta["stopped"] == "baseline failed":
            L.append("The untouched copy already fails the checks. Fix that first: a sinking ship has no blast radius.")
            for st in base["steps"]:
                if st.get("tail"):
                    L.append("")
                    L.append(f"`{st['name']}` (rc {st['rc']}) last lines:")
                    L.append("")
                    L.append("```")
                    L += st["tail"][-15:]
                    L.append("```")
        elif str(meta["stopped"]).startswith("baseline unusable"):
            L.append("The untouched copy went silent or hung on the test step, so the quiet rule cannot be trusted. "
                     "Stopped the whole job: baseline unusable. Fix the hang first.")
        else:
            L.append("The untouched copy gave different results on repeated runs, so Theseus refuses to call anything bloat. "
                     "Fix the flaky tests, or rerun with --allow-flaky to map load-bearing parts with no bloat calls.")
        L.append("")
        L.append("_Theseus. Copyright (c) 2026 [@Nefarious_Trey on X](https://x.com/Nefarious_Trey). MIT License. Made by Trey with Grok._")
        with open(os.path.join(outdir, "BLAST_RADIUS.md"), "w") as f:
            f.write("\n".join(L) + "\n")
        return summ
    L.append("## Summary")
    L.append("")
    L.append("| | Load-bearing | Not needed |")
    L.append("|---|---|---|")
    L.append(f"| Whole files | {summ['files_load_bearing']} | {summ['files_not_needed']} |")
    L.append(f"| Top-level blocks (in load-bearing code files) | {summ['blocks_load_bearing']} | {summ['blocks_not_needed']} |")
    L.append("")
    L.append(f"Load-bearing: {summ['load_bearing_code']} code or config parts, and {summ['load_bearing_tests']} test files "
             "(cutting a test drops checks, so it counts as broken).")
    L.append("")
    L.append(f"Not needed: {summ['bloat_proven']} proven bloat (tests ran it and still held without it), "
             f"{summ['untested_human_call']} UNTESTED (no test reaches it, a human decides), "
             f"{summ['tests_not_needed_is_a_check']} test helpers or tests whose cut changed no count (never bloat).")
    L.append("")
    L.append("A cut is BROKEN (load-bearing) if any check exits non-zero, passing tests drop, skipped or pending rise, "
             "failing tests rise, the test summary vanishes, or a new load crash appears (Cannot find module, "
             "SyntaxError, ImportError and similar).")
    L.append("")
    if meta.get("agent_brief"):
        L.append("## The brief (AI summary of the numbers above)")
        L.append("")
        L.append(meta["agent_brief"])
        L.append("")
    L += end_rules_section(summ)
    L.append("## Load-bearing: what breaks without each part")
    L.append("")
    L.append("| Part | Why it counts as broken | Blast radius (files hit) | First error |")
    L.append("|---|---|---|---|")
    for r in lb:
        wb = r["what_broke"]
        rad = r.get("blast_radius_files", [])
        rad_s = ", ".join(rad[:4]) + (f" +{len(rad) - 4}" if len(rad) > 4 else "")
        err = wb["error_lines"][0] if wb["error_lines"] else (wb["tail"][-1] if wb["tail"] else "")
        tag = " (test)" if _is_test(r) else ""
        L.append(f"| `{_cell(r['part']['id'], 70)}`{tag} | {_cell(_why(r), 90)} | {_cell(rad_s or '-', 90)} | {_cell(err)} |")
    ex = [r for r in lb if (r.get("explain") or {}).get("ai")]
    if ex:
        L.append("")
        L.append("### In plain words (AI explanations; the tests made the call)")
        L.append("")
        for r in ex:
            L.append(f"- `{r['part']['id']}`: {r['explain']['ai']}")
    L.append("")
    L.append("## Not needed: the ship still sails without these")
    groups = [
        ("Proven bloat (tests ran it; every check held without it)", lambda r: r.get("coverage") == PROVEN),
        ("UNTESTED: human call (no test reaches it, so passing proves nothing)", lambda r: r.get("coverage") == UNTESTED),
        ("Tests themselves (a check, not cargo; never bloat)", lambda r: r.get("coverage") == "is-a-check"),
    ]
    for title, pred in groups:
        sel = [r for r in nn if pred(r)]
        L.append("")
        L.append(f"### {title} ({len(sel)})")
        L.append("")
        for r in sel:
            L.append(f"- `{r['part']['id']}`: {'; '.join(r.get('notes', []))}")
    L.append("")
    inc = sorted([r for r in recs if r["verdict"] == INCONCLUSIVE], key=lambda r: r["part"]["id"])
    conf = [r for r in recs if r.get("confirmed_on_recheck")]
    rc = meta["timing"].get("timeout_recheck") or {}
    L.append("## Timed out (rechecked)")
    L.append("")
    if meta["timing"].get("reruns_per_part_max"):
        L.append(f"v0.2.3: hangs are handled by the reruns above (at most {meta['timing']['reruns_per_part_max']} runs per part), "
                 f"not by a separate recheck pass. {len(inc)} parts got no sure result in any run and are counted as neither. "
                 "A hang is never called bloat.")
    else:
        L.append(f"{rc.get('timed_out_first_pass', 0)} cuts hit the time cap on the first pass. Each was rechecked once on a fresh "
                 f"copy with a {rc.get('cap_seconds', '?')} s cap: {len(conf)} got a real verdict (confirmed on recheck), "
                 f"{len(inc)} are still inconclusive and counted as neither. A hang is never called bloat.")
    L.append("")
    for r in conf:
        L.append(f"- `{r['part']['id']}`: {r['verdict']}, confirmed on recheck")
    for r in inc:
        pf = r.get("partial_failures_before_cap")
        if r.get("end_rule") == "killed early, inconclusive":
            L.append(f"- `{r['part']['id']}`: " + "; ".join(r.get("notes") or ["killed early, inconclusive"]))
            continue
        if r.get("runs"):
            L.append(f"- `{r['part']['id']}`: " + "; ".join(r.get("notes") or ["inconclusive"]))
            continue
        L.append(f"- `{r['part']['id']}`: still timed out: inconclusive"
                 + (f" ({pf} test failures printed before the cap)" if pf else ""))
    nr = [r for r in recs if r["verdict"].startswith("NOT RUN")]
    if nr:
        L.append("")
        L.append(f"{len(nr)} parts were not run because --max-minutes was reached.")
    L.append("")
    L.append("## Delete these?")
    L.append("")
    L.append("Nothing has been deleted. Theseus never touches the original. If you say yes, it writes a "
             "trimmed copy to a new folder and re-checks it in the lockbox:")
    L.append("")
    L.append("```")
    L.append("python3 theseus.py apply --report report.json --dest <new-folder> --approve [--scope bloat|all]")
    L.append("```")
    L.append("")
    L.append("`--scope bloat` takes only proven bloat. `--scope all` also takes UNTESTED parts, a human call. "
             "Tests, LICENSE, README and NOTICE are never taken. Each part was tested alone. Removing several "
             "together can still break, so apply re-runs the checks on the trimmed copy.")
    L.append("")
    L.append("_Theseus. Copyright (c) 2026 [@Nefarious_Trey on X](https://x.com/Nefarious_Trey). MIT License. Made by Trey with Grok._")
    with open(os.path.join(outdir, "BLAST_RADIUS.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    return summ
