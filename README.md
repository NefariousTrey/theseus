# Theseus

_Blast radius that leaves only what's load-bearing._

Made by Trey with Grok.
Copyright (c) 2026 [@Nefarious_Trey on X](https://x.com/Nefarious_Trey). MIT License.

Theseus copies your project, deletes one part at a time from the copy, runs your tests, and marks each part
LOAD-BEARING or NOT NEEDED. You get a blast radius map. Nothing is deleted from anything until you say yes,
and even then Theseus only writes a NEW trimmed copy. Your original project is never written.

Live demo on Vultr Cloud Compute: https://66-135-26-143.sslip.io

Version 0.2.3. Python standard library only. No telemetry. No network inside the lockbox.

## How it works
1. **Copy.** Theseus copies the project into a bwrap lockbox: no network, no host home, no secrets, memory and time caps.
2. **Baseline.** It runs your tests on the untouched copy 3 times. If they disagree, it stops.
3. **Plan (Vultr agent).** An agent on Vultr Serverless Inference picks the cut order: the parts most likely to be dead go first.
4. **Cut and test.** For each part (a file or a top-level block), Theseus deletes it from a fresh copy and reruns the tests.
5. **Verdict (the tests decide).** The test results alone decide LOAD-BEARING or NOT NEEDED. The agent never makes a verdict.
6. **Explain (Vultr agent).** For each break, the agent writes one plain line on why it broke, labeled "The tests made the call".
7. **Approve.** You review the map. Only on your yes, Theseus writes a trimmed copy, reruns the tests on it, and hands you a .tar.gz.

Without a Vultr key, steps 3 and 6 fall back to simple rules. The verdicts are the same either way.

## Architecture
```
 browser ──HTTPS──> Caddy (TLS) ──> Theseus web (stdlib HTTP, 127.0.0.1)
                                     │   key: /etc/theseus/env (0640, never in the lockbox)
                                     ├──> Vultr Serverless Inference  (plan + explain only)
                                     └──> theseus.py scan --events
                                            └──> bwrap lockbox per cut (no net, caps, /etc secrets masked)
                                                   └──> your tests on a throwaway copy
 Host: one Vultr Cloud Compute VM, Ubuntu 24.04, 1 vCPU / 1 GB (+2 GB swap)
```

## Web app
- Public: pick a demo target, watch the scan live, click any tile on the blast radius map for its verdict, evidence and the agent's note.
- **Approve** exports a trimmed copy only, after a confirm. Nothing on the server or in your project is deleted.
- **Containment** runs the hostile demo and shows what the lockbox blocked.
- Admin (token login) unlocks bigger targets like Express. A repo URL scan exists but is off by default.
- Hardening: CSRF tokens tied to the cookie, same-origin checks, HttpOnly SameSite=Strict cookies, strict CSP (self only),
  no innerHTML, per-IP rate limits, one scan at a time, 16 KB JSON-only bodies, stalled clients dropped, redacted output.

## Deploy on Vultr
```
# on a fresh Ubuntu 24.04 VM, as root, from a copy of this folder
THESEUS_DOMAIN=<vm-ip-with-dashes>.sslip.io bash deploy/setup-vultr.sh
echo 'VULTR_INFERENCE_API_KEY=...' >> /etc/theseus/env && systemctl restart theseus-web
```
The script installs bwrap, Caddy and Node (checksum-checked), adds swap, makes a `theseus` system user,
opens only ports 22, 80 and 443, then runs `doctor` and `contain`. The admin token goes into /etc/theseus/env (root and theseus only).

## Commands
- `theseus.py doctor` checks this machine: bwrap version, overlay support, user namespaces, memory cap.
- `theseus.py selftest` runs every rule below on small made-up projects. It must say `SELFTEST: PASS`.
- `theseus.py scan TARGET --oracle O.json --out NEW [--agent]` maps every part.
- `theseus.py recheck --report R --all --out NEW` reruns the exact cuts on fresh copies.
- `theseus.py contain` shows what the lockbox blocks, as a table.
- `theseus.py apply --report R --dest NEW --approve` writes a NEW trimmed copy, then reruns the checks on it. Human yes only.
- `python3 web/server.py --port 8080` runs the web app.

## What each claim rests on
Every line here is checked by a named selftest case, or shown in the receipts.

| Claim | Proof |
|---|---|
| Each cut runs in a bwrap lockbox: no network, no host home, /usr read-only, no secret-like env vars, only the copy, a private /tmp and an empty HOME writable. | selftest containment probe (network, DNS, host home, /usr write, env names) |
| Each job has a memory cap (cgroup MemoryMax via systemd-run) and a time cap. | `memory_cap` (600 MB job killed under 128M), `timeout_label_and_recheck` |
| The lockbox env is fixed: clean empty HOME, TZ=UTC, C.UTF-8, PYTHONHASHSEED=0, CI=1. | `deterministic_env` |
| Symlinks are never parts and never followed. Writes stay inside the copy. | `symlink_write_through` |
| Copies are wiped after each run unless you ask for `--keep-copies`. A wipe outside the run folder is refused. | `keep_copies_and_wipe_guard` |
| `recheck` and `--repeat` flag cuts that do not repeat. | `recheck_repeatability` |
| A hang is rerun (with the longer cap after the first hang), at most 5 runs per part. Hung every time stays inconclusive. Hung once and passed later is flaky, kept load-bearing. Failures seen before the cap are noted. | `timeout_label_and_recheck`, `hang_partial_failure_note` |
| Test counts are read from mocha, jasmine, jasmine-node, jest, vitest, tap and node:test, pytest, go test and unittest. No summary means "unknown", never a guess. | `count_parsers` (15 summaries) |
| A cut is BROKEN if exit code changes, the test summary vanishes, passing drops, failing rises, skipped or pending rises, or a new load crash appears (missing module, import error, syntax error). Exit 0 alone is not enough. | `broken_rules_count_drop_skip_rise_load_crash` |
| The untouched copy runs at least 3 times. If the runs disagree, the scan stops. With `--allow-flaky` it maps parts but makes no bloat calls. | `flaky_baseline_guard` |
| If the untouched copy fails, nothing is cut and the work folder is removed. | `failed_baseline_cleanup` |
| Coverage honesty: only code the tests actually ran can be "proven bloat". Code that never ran is "UNTESTED: human call". Works for Python (sys.monitoring) and Node (V8 coverage), with nothing installed. | `coverage_python_untested_vs_bloat`, `coverage_node_untested_vs_bloat` |
| Test files are never bloat. Cutting a test that lowers the count is LOAD-BEARING (test). | `broken_rules...`, receipts |
| Docs, assets and config files are never code bloat. Config written as code (webpack.config.js, conftest.py, Gruntfile) counts as config. | `config_as_code_kind` |
| Short names (3 chars or less) only count as used on exact calls, members, imports or requires. Blast paths match on word edges. | `short_names_and_blast_paths` |
| Secret folders under /etc (Theseus's own key file, SSH keys, Wi-Fi passwords, TLS keys) are empty and read-only inside the lockbox. | `host_secret_dirs_masked` |
| The Vultr key lives only in the web server's env file. It is never passed into the lockbox, never logged, never in API output. | containment probe (env names), web red team |
| The scan was run on Express, Q and co. | receipts |

## What the labels mean
- **LOAD-BEARING**: the checks broke without this part. The report says why.
- **NOT NEEDED, proven-not-needed**: the tests ran this code, and nothing changed without it. A candidate to cut. A human still decides.
- **NOT NEEDED, UNTESTED: human call**: nothing broke, but the tests never ran it, or it is a doc, asset or config. Theseus cannot tell you if it matters.
- **NOT NEEDED, is-a-check**: a test file whose removal changed nothing. Never bloat.
- **TIMED OUT / inconclusive**: it hung. We do not guess.
- **Inconclusive, killed early**: the run was ended early, but every failing test also fails at baseline. We do not guess.

## When a run ends early (v0.2.1)
If a cut breaks the tests, that run did its job. Theseus ends it and moves to the next part. It does not wait for a hung run.

- **Printed failure, killed early.** Mocha printed its summary with 1 or more failing. Theseus waits 3 s for the failure list, then kills the run. It counts as load-bearing only if a failing test is new. New means its full title is not in the baseline's failing set. If the titles cannot be read, it falls back to "more failing than the baseline" and says so.
- **Failed, summary never printed.** Mocha printed a failure line, but no summary came within the quiet limit (see below). Theseus kills it and compares the failure count with the baseline.
- **Silent, counted broken.** The test command printed nothing for the quiet limit. v0.2.3: the limit is 3x the longest output gap seen in that step's baseline runs, never under 15 s and never over 60 s. The chosen limit is printed in every receipt. The timer starts from the last byte of output and runs only during the test command. This is counted broken under the quiet rule. It is not proven by a failing test.
- **Hit 600 s cap.** The old hard cap stays as a backstop.

The summary trigger is for mocha only. Other runners (jest, pytest, tap and the rest) keep the old time cap, plus the quiet rule. The quiet rule runs only on steps that name a test runner, or that set `"quiet_rule": true` in the oracle. Lint and install steps keep the old time cap only. Set `"quiet_rule": false` on a step to turn it off.

The baseline is never cut short. If the baseline itself goes silent for as long as the quiet limit, or hangs, the whole job stops as "baseline unusable".

Each run is in its own process group. Theseus sends SIGTERM to the group, then SIGKILL after 5 s. bwrap runs with `--die-with-parent`. After each run Theseus checks that nothing from it is left running, and the report counts any that were.

The report keeps at most 20 failure lines per part as evidence, with home folder paths removed.

## What Theseus does NOT catch
- It cuts whole files and whole top-level blocks. No line-level cuts yet.
- It only knows what your tests check. Behavior no test looks at can break silently.
- Each cut is tested alone. Two parts that are each "not needed" may be needed together.
- Node's own random order or seeds are not fixed by us. The 3 baseline runs only catch flakiness that shows up in 3 runs.
- A check with no count we can read (for example lint) is judged by exit code only.
- A test suite that silently runs nothing looks like "never ran". We warn when test files never ran at baseline.
- Coverage covers Python and Node only. Other languages get no bloat calls.
- The agent can be wrong about why something broke. Its notes are labeled, and they never change a verdict.
- Q counts come from its A+ promises suite only.

## Lawful use
**Acceptable Use.** This software may be used only for lawful purposes. You may not use it to harm, harass, defraud, deceive or exploit any person, or to infringe any person's rights, including rights to equal treatment, privacy and personal autonomy.

**Disclaimer of Warranty.** This software is provided "AS IS", without warranty of any kind, express or implied, including warranties of merchantability, fitness for a particular purpose and non-infringement. The authors developed it with reasonable care and due diligence and welcome feedback.

**Limitation of Liability.** In no event shall the authors be liable for any claim, damages or other liability arising from the use or misuse of this software, including any use that violates law or these terms.

Scan only code you own or have the right to test, and follow the license of every project you scan.

## License
MIT. See LICENSE and NOTICE.
Demo targets keep their own licenses: Express, Q and co are MIT, credited in each report.

## v0.2.2 notes (Nov 4, 2026, appended; the text above is kept as written)

- **Version.** This is Theseus 0.2.2 (the "Version 0.2.0" line above is old).
- **Live VM.** The demo now runs on Vultr vc2-6c-16gb: 6 vCPU, 16 GB, 12 workers (the "1 vCPU / 1 GB" line above is old).
- **Hangs.** "Inconclusive" now applies only to runners Theseus does not watch, and to runs that hit the 600 s cap. For mocha, silence for the quiet limit (v0.2.3: 3x the longest baseline output gap, 15 to 60 s) is counted broken under the quiet rule, not proven by a failing test. The report says which runs ended that way.
- **Grace.** After mocha prints a failing summary, Theseus waits about 3 s (longer only while output is still flowing, up to 30 s), then kills the run.
- **Demo target credit.** compression (https://github.com/expressjs/compression), MIT License, Copyright (c) its authors. It is scanned, not bundled.
- **Agent JSON.** The planner strips code fences and think tags, takes the largest complete JSON, retries up to 2 times with a strict prompt, and plans in chunks of 40 parts (halved when an answer is cut off). Rule order is used only after retries, and the report counts those fallbacks.
- **What goes to Vultr when the agent box is on.** The box is off by default. When on, Theseus sends Vultr Serverless Inference: the project folder name, part names, paths, kinds and line ranges (to plan the order); and for broken parts, up to 40 lines of that part's code plus short error lines (to explain the break and write the brief). Secret-looking strings are scrubbed before sending. Nothing else leaves the machine. No telemetry. Verdicts always come from the tests, never the model.
- **Times.** Reports print the server's local time; on the UTC server they also show US Eastern time.

### Correction (Nov 4, 2026, 10:28 PM ET)
- The agent reads the list of parts, plans the cut order, explains the biggest breaks in plain words, and writes the brief. It explains at most 25 load-bearing parts per scan (the engine's cap); scans started from the web page explain up to 20. The tests back each call with proof.
- When the agent box is on, Theseus also sends part sizes (line counts), whether a part's file ran under the tests, the scan's summary counts, up to 12 part ids for the brief, and up to 10 blast-radius file names per explanation. That plus the items listed above is everything that is sent.

### Correction (Nov 4, 2026, 10:37 PM ET)
- On the "Coverage covers Python and Node only" line above: cut-and-test works on any code that has a test command, because a verdict only needs the tests to pass or fail. Only the bloat check (proof that the tests covered a part) needs coverage, and that is Python or Node only. Other languages still get load-bearing and not-needed verdicts, just no proven-bloat calls.

## v0.2.3 notes (Nov 4, 2026, 10:50 PM ET): quiet limit and reruns

Trey's rule is unchanged: if a cut leaves the run dead or silent, that part is load-bearing (it breaks if deleted). v0.2.3 makes the wait shorter and checks unsure parts again.

- **Quiet limit.** 3x the longest output gap seen in that step's baseline runs, never under 15 s and never over 60 s. The old rule (the larger of 60 s and 10x the baseline run time) is gone. Each receipt prints the limit it used and the baseline gap it came from. If the longest baseline gap is over 20 s, the 60 s ceiling gives less than a 3x margin, and the receipt warns about it. If the baseline itself is quiet for as long as the limit, the scan stops as "baseline unusable".
- **Reruns.** A cut whose run was silent, timed out, or inconclusive runs again on a fresh copy. The hard cap is 5 runs per part, first run included. It is in code (`runend.MAX_RUNS`), and unit tests check that it cannot loop. A rerun after a time-out gets the long recheck cap. A clean pass or a proven failure on the first run is not rerun.
- **Verdicts after reruns.**
  - Every run passes cleanly: not needed. This is the only way to get not needed.
  - A proven failure, and no run passed: load-bearing.
  - Silent (or hung) every time, and no run passed: load-bearing, "silent, counted broken". This is the quiet rule, not a failing test.
  - Some runs pass and some do not after 5 runs: "flaky", kept load-bearing, because it is never safe to cut.
  - Hung or inconclusive every time: inconclusive. A hang alone is not a verdict.
- The separate one-time recheck pass for timed-out cuts is replaced by these reruns.

- **What the end labels mean, exactly.**
  - "Failed, summary never printed": mocha printed a failure line, and no summary came within the quiet limit, counted from the first failure line (15 s in the v0.2.3 receipts). The run is killed and its failure count is compared with the baseline.
  - "Silent, counted broken": the run printed nothing for the quiet limit, and no run of that part passed. This also covers a part whose runs were a mix of silent and unsure (timed out or inconclusive), with no pass.
  - 60 s of silence is always counted broken. v0.2.3 can stop sooner when the baseline prints often.
- **Version note.** The v0.2.2 notes above say "This is Theseus 0.2.2". The current version is 0.2.3 (line 14).

## v0.2.3 receipts and scope (Nov 5, 2026, appended)

Core loop (Trey): cut one part from a copy and run. If it breaks, the part is LOAD-BEARING and what broke is recorded. If it still runs, the part is NOT NEEDED, and proven bloat only when the tests covered it and nothing else needs it. Silent is counted broken. Theseus works on any project with a test suite. It is proven so far on 6 Node projects (Express, compression, Q, co, serve-index, body-parser). Never overclaim beyond that.

Live receipts on Vultr Cloud Compute (https://66-135-26-143.sslip.io/), Theseus v0.2.3, agent on Vultr Serverless Inference:
- expressjs/compression (r-compression-v023-6c-110420262256): 36 of 36 parts matched the prior run. 24 load-bearing, 12 not needed, 1 proven bloat. Quiet limit 15 s. 0 reruns.
- expressjs/express (r-express-v023-6c-110420262256): 402 of 402 verdicts matched the prior run. 354 load-bearing, 48 not needed, 0 proven bloat. Quiet limit 15 s. 0 reruns. Seven end labels moved to "failed, summary never printed"; all stayed LOAD-BEARING.

Install and run: see Commands and Deploy on Vultr above. MIT License (see LICENSE). Credit line: Made by Trey with Grok. Copyright (c) 2026 [@Nefarious_Trey on X](https://x.com/Nefarious_Trey). No telemetry. Lawful use terms are in the Lawful use section above.

## Vultr services used (Nov 5, 2026, appended)
- Vultr Cloud Compute: one VM runs the backend and the web app. Caddy serves HTTPS and passes requests to the Theseus app. All scans run on this VM with parallel workers.
- Vultr Serverless Inference: every agent LLM call (plan, explain, brief) goes to https://api.vultrinference.com/v1. No other LLM provider is used.
- Sandbox: each cut-and-test run happens inside bwrap. It gives each run its own network, process, IPC and user namespaces, an empty environment with no keys, a read-only system, and time and memory caps. We chose bwrap because it gives the same kind of isolation as a container without a root daemon running on the host.
- Docker: a Docker sandbox option is a possible future addition. It is not needed to run Theseus today.
