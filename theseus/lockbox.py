# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""The lockbox: run one command inside bubblewrap.

No network (own empty network namespace), no view of the user's home.
System dirs (/usr, /etc and the /bin, /lib links) are visible read-only.
Only the run's own working copy, a private /tmp and a clean empty HOME are
writable. Each step has a time cap and a memory cap. The environment is
cleared and then set to fixed values (TZ, locale, HOME, PYTHONHASHSEED), so
no API keys or tokens get in and runs do not depend on the host's settings.
"""
import os
import shutil
import signal
import subprocess
import threading
import time

from . import runend as RE

_LIVE = set()
_LIVE_LOCK = threading.Lock()
STOPPING = threading.Event()


def kill_all():
    """Stop every running lockbox job (a stopped scan must not leave jobs or copies behind)."""
    STOPPING.set()
    with _LIVE_LOCK:
        procs = list(_LIVE)
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    for p in procs:
        try:
            p.wait(timeout=10)
        except Exception:
            pass
    return len(procs)

SYSTEM_RO = ["/usr", "/etc"]
SANDBOX_HOME = "/home/theseus"
# fixed, host-independent env inside every run
FIXED_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": SANDBOX_HOME,
    "CI": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    "TERM": "dumb",
}
MERGED_LINKS = ["/bin", "/sbin", "/lib", "/lib64", "/lib32"]


def _scope_ok():
    if not shutil.which("systemd-run"):
        return False
    try:
        r = subprocess.run(
            ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=64M", "true"],
            capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def _to_bytes(mem):
    mem = str(mem).strip().upper()
    mult = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
    if mem[-1] in mult:
        return int(float(mem[:-1]) * mult[mem[-1]])
    return int(mem)


# Folders under the read-only system dirs that may hold host secrets. Each one that exists is
# covered by an empty tmpfs inside the lockbox. Add more with THESEUS_MASK_DIRS=/a:/b.
MASK_DIRS = ["/etc/theseus", "/etc/caddy", "/etc/ssh", "/etc/ssl/private", "/etc/sudoers.d",
             "/etc/NetworkManager/system-connections", "/etc/wireguard", "/etc/letsencrypt"]


def mask_dirs():
    extra = [d for d in os.environ.get("THESEUS_MASK_DIRS", "").split(":") if d.startswith("/")]
    out = []
    for d in MASK_DIRS + extra:
        d = os.path.normpath(d)
        if d not in out and os.path.isdir(d) and any(d.startswith(r + "/") for r in SYSTEM_RO):
            out.append(d)
    return out


class Lockbox:
    """bwrap + timeout + memory cap. One instance can run many jobs."""

    def __init__(self, memory="2G", env=None, overlays=None, tasks=4096):
        if not shutil.which("bwrap"):
            raise SystemExit("theseus: bubblewrap (bwrap) is required")
        self.memory = memory
        self.tasks = int(tasks)
        self.env = dict(env or {})
        # overlays: (host_dir, inside_path). Mounted as a throwaway overlay:
        # readable, writes vanish when the box exits, the host dir is untouched.
        self.overlays = list(overlays or [])
        self.scope = _scope_ok()

    def describe(self):
        return {
            "sandbox": "bubblewrap",
            "namespaces": "--unshare-all (net, ipc, pid, uts, cgroup, user)",
            "network": "none (own empty network namespace)",
            "host_home": "not mounted; HOME is a clean empty tmpfs at " + SANDBOX_HOME,
            "system_dirs": "read-only: " + ", ".join(SYSTEM_RO) + " (+ /bin /lib links)",
            "writable": "only the run's working copy at /work, a private /tmp and the clean HOME",
            "deterministic_env": ", ".join(f"{k}={v}" for k, v in FIXED_ENV.items() if k != "PATH")
                                 + "; Node has no global seed switch, so Math.random stays random",
            "dependency_dirs": "throwaway overlay (reads host copy, writes vanish)",
            "masked": "empty tmpfs over " + (", ".join(mask_dirs()) or "none present"),
            "env": "cleared; only " + ", ".join(sorted(set(FIXED_ENV) | set(self.env))),
            "die_with_parent": True,
            "time_cap": "per step, timeout --kill-after=5 (hard cap, backstop)",
            "run_end": "v0.2.1: whole process group killed with SIGTERM, then SIGKILL after 5 s; left-running check after each run",
            "memory_cap": (f"systemd-run --user --scope MemoryMax={self.memory} MemorySwapMax=0"
                           if self.scope else f"prlimit --as={self.memory} (fallback)"),
            "process_cap": f"TasksMax={self.tasks} (cgroup)" if self.scope else "none (no systemd user scope)",
        }

    def argv(self, workdir, cmd, timeout_s, extra_env=None, binds=None, ro_binds=None):
        a = []
        if self.scope:
            a += ["systemd-run", "--user", "--scope", "-q",
                  "-p", f"MemoryMax={self.memory}", "-p", "MemorySwapMax=0",
                  "-p", f"TasksMax={self.tasks}", "--"]
        else:
            a += ["prlimit", f"--as={_to_bytes(self.memory)}", "--"]
        a += ["timeout", "--kill-after=5", str(int(timeout_s))]
        a += ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
        for d in SYSTEM_RO:
            if os.path.isdir(d):
                a += ["--ro-bind", d, d]
        for link in MERGED_LINKS:
            if os.path.islink(link):
                a += ["--symlink", os.readlink(link), link]
            elif os.path.isdir(link):
                a += ["--ro-bind", link, link]
        for d in mask_dirs():  # host secrets under /etc are hidden behind an empty tmpfs
            a += ["--tmpfs", d, "--remount-ro", d]
        a += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", SANDBOX_HOME,
              "--bind", os.path.abspath(workdir), "/work"]
        for host, inside in binds or []:
            a += ["--bind", os.path.abspath(host), inside]
        for host, inside in ro_binds or []:
            a += ["--ro-bind", os.path.abspath(host), inside]
        for host, inside in self.overlays:
            a += ["--overlay-src", os.path.abspath(host), "--tmp-overlay", inside]
        a += ["--chdir", "/work"]
        env = dict(FIXED_ENV)
        env.update(self.env)
        env.update(extra_env or {})
        for k, v in env.items():
            a += ["--setenv", k, str(v)]
        a += ["sh", "-c", cmd]
        return a

    def run(self, workdir, cmd, timeout_s=300, extra_env=None, binds=None, ro_binds=None, watch=None):
        """Run one step. watch: None, or {"mocha": bool, "quiet_s": float|None, "no_summary_s": float}.

        The output is read as it comes. With watch set, the run-end rules (runend.py) can end
        the run early. Without it, only the hard cap ends it. Either way the whole process
        group is killed (SIGTERM, then SIGKILL after 5 s) and we check nothing is left running.
        """
        for _host, inside in self.overlays:
            rel = os.path.relpath(inside, "/work")
            if not rel.startswith(".."):
                os.makedirs(os.path.join(workdir, rel), exist_ok=True)
        if STOPPING.is_set():
            raise RuntimeError("theseus: stopping, no new lockbox jobs")
        t0 = time.time()
        # own session and process group, so a stop can kill the whole job tree at once
        p = subprocess.Popen(self.argv(workdir, cmd, timeout_s, extra_env, binds, ro_binds),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        with _LIVE_LOCK:
            _LIVE.add(p)
        w = RE.RunWatch(t0, mocha=(watch or {}).get("mocha"), quiet_s=(watch or {}).get("quiet_s"),
                        no_summary_s=(watch or {}).get("no_summary_s", RE.NO_SUMMARY_S))
        chunks, lock = [], threading.Lock()
        fd = p.stdout.fileno()

        def reader():
            while True:
                try:
                    b = os.read(fd, 65536)
                except OSError:
                    break
                if not b:
                    break
                with lock:
                    chunks.append(b)
                    w.feed(b.decode("utf-8", errors="replace"), time.time())

        th = threading.Thread(target=reader, daemon=True)
        th.start()
        early, tree, last_tree, end_t = None, set(), 0.0, None
        backstop = timeout_s + 15  # `timeout` inside argv is the real cap; this is a backstop
        try:
            while p.poll() is None:
                now = time.time()
                if now - last_tree >= 1.0:
                    tree |= _tree(p.pid)
                    last_tree = now
                if watch:
                    with lock:
                        why = w.check(now)
                    if why:
                        early = why
                        end_t = now
                        tree |= _tree(p.pid)
                        kill_group(p)
                        break
                if now - t0 > backstop:
                    tree |= _tree(p.pid)
                    kill_group(p)
                    break
                time.sleep(0.2)
            p.wait()
            if end_t is None:
                end_t = time.time()
            th.join(timeout=10)
        finally:
            try:
                p.stdout.close()
            except OSError:
                pass
            with _LIVE_LOCK:
                _LIVE.discard(p)
        secs = time.time() - t0
        rc = p.returncode
        left = _still_running(tree)
        with lock:
            out = b"".join(chunks).decode("utf-8", errors="replace")
        return {
            "cmd": cmd,
            "rc": rc,
            "seconds": round(secs, 2),
            "timed_out": early is None and rc in (124, 137, -9, -15) and secs >= timeout_s - 1,
            "killed": rc in (137, -9),
            "early_end": early,
            "max_quiet_s": round(w.gap(end_t), 2),
            "left_running": left,
            "output": RE.strip_home(out),
        }


def _proc_stat(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            s = f.read()
        rest = s[s.rindex(")") + 2:].split()
        return rest[0], int(rest[1]), rest[19]  # state, ppid, starttime
    except (OSError, ValueError, IndexError):
        return None


def _tree(root):
    """(pid, starttime) of root and every descendant, from /proc (host view sees namespaced kids)."""
    kids = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            st = _proc_stat(int(d))
            if st:
                kids.setdefault(st[1], []).append((int(d), st[2]))
    out, todo = set(), [root]
    rst = _proc_stat(root)
    if rst:
        out.add((root, rst[2]))
    while todo:
        for pid, start in kids.get(todo.pop(), []):
            if (pid, start) not in out:
                out.add((pid, start))
                todo.append(pid)
    return out


def _alive(tree):
    n = 0
    for pid, start in tree:
        st = _proc_stat(pid)
        if st and st[2] == start and st[0] not in ("Z", "X"):
            n += 1
    return n


def _still_running(tree, settle_s=5.0):
    """How many processes seen in this run's tree are still alive (zombies do not count).

    The kernel tears down a killed sandbox a moment after its parent exits, so a single
    look can catch a process mid-exit. We look again for up to settle_s seconds and count
    only what is still there at the end.
    """
    end = time.time() + settle_s
    n = _alive(tree)
    while n and time.time() < end:
        time.sleep(0.25)
        n = _alive(tree)
    return n


def kill_group(p, term_wait=5.0):
    """SIGTERM the run's whole process group, then SIGKILL after term_wait seconds.

    bwrap runs with --die-with-parent, so the sandboxed tree dies with it.
    """
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        p.wait(timeout=term_wait)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(p.pid, signal.SIGKILL)  # anything still in the group goes now
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def doctor():
    """Pre-flight for a new host (fix 6): can this machine run the lockbox?"""
    import tempfile
    out = {}
    bw = shutil.which("bwrap")
    out["bwrap"] = "found" if bw else "MISSING"
    ver = None
    if bw:
        try:
            v = subprocess.run([bw, "--version"], capture_output=True, text=True, timeout=10).stdout.split()[-1]
            ver = tuple(int(x) for x in v.split(".")[:3])
            out["bwrap_version"] = v
        except Exception as e:
            out["bwrap_version"] = f"unknown ({e})"
    out["overlay_support"] = ("ok (0.10+)" if ver and ver >= (0, 10) else
                              "NO: bwrap older than 0.10, env_dirs overlays (node_modules) will fail")
    out["memory_cap"] = ("systemd-run --user --scope (cgroup MemoryMax)" if _scope_ok()
                         else "prlimit --as fallback (address-space cap; Node may crash under it)")
    try:
        with tempfile.TemporaryDirectory(prefix="theseus-doctor-") as d:
            r = Lockbox(memory="256M").run(d, "true", 20) if bw else {"rc": -1, "output": "no bwrap"}
        out["user_namespaces"] = "ok" if r["rc"] == 0 else "FAIL: " + (r["output"].strip()[:200] or f"rc {r['rc']}")
    except BaseException as e:
        out["user_namespaces"] = f"FAIL: {e}"
    if out["user_namespaces"] != "ok":
        out["hint"] = ("Ubuntu 23.10+ blocks unprivileged user namespaces with AppArmor. An admin must allow "
                       "bwrap (an AppArmor profile) or set kernel.apparmor_restrict_unprivileged_userns=0.")
    out["running_as_root"] = os.geteuid() == 0
    out["ok"] = bool(bw) and out["user_namespaces"] == "ok"
    return out
