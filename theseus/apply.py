# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""Apply: only with a human yes, and only to a NEW trimmed copy. Never the original."""
import json
import os
import shutil

from . import parts as P
from .engine import Scan, wipe, stamp, _writable

KEEP_PREFIX = ("LICENSE", "README", "NOTICE")


def pick(report, scope):
    out = []
    for r in report["parts"]:
        if r["verdict"] != "NOT NEEDED":
            continue
        if r.get("coverage") == "is-a-check" or P.is_test_file(r["part"]["file"]):
            continue  # tests are never trimmed, not even with --scope all
        base = os.path.basename(r["part"]["file"]).upper()
        if r["part"]["level"] == "file" and base.startswith(KEEP_PREFIX):
            continue
        if scope == "bloat" and r.get("coverage") != "proven-not-needed":
            continue
        out.append(r["part"])
    return out


def apply(report_path, dest, approve, scope="bloat", verify=True, log=print):
    report = json.load(open(report_path))
    meta = report["meta"]
    chosen = pick(report, scope)
    log(f"theseus apply: {len(chosen)} parts selected (scope={scope})")
    for p in chosen:
        log(f"  - {p['id']}")
    if not approve:
        log("Nothing changed. Re-run with --approve to write a trimmed copy to --dest.")
        return 2
    dest = os.path.abspath(dest)
    if os.path.exists(dest):
        raise SystemExit(f"theseus: {dest} already exists, refusing to overwrite")
    # report paths are stored relative to the report folder
    rdir = os.path.dirname(os.path.abspath(report_path))
    target = os.path.realpath(os.path.join(rdir, meta["target"]))
    real_dest = os.path.join(os.path.realpath(os.path.dirname(dest)), os.path.basename(dest))
    if real_dest == target or real_dest.startswith(target + os.sep):
        raise SystemExit("theseus: dest must be outside the original")
    skip = set([".git"] + meta["oracle"].get("env_dirs", []))
    shutil.copytree(target, dest, symlinks=True, ignore=lambda _d, n: [x for x in n if x in skip])
    _writable(dest)
    gone = {p["file"] for p in chosen if p["level"] == "file"}
    for p in chosen:
        if p["level"] == "file":
            P.apply_removal(dest, p)
    blocks = sorted([p for p in chosen if p["level"] == "block" and p["file"] not in gone],
                    key=lambda p: (p["file"], -p["first"]))
    for p in blocks:
        P.apply_removal(dest, p)
    with open(os.path.join(dest, "THESEUS_TRIM.json"), "w") as f:
        json.dump({"from": meta.get("target_name", os.path.basename(target)), "scope": scope, "removed": [p["id"] for p in chosen],
                   "approved": True, "when": stamp()}, f, indent=2)
    log(f"trimmed copy written to {dest}")
    if not verify:
        return 0
    probe_out = dest.rstrip("/") + "-verify-" + stamp()
    oracle = dict(meta["oracle"])
    if oracle.get("env_source") and not os.path.isabs(oracle["env_source"]):
        oracle["env_source"] = os.path.realpath(os.path.join(rdir, oracle["env_source"]))
    s = Scan(dest, oracle, probe_out, workers=1, blocks=False, log=lambda *_: None, coverage=False)
    base = s.baseline()
    wipe(s.workdir)
    want = meta.get("baseline_tests")
    if base["passed"] and want is not None and (s.base_tests or 0) < want:
        base["passed"] = False
        log(f"  passing tests dropped {want} -> {s.base_tests}")
    log("re-check of the trimmed copy in the lockbox: " + ("PASS, still the ship" if base["passed"] else "FAIL"))
    for st in base["steps"]:
        log(f"  {st['name']}: rc={st['rc']}")
    shutil.rmtree(probe_out, ignore_errors=True)
    return 0 if base["passed"] else 1
