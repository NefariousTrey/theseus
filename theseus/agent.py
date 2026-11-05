# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""The agent: Vultr Serverless Inference plans the cut order and explains breaks in plain words.

The agent gives advice only. It never decides a verdict: the tests do
(engine.judge). It never runs inside the lockbox, and the key never enters
it: the lockbox starts from an empty environment (--clearenv). Everything sent
to the model is cut short and scrubbed of secret-looking strings first.
With no key, Theseus falls back to a rule-based order and rule-based wording,
and says so in the report.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.vultrinference.com/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash-1017"
# USD per 1M tokens (in, out), from api.vultrinference.com/docs/models, read 11/04/2026
PRICES = {"deepseek-v4.1-flash-1017": (0.15, 0.60), "qwen3.8-flash-next": (0.10, 0.20),
          "glm-5.3-flash": (0.10, 0.35), "qwen3.8-27b": (0.15, 1.00)}
KEY_ENV = ("THESEUS_LLM_KEY", "VULTR_INFERENCE_API_KEY")

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(bearer|token|api[_-]?key|secret|password|passwd|authorization)\b(\s*[:=]\s*|\s+)['\"]?[^\s'\"]{6,}"),
    re.compile(r"\b(sk|pk|rk|ghp|gho|ghs|github_pat|xox[abpr]|glpat|AKIA|ASIA)[-_A-Za-z0-9]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b"),  # JWT
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
]


def redact(text, limit=None):
    """Scrub secret-looking strings and host paths. Used for prompts, logs and the web view."""
    t = str(text or "")
    for k in KEY_ENV:
        v = os.environ.get(k)
        if v and len(v) >= 8:
            t = t.replace(v, "[redacted]")
    for p in SECRET_PATTERNS:
        t = p.sub("[redacted]", t)
    home = os.path.expanduser("~")
    if home and home != "/":
        t = t.replace(home, "~")
    if limit and len(t) > limit:
        t = t[:limit] + " …"
    return t


class Agent:
    """A small OpenAI-compatible client for Vultr Serverless Inference (Python stdlib only)."""

    def __init__(self, key=None, base=None, model=None, max_calls=None, timeout=60):
        self.key = key if key is not None else next((os.environ[k] for k in KEY_ENV if os.environ.get(k)), None)
        self.base = (base or os.environ.get("THESEUS_LLM_BASE") or DEFAULT_BASE).rstrip("/")
        self.model = model or os.environ.get("THESEUS_LLM_MODEL") or DEFAULT_MODEL
        self.max_calls = int(max_calls or os.environ.get("THESEUS_LLM_MAX_CALLS") or 80)
        self.timeout = timeout
        self.calls = 0
        self.failures = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.answered_by = set()
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return bool(self.key)

    def describe(self):
        pin, pout = PRICES.get(self.model, (None, None))
        cost = None if pin is None else round((self.tokens_in * pin + self.tokens_out * pout) / 1e6, 5)
        host = re.sub(r"^https?://", "", self.base).split("/")[0]
        return {"service": "Vultr Serverless Inference" if host.endswith("vultrinference.com") else host,
                "model": self.model, "answered_by": sorted(self.answered_by), "enabled": self.enabled,
                "calls": self.calls, "failures": self.failures,
                "tokens_in": self.tokens_in, "tokens_out": self.tokens_out, "est_cost_usd": cost,
                "role": "plans cut order and explains breaks; verdicts come from the tests only"}

    def chat(self, system, user, max_tokens=700, want_json=False):
        if not self.enabled:
            raise RuntimeError("no inference key")
        with self._lock:
            if self.calls >= self.max_calls:
                raise RuntimeError("agent call budget reached")
            self.calls += 1
        body = {"model": self.model, "max_completion_tokens": max_tokens, "temperature": 0.2,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": redact(user, 12000)}]}
        if want_json:
            body["response_format"] = {"type": "json_object"}
        if os.environ.get("THESEUS_LLM_THINKING", "0") != "1":
            # hybrid models (e.g. deepseek-v4.1-flash) otherwise spend the whole token budget thinking
            # and return empty content; unknown kwargs are ignored by other models
            body["chat_template_kwargs"] = {"thinking": False, "enable_thinking": False}
        data = json.dumps(body).encode()
        last = None
        for attempt in range(3):
            req = urllib.request.Request(self.base + "/chat/completions", data=data, method="POST", headers={
                "Authorization": "Bearer " + self.key, "Content-Type": "application/json",
                "User-Agent": "theseus-agent"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    out = json.loads(r.read().decode("utf-8", "replace"))
                u = out.get("usage") or {}
                with self._lock:
                    self.tokens_in += int(u.get("prompt_tokens") or 0)
                    self.tokens_out += int(u.get("completion_tokens") or 0)
                    if out.get("model"):
                        self.answered_by.add(str(out["model"]))
                return (out["choices"][0]["message"].get("content") or "").strip()
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError) as e:
                last = type(e).__name__
            time.sleep(1.5 * (attempt + 1))
        with self._lock:
            self.failures += 1
        raise RuntimeError("inference failed: " + str(last))  # never includes the key


class Truncated(ValueError):
    """The answer started JSON but was cut off (size limit)."""


def _balanced(text, i):
    """End index (exclusive) of the balanced {...} or [...] starting at i, or None if cut off."""
    stack, q, esc = [], False, False
    for j in range(i, len(text)):
        c = text[j]
        if q:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': q = False
            continue
        if c == '"': q = True
        elif c in "{[": stack.append("}" if c == "{" else "]")
        elif c in "}]":
            if not stack or c != stack.pop():
                return -1
            if not stack:
                return j + 1
    return None


def _json_from(text):
    """Parse JSON from a model answer: strips think tags and code fences, takes the largest
    balanced {...} or [...] that parses, tolerates text before and after."""
    t = text or ""
    t = re.sub(r"<think>[\s\S]*?</think>", "", t, flags=re.I)
    t = re.sub(r"<think>[\s\S]*$", "", t, flags=re.I) if "</think>" not in t.lower() and "<think>" in t.lower() else t
    t = re.sub(r"```(?:json|JSON)?", "", t)
    best, cut = None, False
    i = 0
    while i < len(t):
        if t[i] not in "{[":
            i += 1
            continue
        e = _balanced(t, i)
        if e is None:
            cut = True
            i += 1
            continue
        if e == -1:
            i += 1
            continue
        try:
            v = json.loads(t[i:e])
        except ValueError:
            i += 1
            continue
        if best is None or e - i > best[0]:
            best = (e - i, v)
        i = e  # an outer value already holds everything inside it
    if best is None:
        if cut:
            raise Truncated("answer cut off before the JSON closed")
        raise ValueError("no JSON object in the answer")
    v = best[1]
    if isinstance(v, list):
        v = {"order": v}
    return v


STRICT = ("Reply with ONLY one JSON object, no prose, no markdown, no code fences. "
          "Keep every reason under 12 words.")


def ask_json(agent, system, user, max_tokens=1500, retries=2):
    """Ask for JSON; on a bad answer retry up to `retries` times with a stricter prompt.
    Returns (obj, tries). Raises the last error if every try fails."""
    last = None
    for k in range(retries + 1):
        sys_k = system if k == 0 else system + " " + STRICT
        try:
            return _json_from(agent.chat(sys_k, user, max_tokens=max_tokens, want_json=True)), k + 1
        except Truncated as e:
            last = e
            max_tokens = int(max_tokens * 1.5)
        except ValueError as e:
            last = e
    raise last


def rule_rank(p, scan):
    """Rule order: parts most likely to be removable first, so bloat shows up early."""
    f = p["file"]
    ran = scan.cov.file_ran(f) if scan.cov.tools else None
    kind_w = {"code": 0, "config": 2, "doc": 3, "asset": 3, "test": 4}.get(p.get("kind"), 2)
    size = (p["last"] - p["first"] + 1) if p["level"] == "block" else 0
    return (kind_w, 0 if ran else 1, -size, p["id"])


PLAN_SYSTEM = (
    "You plan the order of a code-removal experiment. Each part will be deleted from a throwaway copy, "
    "one at a time, and the project's own tests decide what breaks. You do not decide any result. "
    "Put first the parts most likely to be removable with nothing breaking (unused helpers, legacy or "
    "duplicate code, optional plugins, polyfills), and last the parts that are clearly core. "
    "Answer with JSON only: {\"order\": [part ids], \"reasons\": {part id: \"max 12 words\"}}. "
    "Use only ids from the list. Give reasons for at most 8 parts.")


def make_planner(agent, cap=120, chunk=40):
    def planner(plist, phase, scan):
        base = sorted(plist, key=lambda p: rule_rank(p, scan))
        if not agent.enabled:
            return base, {"source": "rule order (no Vultr inference key set)"}
        head = base[:cap]
        rows = []
        for p in head:
            ran = scan.cov.file_ran(p["file"]) if scan.cov.tools else None
            rows.append({"id": p["id"], "kind": p.get("kind"),
                         "lines": (p["last"] - p["first"] + 1) if p["level"] == "block" else None,
                         "tests_loaded_file": ran})
        ids = {p["id"]: p for p in head}
        order, seen, reasons, tries, chunks, fails = [], set(), {}, 0, 0, []
        size = chunk
        i = 0
        while i < len(rows):
            part = rows[i:i + size]
            user = (f"Project: {os.path.basename(scan.target)}. Phase: {phase} "
                    f"({'whole files' if phase == 'files' else 'top-level blocks inside files that tests need'}).\n"
                    "Parts:\n" + json.dumps(part))
            try:
                ans, n = ask_json(agent, PLAN_SYSTEM, user, max_tokens=1500)
                tries += n
            except Truncated as e:
                tries += 3
                if size > 10:  # answer too long: ask again with a shorter list
                    size = max(10, size // 2)
                    continue
                fails.append(str(e)); i += len(part); continue
            except Exception as e:
                tries += 3
                fails.append(redact(str(e), 80)); i += len(part); continue
            chunks += 1
            got = 0
            for x in ans.get("order") or []:
                if isinstance(x, str) and x in ids and x not in seen and any(r["id"] == x for r in part):
                    order.append(ids[x]); seen.add(x); got += 1
            if not got:
                fails.append("answer had no known part ids")
            order += [ids[r["id"]] for r in part if r["id"] not in seen]
            seen.update(r["id"] for r in part)
            for k, v in (ans.get("reasons") or {}).items():
                if k in ids and len(reasons) < 8:
                    reasons[k] = redact(str(v), 120)
            i += len(part)
        if not chunks:
            return base, {"source": f"rule order (agent unavailable after retries: {'; '.join(fails)[:120]})",
                          "fallback": True, "agent_tries": tries}
        order += [p for p in head if p["id"] not in seen] + base[cap:]
        info = {"source": f"agent ({agent.model} on {agent.describe()['service']})",
                "agent_ranked": len(seen), "reasons": reasons, "agent_tries": tries, "agent_chunks": chunks}
        if fails:
            info["fallback_chunks"] = len(fails)
            info["fallback_why"] = "; ".join(fails)[:160]
        return order, info
    return planner


EXPLAIN_SYSTEM = (
    "A part of a software project was deleted from a throwaway copy and the project's tests were run. "
    "The tests already decided the part is load-bearing; do not argue with that. In 2 or 3 short, plain "
    "sentences for a busy engineer, say what this part does and why the tests broke without it, based "
    "only on the evidence given. Do not invent file names or functions. No markdown.")


def rule_words(rec):
    wb = rec.get("what_broke") or {}
    why = "; ".join(r for v in (wb.get("why") or {}).values() for r in v) or "a check failed"
    first = (wb.get("error_lines") or [""])[0]
    rad = rec.get("blast_radius_files") or []
    s = f"Without {rec['part']['id']}, the checks broke: {why}."
    if first:
        s += f" First error: {first[:160]}."
    if rad:
        s += f" Files named in the failure: {', '.join(rad[:4])}{' and more' if len(rad) > 4 else ''}."
    return s


def explain(agent, rec, target):
    """Plain words for one load-bearing cut. The rule-based sentence is kept beside the AI one."""
    out = {"rule_based": redact(rule_words(rec), 600)}
    if not agent.enabled:
        out["source"] = "rule-based (no Vultr inference key set)"
        return out
    p = rec["part"]
    snippet = ""
    try:
        lines = open(os.path.join(target, p["file"]), encoding="utf-8").read().split("\n")
        lo, hi = (p["first"], p["last"] + 1) if p["level"] == "block" else (0, min(len(lines), 40))
        snippet = "\n".join(lines[lo:min(hi, lo + 40)])
    except (OSError, UnicodeDecodeError, KeyError):
        pass
    wb = rec.get("what_broke") or {}
    user = ("Deleted part: " + p["id"] + "\n\nCode of the part (cut short):\n" + redact(snippet, 2500)
            + "\n\nWhy the tests counted it as broken: " + json.dumps(wb.get("why")) +
            "\nFirst error lines:\n" + redact("\n".join(wb.get("error_lines") or []), 1500)
            + "\nOther files named in the failure: " + ", ".join((rec.get("blast_radius_files") or [])[:10]))
    try:
        out["ai"] = redact(agent.chat(EXPLAIN_SYSTEM, user, max_tokens=300), 700)
        out["source"] = f"AI explanation by {agent.model} on {agent.describe()['service']}; the verdict came from the tests"
    except Exception as e:
        out["source"] = f"rule-based (agent unavailable: {redact(str(e), 80)})"
    return out


BRIEF_SYSTEM = (
    "Write a 4 to 6 sentence brief for an engineering lead about a blast radius scan: what is load-bearing, "
    "what was proven safe to cut, what is untested and needs a human call, and the safest next step. Use only "
    "the numbers and part names given. Never call untested parts safe. Never say anything was deleted: "
    "nothing is deleted until a human approves, and approval only writes a trimmed copy. No markdown.")


def brief(agent, summary, recs):
    if not agent.enabled:
        return None
    top_lb = [r["part"]["id"] for r in recs if r["verdict"] == "LOAD-BEARING"][:12]
    proven = [r["part"]["id"] for r in recs if r.get("coverage") == "proven-not-needed"][:12]
    user = json.dumps({"summary": summary, "some_load_bearing": top_lb, "proven_bloat": proven})
    try:
        return redact(agent.chat(BRIEF_SYSTEM, user, max_tokens=400), 1200)
    except Exception:
        return None


def explain_all(agent, recs, target, max_n=25, workers=4):
    """Explain the load-bearing code cuts with the widest blast radius first (tests last)."""
    from concurrent.futures import ThreadPoolExecutor
    lb = [r for r in recs if r["verdict"] == "LOAD-BEARING"]
    lb.sort(key=lambda r: (bool(r.get("is_test")), -len(r.get("blast_radius_files") or []), r["part"]["id"]))
    pick = lb[:max_n]
    with ThreadPoolExecutor(workers) as ex:
        for r, words in zip(pick, ex.map(lambda r: explain(agent, r, target), pick)):
            r["explain"] = words
    for r in lb[max_n:]:
        r["explain"] = {"rule_based": redact(rule_words(r), 600), "source": "rule-based (past the AI explain limit)"}
    return len(pick)
