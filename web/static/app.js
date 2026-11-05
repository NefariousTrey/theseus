// Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
// No frameworks, no third-party scripts, no telemetry. Every server string goes in with textContent.
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const S = { csrf: "", admin: false, run: null, after: 0, parts: new Map(), report: null, timer: null, total: 0, done: 0, sel: null };

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") n.className = v; else if (k === "on") for (const [e, f] of Object.entries(v)) n.addEventListener(e, f);
      else n.setAttribute(k, v);
    }
    for (const c of kids.flat()) if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return n;
  }
  async function api(path, opts = {}) {
    const o = { credentials: "same-origin", headers: {} };
    if (opts.body !== undefined) {
      o.method = "POST"; o.headers["Content-Type"] = "application/json"; o.headers["X-Theseus-CSRF"] = S.csrf;
      o.body = JSON.stringify(opts.body);
    }
    const r = await fetch(path, o);
    const ct = r.headers.get("Content-Type") || "";
    const data = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error((data && data.error) || r.statusText);
    return data;
  }
  const isTestFile = (f) => /(^|\/)(test|tests|spec|__tests__)\//.test(f) || /\.(test|spec)\.[a-z]+$/.test(f) || /(^|\/)test_[^/]+\.py$/.test(f);
  function cls(p) {
    if (!p || !p.verdict) return "c-wait";
    if (p.verdict === "LOAD-BEARING") return (p.is_test || isTestFile(p.file)) ? "c-test" : "c-lb";
    if (p.verdict === "NOT NEEDED") {
      if (p.coverage === "proven-not-needed") return "c-bloat";
      if (p.coverage === "is-a-check") return "c-test";
      return "c-untested";
    }
    return "c-hung";
  }
  const label = (p) => p.verdict === "NOT NEEDED" ? `NOT NEEDED, ${p.coverage || ""}` : p.verdict;

  // ---- session ----
  async function session() {
    const s = await api("/api/session");
    S.csrf = s.csrf; S.admin = s.admin;
    $("agentChip").classList.toggle("on", !!s.agent_ready);
    $("agentChip").title = s.agent_ready ? "Agent ready" : "No inference key on this server: rule-based order and wording";
    $("adminBtn").textContent = s.admin ? "Admin: unlocked" : "Admin";
    $("repoRow").classList.toggle("hidden", !s.repo_urls);
  }
  async function targets() {
    const t = await api("/api/targets");
    const sel = $("target"); sel.replaceChildren();
    for (const x of t) sel.append(el("option", x.locked ? { value: x.id, disabled: "" } : { value: x.id }, x.name + (x.locked ? " (admin)" : "")));
  }
  async function runs() {
    const list = await api("/api/runs");
    const ul = $("runs"); ul.replaceChildren();
    if (!list.length) ul.append(el("li", { class: "hint" }, "No runs yet."));
    for (const r of list) {
      const s = r.summary;
      const line = s ? `${s.parts_checked} parts, ${s.load_bearing} load-bearing, ${s.bloat_proven} proven bloat` : (r.status || "");
      ul.append(el("li", {}, el("button", { type: "button", on: { click: () => openRun(r.id) } },
        (r.kind === "receipt" ? "Receipt: " : "") + (r.name || r.preset || r.id), el("small", {}, line))));
    }
  }

  // ---- a run ----
  function reset(title) {
    clearTimeout(S.timer);
    Object.assign(S, { after: 0, parts: new Map(), report: null, total: 0, done: 0, sel: null });
    $("runTitle").textContent = title; $("feed").replaceChildren(); $("map").replaceChildren();
    $("brief").classList.add("hidden"); $("warnings").replaceChildren(); $("planLine").textContent = "";
    $("detail").replaceChildren(el("h2", {}, "Part detail"), el("p", { class: "hint" }, "Click any tile."));
    $("approveList").replaceChildren(); $("approveBtn").disabled = true; $("approveOut").classList.add("hidden");
    $("barFill").style.width = "0"; stats(null);
  }
  async function openRun(id) {
    const m = await api(`/api/runs/${encodeURIComponent(id)}`);
    S.run = id; reset(m.name || id);
    if (m.note) $("runNote").textContent = m.note;
    status(m.status);
    poll();
  }
  function status(st) {
    const b = $("runStatus"); b.textContent = st || ""; b.className = "badge " + (st === "running" ? "running" : st === "done" ? "done" : st ? "bad" : "");
  }
  async function poll() {
    if (!S.run) return;
    const id = S.run;
    try {
      const r = await api(`/api/runs/${encodeURIComponent(id)}/events?after=${S.after}`);
      if (id !== S.run) return;
      for (const e of r.events) onEvent(e);
      S.after += r.events.length;
      status(r.status);
      if (r.status === "running" || r.events.length === 400) { S.timer = setTimeout(poll, 700); return; }
      await loadReport(id);
    } catch (e) { $("runNote").textContent = e.message; }
  }
  function onEvent(e) {
    const feed = $("feed");
    if (e.kind === "start") { S.total = e.parts; feed.append(el("li", {}, `Started: ${e.parts} file parts, ${e.workers} workers in parallel lockboxes.`)); }
    else if (e.kind === "baseline") {
      feed.append(el("li", {}, e.passed ? `Baseline: ${e.runs} untouched runs agree, ${e.tests ?? "?"} tests pass. ${e.coverage || ""}` : "Baseline failed: nothing is cut."));
      for (const w of e.warnings || []) $("warnings").append(el("div", { class: "warn" }, w));
    } else if (e.kind === "plan") {
      $("planLine").textContent = `Cut order (${e.phase}): ${e.source}`;
      feed.append(el("li", {}, `Plan for ${e.phase}: ${e.source}.`));
      const why = Object.entries(e.reasons || {}).slice(0, 3);
      for (const [k, v] of why) feed.append(el("li", { class: "hint" }, `agent: ${k}: ${v}`));
    } else if (e.kind === "phase") {
      if (e.parts) S.total = S.done + e.parts;
      feed.append(el("li", {}, `Now cutting ${e.phase}${e.parts ? `: ${e.parts} parts` : ""}.`));
    } else if (e.kind === "part") {
      S.done++; S.parts.set(e.id, e); paintTile(e);
      if (e.verdict !== "LOAD-BEARING" || S.done % 5 === 0) feed.append(el("li", {}, `${e.id}: ${label(e)}`));
    } else if (e.kind === "explain") feed.append(el("li", { class: "hint" }, `agent explained ${e.count ?? ""} breaks`));
    else if (e.kind === "done") feed.append(el("li", {}, "Done. The report is ready."));
    else if (e.kind === "stopped") feed.append(el("li", {}, `Stopped: ${e.reason || "killed"}. Sandbox copies wiped.`));
    feed.scrollTop = feed.scrollHeight;
    $("barFill").style.width = S.total ? Math.min(100, 100 * S.done / Math.max(S.total, S.done)) + "%" : "4%";
    liveStats();
  }
  function liveStats() {
    const c = { lb: 0, bloat: 0, untested: 0, hung: 0 };
    for (const p of S.parts.values()) { const k = cls(p).slice(2); if (k in c) c[k]++; }
    stats({ parts_checked: S.parts.size, load_bearing: c.lb, bloat_proven: c.bloat, untested_human_call: c.untested, timed_out_inconclusive: c.hung });
  }
  function stats(s) {
    const box = $("stats"); box.replaceChildren();
    if (!s) return;
    const items = [["", s.parts_checked, "parts checked"], ["lb", s.load_bearing, "load-bearing"], ["bloat", s.bloat_proven, "proven bloat"],
      ["untested", s.untested_human_call, "untested: human call"], ["hung", s.timed_out_inconclusive, "timed out / inconclusive"]];
    for (const [k, v, t] of items) box.append(el("div", { class: "stat " + k }, el("b", {}, v ?? 0), el("span", {}, t)));
  }

  // ---- map ----
  function dirOf(f) { const i = f.lastIndexOf("/"); return i < 0 ? "./" : f.slice(0, i + 1); }
  function tileFor(file) {
    let t = document.querySelector(`.tile[data-file="${CSS.escape(file)}"]`);
    if (t) return t;
    const d = dirOf(file);
    let sec = document.querySelector(`.dir[data-dir="${CSS.escape(d)}"]`);
    if (!sec) {
      const hint = $("map").querySelector("p.hint"); if (hint) hint.remove();
      sec = el("div", { class: "dir", "data-dir": d }, el("h3", {}, d), el("div", { class: "tiles" }));
      const all = [...$("map").children]; const after = all.find((x) => x.dataset.dir > d);
      $("map").insertBefore(sec, after || null);
    }
    t = el("button", { type: "button", class: "tile c-wait", "data-file": file, title: file, on: { click: () => detail(file) } },
      file.slice(d === "./" ? 0 : d.length), el("span", { class: "blocks" }));
    sec.querySelector(".tiles").append(t);
    return t;
  }
  function paintTile(p) {
    const t = tileFor(p.file);
    if (p.level === "file") { t.className = "tile " + cls(p) + (S.sel === p.file ? " sel" : ""); }
    else {
      const b = t.querySelector(".blocks");
      let i = b.querySelector(`i[data-id="${CSS.escape(p.id)}"]`);
      if (!i) { i = el("i", { "data-id": p.id }); b.append(i); }
      i.className = cls(p); i.title = `${p.id}: ${label(p)}`;
      if (t.classList.contains("c-wait")) t.className = "tile c-lb" + (S.sel === p.file ? " sel" : "");
    }
  }
  function detail(file) {
    S.sel = file;
    document.querySelectorAll(".tile.sel").forEach((x) => x.classList.remove("sel"));
    const t = document.querySelector(`.tile[data-file="${CSS.escape(file)}"]`); if (t) t.classList.add("sel");
    const box = $("detail"); box.replaceChildren(el("h2", {}, "Part detail"));
    const rows = [...S.parts.values()].filter((p) => p.file === file).sort((a, b) => (a.level === "file" ? -1 : 1) - (b.level === "file" ? -1 : 1) || (a.first || 0) - (b.first || 0));
    for (const p of rows) {
      const why = p.why ? (Array.isArray(p.why) ? p.why : Object.values(p.why).flat()) : [];
      const dl = el("dl", {},
        el("dt", {}, "part"), el("dd", { class: "mono" }, p.id),
        el("dt", {}, "verdict"), el("dd", {}, label(p)),
        why.length ? [el("dt", {}, "the tests said"), el("dd", {}, why.join("; "))] : null,
        p.error ? [el("dt", {}, "first error"), el("dd", { class: "mono" }, p.error)] : null,
        (p.radius && p.radius.length) ? [el("dt", {}, "blast radius"), el("dd", { class: "mono" }, p.radius.join(", "))] : null,
        (p.notes && p.notes.length) ? [el("dt", {}, "notes"), el("dd", {}, p.notes.join(" "))] : null);
      const card = el("div", { class: "detail" }, dl);
      if (p.explain && (p.explain.ai || p.explain.rule_based)) {
        card.append(el("div", { class: "ai" },
          el("small", {}, p.explain.ai ? `In plain words, by the agent (${p.explain.source || "Vultr Serverless Inference"}). The tests made the call.` : "In plain words (rule-based)."),
          p.explain.ai || p.explain.rule_based));
      }
      box.append(card, el("hr", { class: "sep" }));
    }
  }

  // ---- report ----
  async function loadReport(id) {
    let rep;
    try { rep = await api(`/api/runs/${encodeURIComponent(id)}/report`); } catch (e) { return; }
    if (id !== S.run) return;
    S.report = rep;
    for (const p of rep.parts) { S.parts.set(p.id, p); paintTile(p); }
    stats(rep.summary); $("barFill").style.width = "100%";
    const m = rep.meta;
    $("runTitle").textContent = `${m.target_name || id}${m.commit ? " @ " + m.commit.slice(0, 10) : ""}`;
    if (m.agent_brief) { $("brief").replaceChildren(el("small", { class: "hint" }, "The brief (agent wording; tests decided every verdict)"), el("p", {}, m.agent_brief)); $("brief").classList.remove("hidden"); }
    $("warnings").replaceChildren(...(m.warnings || []).map((w) => el("div", { class: "warn" }, w)));
    if (m.target_credit) $("warnings").append(el("p", { class: "hint" }, "Target credit: " + m.target_credit));
    approveList();
  }
  function scope() { return document.querySelector("input[name=scope]:checked").value; }
  function approveList() {
    const box = $("approveList"); box.replaceChildren();
    if (!S.report) return;
    const sc = scope();
    const keep = /^(LICENSE|README|NOTICE)/i;
    const pick = S.report.parts.filter((p) => p.verdict === "NOT NEEDED" && p.coverage !== "is-a-check" && !p.is_test && !isTestFile(p.file)
      && !(p.level === "file" && keep.test(p.file.split("/").pop())) && (sc === "all" || p.coverage === "proven-not-needed"));
    if (!pick.length) box.append(el("p", { class: "hint" }, "Nothing to trim at this scope."));
    for (const p of pick) box.append(el("div", { class: "approveItem" }, `- ${p.id}  (${p.coverage})`));
    $("approveBtn").disabled = !pick.length || S.run.startsWith("r-") && false;
  }

  // ---- actions ----
  $("runBtn").addEventListener("click", async () => {
    $("runNote").textContent = "";
    const body = { target: $("target").value, agent: $("agent").checked };
    const repo = $("repo").value.trim(); if (repo && !$("repoRow").classList.contains("hidden")) body.repo = repo;
    try {
      $("runBtn").disabled = true;
      const r = await api("/api/scan", { body });
      if (r.note) $("runNote").textContent = r.note;
      await runs(); await openRun(r.id);
    } catch (e) { $("runNote").textContent = e.message; } finally { setTimeout(() => ($("runBtn").disabled = false), 1500); }
  });
  $("approveBtn").addEventListener("click", async () => {
    const sc = scope();
    if (!confirm(`Write a NEW trimmed copy without the ${sc === "bloat" ? "proven bloat" : "listed"} parts and re-run the tests on it? The original is not touched.`)) return;
    const out = $("approveOut"); out.classList.remove("hidden"); out.textContent = "Writing the trimmed copy and re-running the tests in the lockbox...";
    $("approveBtn").disabled = true;
    try {
      const r = await api(`/api/runs/${encodeURIComponent(S.run)}/approve`, { body: { scope: sc, confirm: true } });
      out.replaceChildren(document.createTextNode((r.verified ? "PASS: the trimmed copy still passes every test.\n\n" : "FAIL: the trimmed copy did not pass. Do not ship it.\n\n") + r.log + "\n"),
        el("a", { href: r.download, download: "" }, "Download the trimmed copy (.tar.gz)"));
    } catch (e) { out.textContent = e.message; } finally { $("approveBtn").disabled = false; }
  });
  document.querySelectorAll("input[name=scope]").forEach((r) => r.addEventListener("change", approveList));
  $("containBtn").addEventListener("click", async () => {
    const p = $("containPanel"), out = $("containOut"); p.classList.remove("hidden");
    out.replaceChildren(el("p", { class: "hint" }, "Running the hostile project inside the lockbox..."));
    $("containBtn").disabled = true;
    try {
      const r = await api("/api/contain", { body: {} });
      const tb = el("table", { class: "contain" }, el("tr", {}, el("th", {}, "What the hostile code tried"), el("th", {}, "Held"), el("th", {}, "What happened")));
      for (const row of r.rows) tb.append(el("tr", {}, el("td", {}, row.attempt), el("td", { class: row.held ? "held" : "escaped" }, row.held ? "yes" : "NO"), el("td", { class: "mono" }, row.result)));
      out.replaceChildren(el("p", {}, el("b", { class: r.all_held ? "held" : "escaped" }, r.all_held ? "CONTAINMENT HELD: every attempt failed." : "CONTAINMENT FAILED: see rows marked NO.")), tb);
      p.scrollIntoView({ behavior: "smooth" });
    } catch (e) { out.replaceChildren(el("p", {}, e.message)); } finally { $("containBtn").disabled = false; }
  });
  $("adminBtn").addEventListener("click", () => $("adminDlg").showModal());
  $("closeDlg").addEventListener("click", () => $("adminDlg").close());
  $("adminForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await api("/api/login", { body: { token: $("token").value } }); $("adminMsg").textContent = "Unlocked."; $("token").value = ""; await session(); await targets(); }
    catch (e) { $("adminMsg").textContent = e.message; }
  });
  $("logoutBtn").addEventListener("click", async () => { await api("/api/logout", { body: {} }); $("adminMsg").textContent = "Locked."; await session(); await targets(); });

  (async () => {
    try {
      await session(); await targets(); await runs();
      const first = document.querySelector("#runs button"); if (first) first.click();
    } catch (e) { $("runNote").textContent = "Could not reach the server: " + e.message; }
  })();
})();
