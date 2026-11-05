# Changelog

Each fix lists the run it came from. Old runs are kept on disk as receipts and only hidden from the public run list (`hidden_runs.txt`). Nothing is deleted.

## 0.2.1 run-end rules (Nov 4, 2026, evening)

Trey asked for this. A part that breaks the tests should end that run and move on. It should not spin on a failure it already showed.

- **Mocha summary trigger.** When mocha prints "N passing, M failing" with M above 0, Theseus waits 3 s, then kills the run. Label: "printed failure, killed early". If a failure line prints but no summary comes, the run ends at the quiet limit (60 s for Express). Label: "failed, summary never printed". This rule is for mocha only.
- **Baseline guard.** A kill only counts when the failing tests are new. Theseus compares full test titles with the baseline's failing set. If titles do not parse, it falls back to the count and says so.
- **Quiet rule.** On a test runner step, no output for max(60 s, 10x the baseline time) ends the run. Label: "silent, counted broken". This is not proven by a failing test, and the report says so. The baseline is exempt. A silent or hung baseline stops the scan as "baseline unusable".
- **Kill.** The whole process group gets SIGTERM, then SIGKILL after 5 s. bwrap keeps --die-with-parent. Theseus then checks that nothing from the run is left running. The 600 s cap stays.
- **Evidence.** Each part keeps at most 20 failure lines. Home paths are stripped.
- **Report.** A new "How runs ended" section counts each end type on its own.
- **Leftover check settles.** The check now looks again for up to 5 s before it counts a process as left running. One early look caught a sandbox mid-exit once (left_running 1 in two scans, nothing was running when checked by hand).

Results on pop-os:

- Before this change, the Vultr rerun (r2) matched pop-os v0.2.0 on all 402 parts. 23 of them were inconclusive. Mocha printed its failures, then never exited, so they hit the cap.
- Recheck of those 23 parts: all 23 are now LOAD-BEARING, "printed failure, killed early", matched by full title. 46 s wall at 8 workers.
- Full Express scan: 195 s wall at 16 workers (v0.2.0 took 1655 s). All 379 decided verdicts match v0.2.0. The 23 inconclusive parts are now load-bearing. 0 inconclusive. 24 and 32 workers took 199 s and 198 s, so 16 is enough.
- Demo target: expressjs/compression (MIT). 36 parts in about 10 s. It shows every verdict type, including one proven bloat line (`var Buffer = require('safe-buffer').Buffer`). The trimmed copy passes tests and lint.

## 0.2.1 (Nov 4, 2026)

- **Web: hide superseded runs from the public list, keep them on disk.** New `hidden_runs.txt` (one run id per line) next to the runs folder. Hidden runs: `20261104-092512-24b387` and `20261104-092557-14fc12` (see below).
- **Express on Vultr: recheck cap back to the 600 s default.** Run `express-on-vultr-110420260928` was started with `--recheck-cap 300` to save time on a 1 vCPU VM. Three parts (`examples/mvc/controllers/user/index.js::db`, `lib/application.js::finalhandler`, `lib/application.js::logerror`) fail at about 303 to 309 s, so a 300 s cap cut them off and left them inconclusive. The pop-os run with 600 s confirmed all three load-bearing. Rerun on Vultr uses the default cap. That run stays a receipt.

## 0.2.0 deploy fixes (Nov 4, 2026, before this changelog)

- **Service sandbox setting** fixed so the lockbox can run under the web service. Came from run `20261104-092512-24b387` (stopped: baseline failed, no cuts made). Hidden from the public list.
- **Empty AI planner reply** fixed (model thinking turned off so the answer holds the JSON plan). Came from run `20261104-092557-14fc12` (agent asked but fell back to rule order: "no JSON object in the answer"). Hidden from the public list. First good agent run after the fix: `20261104-092729-be5ba0`.
- **Approve button on stopped scans** fixed.

## v0.2.2 (110420262200)
- Agent JSON parsing hardened: strips code fences and think tags, takes the largest balanced JSON, tolerates trailing text; up to 2 retries with a strict JSON-only prompt; plans in chunks of 40 parts and halves the chunk when an answer is cut off; rule order only after retries. Report counts plan fallbacks (agent_plan_fallbacks). Verdicts still come only from the tests.
- Tests: new tests/test_agentjson.py; 30 unit tests pass and selftest PASS on pop-os; pop-os compression rerun 36/36 (24/12/1 bloat), 10 s.
- Vultr vc2-6c-16gb, 12 workers, as theseus user: Express 402/402 match (354/48/0/0), 1588 s, agent on files and blocks, 0 fallbacks, 32 calls, 18050 tokens in / 5873 out. compression 36/36 match, 58 s, 0 fallbacks, 27 calls, 11357 in / 2133 out.
- Code backup on VM: /opt/theseus.bak.110420262119. Hidden (kept on disk): r-express-v021-6c-110420262012, two 1 vCPU Express runs, orderflow runs 20261104-092729-be5ba0 and 20261104-092539-e74a7f.
- Receipts: https://66-135-26-143.sslip.io/ . New submission draft LABLAB_SUBMISSION_110420262200.md and screenshots 01/03 _110420262200 in the submit kit.

## 0.2.2b review fixes (Nov 4, 2026, late)
- Version string set to 0.2.2. Report time also shows ET when the server is on UTC.
- Agent box on the web page is off by default, with a plain line on what is sent to Vultr. README note says the same.
- compression credit added to the web footer and README. Grace wording now says "about 3 s, up to 30 s while output flows".
- Internal review names in code comments replaced with neutral fix numbers. Red team test builds the home path at runtime.
- README: appended v0.2.2 notes (version, VM size, hang scope, credit, agent data).

## 0.2.2b recheck fixes (Nov 4, 2026, 10:28 PM ET)
- README: corrected the agent line (explains up to 25 breaks per scan, 20 from the web page) and listed part sizes, counts and file names as also sent.
- .gitignore: ignore *.bak* and VULTR_LEDGER.md* (ledger kept as a private copy outside the repo).
- Public receipts r-express-v022 and r-compression-v022 got a note that their header says 0.2.1 (version string bumped after the run; same scan code). The v0.2.2b compression check run is now public as r-compression-v022b-6c-110420262217.
- (VM note, merged Nov 4, 2026) On the Vultr VM, meta.json and BLAST_RADIUS.md for those receipts were backed up before the note was added.

## 0.2.3 quiet limit and reruns (Nov 4, 2026, 11:24 PM ET)

Trey asked for this. Keep the shortest wait that still tells a dead run from lag, and check unsure parts again, at most 5 runs each. His rule stays: a cut that leaves the run dead or silent is load-bearing.

- **Quiet limit.** Now 3x the longest output gap seen in that step's baseline runs, never under 15 s and never over 60 s. The old max(60 s, 10x the baseline time) is dropped. Every receipt prints the limit and the gap it came from. The "failed, summary never printed" wait uses the same limit.
- **Reruns.** A silent, timed-out or inconclusive cut runs again on a fresh copy, up to 5 runs in total. The cap is `runend.MAX_RUNS = 5`, applied in code, and unit tests show it cannot loop (even when 10**9 runs are asked for). A rerun after a time-out gets the long recheck cap. This replaces the separate recheck pass.
- **Verdicts.** Not needed only if every run passed cleanly. Proven failure with no pass: load-bearing. Silent every time: load-bearing, "silent, counted broken". Mixed after 5 runs: "flaky", kept load-bearing. Hung every time: inconclusive.
- **Trade-off.** Under the strict rule, a slow but good cut that times out once and then passes is kept as flaky load-bearing, not called not needed.
- **Runs.** Unit tests, OK (receipts/unittest-v023-110420262251.txt) and selftest PASS in 67 s (receipts/selftest-v023-110420262251.txt). compression: r-compression-v023-6c-110420262256, 36 parts, 52.9 s total, 28.9 s first pass, 62 s wall, same 36 verdicts as v0.2.2b. Express: r-express-v023-6c-110420262256, 402 parts, 1385 s, 1391 s wall, same 402 verdicts as v0.2.2. Quiet limit used: 15 s on both (longest baseline gaps 1.2 s and 1.1 s). 0 parts needed a rerun.
- **Changed end labels (verdicts unchanged).** 7 Express parts that are still load-bearing now end as "failed, summary never printed" instead of "clean load-bearing" (5: examples/auth users, application.js http, once, tryRender, view.js tryStat) or "printed failure, killed early" (2: response.js mime, stringify). Mocha had printed failure lines, then no summary came within the new 15 s limit, so the run was ended earlier. Their failing-test counts are not in the new receipt.
- **Hidden.** r-compression-v022b-6c-110420262217 and r-express-v022-6c-110420262124 are superseded and hidden. Two v0.2.3 runs that failed to start (launched as root, so bwrap could not reach the work folder) are hidden too: r-compression-v023-6c-110420262251 and r-express-v023-6c-110420262251. Nothing deleted.

### Correction to 0.2.3 (Nov 4, 2026, 11:34 PM ET)
- The 7 relabeled Express parts do have failing counts in the receipt (each part's unit-tests step, `guard.failing`, baseline 0): examples/auth users 2, application.js http 6, once 5, tryRender 58, response.js mime 585, stringify 254, view.js tryStat 46. The mocha summary counts are missing, because the summary never printed. The line above saying the counts are not in the receipt was wrong.
- runend.py docstring: the "within 60 s" line now names the quiet limit after the first failure line, and a v0.2.3 note sits beside Trey's 60 s rule.
