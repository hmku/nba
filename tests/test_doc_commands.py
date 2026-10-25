"""Documentation drift check: every `fantasy ...` command and `--flag` in the agent context must exist.

Scans the agent-facing docs (CLAUDE.md, skills, subagents, runbooks), extracts `fantasy` invocations
from inline code spans and fenced code blocks, and resolves them against the real Typer/click command
tree (`typer.main.get_command(app)`). Deterministic, offline: nothing is executed.

Doc shorthand that is understood (not errors):
- alternatives inside a word: `fantasy draft init|board|pick`, `--approve|--reject`, `sim season\\|matchup`
  (markdown-table escaped pipe);
- placeholders: `<id>`, `$D`, `...`, `..` -- a placeholder where a subcommand is expected stops resolution,
  and flags after it are checked against every command below that point;
- optional brackets `[--rest]`, `[--week]` (the flag is still checked);
- quoted strings (`"..."`, `'{"json": ...}'`) are opaque; `# comments` are ignored;
- several commands per snippet, split on `$(`, `)`, `&&`, `||`, `;` and a shell pipe ` | `;
- a snippet that ends at a group (`fantasy intel --rest`) checks its flags against the whole group.

When this fails: fix the doc (see `fantasy <cmd> --help`), or, if the command is intentionally not
there yet (e.g. documented as PLANNED), add an entry to ALLOWLIST below with a reason.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import typer

from fantasy.cli import app

ROOT = Path(__file__).resolve().parents[1]
DOC_GLOBS = ["CLAUDE.md", ".claude/skills/*/SKILL.md", ".claude/agents/*.md", "docs/runbooks/*.md"]

# Intentional exceptions: (doc path relative to repo root, problem key, reason). The problem key is the
# `key` of a Problem: "cmd:<fantasy path that does not resolve>" or "flag:<fantasy path> <flag>".
# Keep this small; every entry must still match a real problem (test_allowlist_entries_are_used).
ALLOWLIST: list[tuple[str, str, str]] = [
    (
        ".claude/skills/in-season-ops/SKILL.md",
        "cmd:fantasy intel export-league",
        "documented as PLANNED (Muse snapshot -> rosters converter); `sim export-league` is the built one",
    ),
    (
        ".claude/skills/league-loop/SKILL.md",
        "cmd:fantasy intel export-league",
        "documented as PLANNED (Muse snapshot -> rosters converter); `sim export-league` is the built one",
    ),
]

_PLACEHOLDER_RE = re.compile(r"^(\.\.\.?|…|\$\w+|\$\{\w+\}|<>|[A-Z]+|N)$")
_FLAG_RE = re.compile(r"^--?[A-Za-z][\w-]*$")


# --------------------------------------------------------------------------- real command tree


@dataclass(frozen=True)
class Node:
    path: str  # "fantasy sim season"
    options: frozenset[str]
    children: dict[str, Node]

    @property
    def is_group(self) -> bool:
        return bool(self.children)

    def all_options(self) -> frozenset[str]:
        """Own options plus those of every descendant (for `group ... --flag` shorthand)."""
        out = set(self.options)
        for child in self.children.values():
            out |= child.all_options()
        return frozenset(out)


def _build(cmd: Any, path: str) -> Node:
    opts: set[str] = {"--help"}
    for param in cmd.params:
        for o in [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]:
            if o.startswith("-"):
                opts.add(o)
    children = {
        name: _build(sub, f"{path} {name}") for name, sub in getattr(cmd, "commands", {}).items()
    }
    return Node(path=path, options=frozenset(opts), children=children)


def command_tree() -> Node:
    return _build(typer.main.get_command(app), "fantasy")


# --------------------------------------------------------------------------- doc extraction


@dataclass(frozen=True)
class Snippet:
    file: str
    line: int
    text: str


def _doc_files() -> list[Path]:
    files: list[Path] = []
    for pattern in DOC_GLOBS:
        files.extend(sorted(ROOT.glob(pattern)))
    return files


def extract_snippets(text: str, file: str = "<doc>") -> list[Snippet]:
    """Code snippets: every line of a fenced block (with `\\` continuations joined) + inline code spans."""
    out: list[Snippet] = []
    lines = text.splitlines()
    in_fence = False
    fence = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        m = re.match(r"(`{3,}|~{3,})", stripped)
        if m and (not in_fence or stripped.startswith(fence)):
            in_fence, fence = (not in_fence, m.group(1))
            i += 1
            continue
        if in_fence:
            start = i
            joined = line
            while joined.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                joined = joined.rstrip()[:-1] + " " + lines[i].strip()
            out.append(Snippet(file, start + 1, joined))
        else:
            for span in re.finditer(r"(`+)(.+?)\1", line):
                out.append(Snippet(file, i + 1, span.group(2)))
        i += 1
    return out


def _mask_quotes(s: str) -> str:
    """Replace quoted strings by Q (contents are opaque); an unterminated quote masks to end of line."""
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c in "'\"":
            j = s.find(c, i + 1)
            out.append("Q")
            i = len(s) if j < 0 else j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_commands(text: str) -> list[list[str]]:
    """Token lists (starting after `fantasy`) for every fantasy invocation in a snippet."""
    s = text.replace("\\|", "|")
    s = re.sub(r"<[^<>]*>", "<>", s)  # <placeholder with spaces> -> one token
    s = _mask_quotes(s)
    s = re.split(r"(?:^|\s)#", s, maxsplit=1)[0]  # shell comment
    segments = re.split(r"\$\(|\)|&&|\|\||;|\s\|\s|\s\|$", s)
    cmds: list[list[str]] = []
    for seg in segments:
        tokens = seg.split()
        while tokens and re.match(r"^(export\s+)?[A-Za-z_]\w*=", tokens[0]):  # VAR=value prefix
            tokens.pop(0)
        if tokens and tokens[0] == "export":
            tokens.pop(0)
        if tokens and tokens[0] == "fantasy":
            cmds.append(tokens[1:])
    return cmds


# --------------------------------------------------------------------------- checking


@dataclass(frozen=True)
class Problem:
    file: str
    line: int
    snippet: str
    key: str  # "cmd:fantasy intel export-league" | "flag:fantasy inseason stream --start"
    detail: str

    def render(self) -> str:
        return f"{self.file}:{self.line}: `{self.snippet.strip()}`\n    -> {self.detail}"


def _clean(tok: str) -> str:
    tok = tok.strip("[](),:;{}")
    return tok if re.fullmatch(r"\.+|…", tok) else tok.rstrip(".")


def check_tokens(tokens: list[str], tree: Node) -> list[tuple[str, str]]:
    """(key, detail) for every unresolvable subcommand / unknown flag in one invocation.

    Subcommand tokens are resolved first (alternatives fan out); flags are checked at the end against
    the resolved command(s): a leaf's own options, or -- if resolution stopped at a group or at a
    placeholder -- the options of every command below it (doc shorthand like `fantasy intel --rest`).
    """
    problems: list[tuple[str, str]] = []
    nodes = [tree]
    resolving = True  # still walking subcommands
    flags: list[str] = []
    for raw in tokens:
        tok = _clean(raw)
        if not tok:
            continue
        if tok.startswith("-"):
            for flag in tok.split("|"):
                flag = _clean(flag).split("=", 1)[0]
                if _FLAG_RE.match(flag):
                    flags.append(flag)
            continue
        if not resolving or raw.startswith("["):
            continue  # positional argument of a leaf / optional argument
        if _PLACEHOLDER_RE.match(tok) or "<>" in tok or "$" in tok or tok == "Q":
            resolving = False
            continue
        nxt: list[Node] = []
        for node in nodes:
            for alt in tok.split("|"):
                child = node.children.get(alt)
                if child is None:
                    problems.append(
                        (
                            f"cmd:{node.path} {alt}",
                            f"no command `{alt}` under `{node.path}` "
                            f"(have: {', '.join(sorted(node.children))})",
                        )
                    )
                else:
                    nxt.append(child)
        if not nxt:
            return problems
        nodes = nxt
        if all(not n.is_group for n in nodes):
            resolving = False
    for node in nodes:
        allowed = node.all_options() if node.is_group else node.options
        for flag in dict.fromkeys(flags):
            if flag not in allowed:
                problems.append(
                    (
                        f"flag:{node.path} {flag}",
                        f"`{flag}` is not an option of `{node.path}` "
                        f"(options: {', '.join(sorted(allowed - {'--help'})) or 'none'})",
                    )
                )
    return problems


def doc_problems(path: Path, tree: Node) -> list[Problem]:
    rel = path.relative_to(ROOT).as_posix()
    out: list[Problem] = []
    for snip in extract_snippets(path.read_text(encoding="utf-8"), rel):
        for tokens in split_commands(snip.text):
            for key, detail in check_tokens(tokens, tree):
                out.append(Problem(rel, snip.line, snip.text, key, detail))
    return out


def _allowed(p: Problem) -> bool:
    return any(p.file == f and p.key == k for f, k, _ in ALLOWLIST)


def iter_invocations() -> Iterator[tuple[Path, list[str]]]:
    for path in _doc_files():
        for snip in extract_snippets(path.read_text(encoding="utf-8")):
            for tokens in split_commands(snip.text):
                yield path, tokens


# --------------------------------------------------------------------------- tests


@pytest.fixture(scope="module")
def tree() -> Node:
    return command_tree()


@pytest.mark.parametrize("doc", _doc_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_doc_commands_exist(doc: Path, tree: Node) -> None:
    problems = [p for p in doc_problems(doc, tree) if not _allowed(p)]
    assert not problems, (
        f"{len(problems)} documented `fantasy` command(s)/flag(s) do not exist in the CLI "
        "(fix the doc using `fantasy <cmd> --help`, or allowlist in tests/test_doc_commands.py with a reason):\n"
        + "\n".join(p.render() for p in problems)
    )


def test_scanner_finds_the_docs() -> None:
    """Guard against the scan silently matching nothing (e.g. a glob or regex change)."""
    files = _doc_files()
    assert len(files) >= 10
    invocations = list(iter_invocations())
    assert len(invocations) > 200
    assert any(t[:2] == ["run", "start"] for _, t in invocations)


def test_allowlist_entries_are_used(tree: Node) -> None:
    keys = {(p.file, p.key) for path in _doc_files() for p in doc_problems(path, tree)}
    stale = [(f, k) for f, k, _ in ALLOWLIST if (f, k) not in keys]
    assert not stale, f"ALLOWLIST entries no longer needed (remove them): {stale}"


# --------------------------------------------------------------------------- parser unit tests


def test_split_commands_handles_shell_shapes() -> None:
    assert split_commands(
        'export FANTASY_RUN_ID=$(fantasy run start --agent <name> --task "a b" --mode live)'
    ) == [["run", "start", "--agent", "<>", "--task", "Q", "--mode", "live"]]
    assert split_commands("fantasy txn list --json ; fantasy status --json") == [
        ["txn", "list", "--json"],
        ["status", "--json"],
    ]
    assert split_commands('fantasy draft recommend --json | python3 -c "x | y"') == [
        ["draft", "recommend", "--json"]
    ]
    assert split_commands("fantasy sim season\\|matchup --json   # fantasy bogus --x") == [
        ["sim", "season|matchup", "--json"]
    ]
    assert split_commands("pip install fantasy") == []


def test_check_tokens_shorthand(tree: Node) -> None:
    ok = [
        "draft init|board|pick",
        "intel latest|list|show [--rest]",
        "approval decide <> --approve|--reject --by harry",
        "run|lock|approval|txn|task ... --rest",
        "sim ... --projections x.csv --no-save",
        "intel --rest",
        "task update T-1 --status blocked|todo [--release]",
    ]
    for s in ok:
        assert check_tokens(s.split(), tree) == [], s
    bad = check_tokens("intel export-league".split(), tree)
    assert [k for k, _ in bad] == ["cmd:fantasy intel export-league"]
    bad = check_tokens("draft board --nope".split(), tree)
    assert [k for k, _ in bad] == ["flag:fantasy draft board --nope"]
    bad = check_tokens("sim season|matchup --nope".split(), tree)
    assert [k for k, _ in bad] == [
        "flag:fantasy sim season --nope",
        "flag:fantasy sim matchup --nope",
    ]
    bad = check_tokens("intel --nope".split(), tree)
    assert [k for k, _ in bad] == ["flag:fantasy intel --nope"]


def test_extract_snippets_fences_and_spans() -> None:
    doc = "Run `fantasy status` then\n```bash\nfantasy run start --agent a \\\n  --mode live\n```\n`x`"
    snips = extract_snippets(doc)
    assert [(s.line, s.text) for s in snips] == [
        (1, "fantasy status"),
        (3, "fantasy run start --agent a  --mode live"),
        (6, "x"),
    ]
