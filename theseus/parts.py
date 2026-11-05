# Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License. Made by Trey with Grok.
"""Find the parts (planks) of a project: whole files first, then top-level blocks."""
import bisect
import os
import re

# Treated as environment, never deletion candidates.
ENV_DEFAULT = [
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", ".output",
    ".vercel", ".next", "build", "target",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    ".node_modules.lock", "poetry.lock", "Cargo.lock", "uv.lock",
]

CODE_EXT = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".py"}
TEXT_EXT = CODE_EXT | {".json", ".md", ".css", ".html", ".sql", ".sh", ".toml",
                       ".yaml", ".yml", ".svg", ".txt", ".d.ts"}
DOC_NAMES = {"LICENSE", "LICENSE.md", "README.md", "SPEC.md", "CHANGELOG.md", "NOTICE"}

DECL = re.compile(
    r"^(export\s+)?(default\s+)?(declare\s+)?(async\s+)?"
    r"(function\*?|class|abstract\s+class|const|let|var|interface|type|enum)\s"
    r"|^(async\s+)?def\s|^class\s|^@")
NAME = re.compile(
    r"(?:function\*?|class|const|let|var|interface|type|enum|def)\s+([A-Za-z_$][\w$]*)")
COMMENT = re.compile(r"^\s*(//|/\*|\*|#)")


def list_files(root, env=ENV_DEFAULT):
    env = set(env)
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in env)
        # never follow or count symlinks (they can point outside the copy)
        dns[:] = [d for d in dns if not os.path.islink(os.path.join(dp, d))]
        for fn in sorted(fns):
            if fn in env or os.path.islink(os.path.join(dp, fn)):
                continue
            out.append(os.path.relpath(os.path.join(dp, fn), root))
    return sorted(out)


TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}


def is_test_file(path):
    """Tests are checks, never bloat."""
    segs = path.replace(os.sep, "/").split("/")
    if any(s in TEST_DIRS for s in segs[:-1]):
        return True
    return bool(re.search(r"\.(test|spec)\.[A-Za-z0-9]+$|^test_.*\.py$|_test\.py$", segs[-1]))


def safe_path(copy_root, rel):
    """Resolve a part inside the copy; refuse links and anything that lands outside."""
    root = os.path.realpath(copy_root)
    fp = os.path.join(copy_root, rel)
    if os.path.islink(fp):
        raise PermissionError(f"theseus: refusing to touch symlink {rel}")
    real = os.path.realpath(fp)
    if real != root and not real.startswith(root + os.sep):
        raise PermissionError(f"theseus: refusing write outside the copy: {rel}")
    return real


# Config written as code (fix 8): it configures tools, it is not the product.
CONFIG_CODE = re.compile(
    r"\.config\.[cm]?[jt]sx?$|^\.(eslintrc|prettierrc|babelrc|mocharc|nycrc|stylelintrc|"
    r"lintstagedrc|swcrc|c8rc|jshintrc)(\.[a-z]+)?$|^(Gruntfile|Gulpfile|gulpfile|Jakefile|karma\.conf|"
    r"webpack\.config|rollup\.config|conftest|setup|noxfile|fabfile)\.(js|cjs|mjs|ts|py|coffee)$")


def kind_of(path):
    if is_test_file(path):
        return "test"
    base = os.path.basename(path)
    ext = os.path.splitext(base)[1].lower()
    if base in DOC_NAMES or ext in {".md", ".txt"}:
        return "doc"
    if CONFIG_CODE.search(base):
        return "config"
    if ext in CODE_EXT:
        return "code"
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".woff", ".woff2"}:
        return "asset"
    return "config"


def _toplevel(line):
    if not line.strip() or line[0] in " \t})]":
        return False
    return not COMMENT.match(line)


def find_blocks(text):
    """Top-level declarations as (name, first_line, last_line), 0-based inclusive.

    Leading comments (JSDoc etc.) stay with the block they describe. A plain
    line scanner, not a parser: if it cuts a block badly the copy fails to
    build, which errs toward LOAD-BEARING, never toward deleting too much.
    """
    lines = text.split("\n")
    n = len(lines)
    blocks = []
    i = 0
    last_end = -1
    while i < n:
        if not DECL.match(lines[i]):
            i += 1
            continue
        s = i
        j = s + 1
        if lines[s].startswith("@"):
            while j < n and lines[j].startswith("@"):
                j += 1
            if j < n and re.match(r"(async\s+)?(def|class)\s", lines[j]):
                j += 1
        while j < n and not _toplevel(lines[j]):
            j += 1
        e = j - 1
        while e > s and (not lines[e].strip() or COMMENT.match(lines[e])):
            e -= 1
        b = s
        while b - 1 > last_end and COMMENT.match(lines[b - 1]):
            b -= 1
        m = NAME.search(lines[s] if not lines[s].startswith("@") else " ".join(lines[s:j]))
        name = m.group(1) if m else f"block{len(blocks) + 1}"
        blocks.append((name, b, e))
        last_end = e
        i = max(j, e + 1)
    return blocks


def remove_lines(text, first, last):
    lines = text.split("\n")
    return "\n".join(lines[:first] + lines[last + 1:])


CONVENTION_DIRS = ("routes", "pages", "app", "api", "middleware", "migrations", "plugins")


def convention_loaded(path):
    """Frameworks load some files by where they sit, not by being imported."""
    return any(seg in CONVENTION_DIRS for seg in path.split("/")[:-1])


def file_part(path):
    return {"id": path, "level": "file", "file": path, "kind": kind_of(path)}


def block_parts(root, path):
    fp = os.path.join(root, path)
    try:
        text = open(fp, encoding="utf-8").read()
    except (UnicodeDecodeError, OSError):
        return []
    parts = []
    for name, b, e in find_blocks(text):
        parts.append({
            "id": f"{path}::{name}@L{b + 1}-{e + 1}",
            "level": "block", "file": path, "kind": "code",
            "name": name, "first": b, "last": e,
        })
    return parts


def apply_removal(copy_root, part):
    fp = safe_path(copy_root, part["file"])
    if part["level"] == "file":
        os.remove(fp)
    else:
        text = open(fp, encoding="utf-8").read()
        fd = os.open(fp, os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(remove_lines(text, part["first"], part["last"]))


SHORT = 3  # names this short only count on an exact code use, never as a plain word


def _short_use(name):
    """Exact code uses of a short name: a call, a member access, or an import/require/export of it."""
    n = re.escape(name)
    return re.compile(
        r"(?<![\w$.])" + n + r"\s*\(|\.\s*" + n + r"(?![\w$])|"
        r"^\s*(?:import|export)\b[^\n]*(?<![\w$])" + n + r"(?![\w$])|"
        r"\brequire\(\s*['\"][^'\"]*(?<![\w$])" + n + r"['\"]|"
        r"^\s*from\s+\S+\s+import\b[^\n]*(?<![\w$])" + n + r"(?![\w$])", re.M)


def _word(name):
    return re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")


def references(root, files, part):
    """Where else is this part named? Used to tell leftovers from code that is still wired in.

    Exact word edges only. A name of 3 characters or less counts only on an
    exact code use (call, member access, import/require/export).
    """
    pats = []
    paths = set()
    if part["level"] == "file":
        base = os.path.basename(part["file"])
        stem = os.path.splitext(base)[0]
        pats.append(_word(base))
        if stem == "index":
            pats = [_word(part["file"].rsplit("/", 1)[0] + "/index") if "/" in part["file"] else _word("./index"),
                    _word(base)]
        elif len(stem) > SHORT:
            pats.append(_word(stem))
        else:
            # short stem: only a require/import path ending in it, e.g. './q' or "../lib/q.js"
            pats.append(re.compile(r"['\"][^'\"\n]*/" + re.escape(stem) + r"(?:\.[\w]+)?['\"]"))
        segs = os.path.splitext(part["file"])[0].split("/")
        if len(segs) >= 2:
            paths.add("/".join(segs[-2:]))
    else:
        nm = part["name"]
        pats.append(_word(nm) if len(nm) > SHORT else _short_use(nm))
    hits = []
    for rel in files:
        if part["level"] == "file" and rel == part["file"]:
            continue
        if os.path.splitext(rel)[1].lower() not in TEXT_EXT:
            continue
        try:
            text = open(os.path.join(root, rel), encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        if part["level"] == "block" and rel == part["file"]:
            lines = text.split("\n")
            text = "\n".join(lines[:part["first"]] + lines[part["last"] + 1:])
        if any(_word(pth).search(text) for pth in paths):
            hits.append(rel)
            continue
        if any(pt.search(text) for pt in pats):
            hits.append(rel)
    return hits


def use_sites(root, files, part, refs):
    """Each place a reference file names this part: (file, 0-based line, char offset).

    Same patterns as references(). Matches inside the cut block itself are skipped.
    """
    if part["level"] == "file":
        base = os.path.basename(part["file"])
        stem = os.path.splitext(base)[0]
        pats = [_word(base)]
        if stem == "index":
            pats = [_word(part["file"].rsplit("/", 1)[0] + "/index") if "/" in part["file"] else _word("./index")]
        elif len(stem) > SHORT:
            pats.append(_word(stem))
        else:
            pats.append(re.compile(r"['\"][^'\"\n]*/" + re.escape(stem) + r"(?:\.[\w]+)?['\"]"))
        segs = os.path.splitext(part["file"])[0].split("/")
        if len(segs) >= 2:
            pats.append(_word("/".join(segs[-2:])))
    else:
        nm = part["name"]
        pats = [_word(nm) if len(nm) > SHORT else _short_use(nm)]
    out = []
    for rel in sorted(set(refs) | ({part["file"]} if part["level"] == "block" else set())):
        try:
            text = open(os.path.join(root, rel), encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                starts.append(i + 1)
        seen = set()
        for pt in pats:
            for m in pt.finditer(text):
                line = bisect.bisect_right(starts, m.start()) - 1
                if part["level"] == "block" and rel == part["file"] and part["first"] <= line <= part["last"]:
                    continue
                if (rel, line) in seen:
                    continue
                seen.add((rel, line))
                out.append((rel, line, m.start()))
    return out


def path_hit(rel, text):
    """Is this exact project path named in tool output (stack traces, lint lines)?

    Word edges on both sides, so a root 'index.js' is not hit by '/work/lib/index.js'.
    """
    pat = (r"(?:(?<=/work/)|(?<=\./)|(?<![^\s'\"(\[`=:,]))" + re.escape(rel) + r"(?![\w/-])(?!\.\w)")
    return re.search(pat, text) is not None
