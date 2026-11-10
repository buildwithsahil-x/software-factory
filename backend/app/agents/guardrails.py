"""Guardrails checked before every agent tool call (PreToolUse hook).

This is the *second* layer of protection. The real enforcement is identity
level: an Azure Reader-only identity, a GitHub token that cannot bypass branch
protection, and read-only access to the Terraform modules. These checks stop
attempts early, explain the rule to the agent, and log them to the dashboard.

Rules:
- No Azure writes: `az` is limited to read-only verbs.
- No `terraform apply/plan/destroy/import/state/...`.
- No `kubectl`/`helm` changes to clusters.
- No pushes to protected branches (dev, main, infra) unless the agent is
  explicitly allowed (the Infra agent may push to `infra`), no force pushes,
  no branch deletion, no merges into protected branches, no `gh pr merge`.
- File tools may only touch paths inside the agent's workspace.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

PROTECTED_BRANCHES = frozenset({"main", "master", "dev", "infra"})

_SHELL_OPERATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", "\n", ";;", "!"}
_WRAPPERS = {"sudo", "env", "time", "nohup", "nice", "command", "exec", "xargs", "timeout", "stdbuf"}
_SHELLS = {"bash", "sh", "zsh", "dash"}

_TERRAFORM_BLOCKED = {
    "apply", "plan", "destroy", "import", "state", "taint", "untaint",
    "force-unlock", "refresh", "workspace", "login",
}
_AZ_READ_VERBS = {"show", "list", "version", "login", "logout", "wait", "find", "help"}
_AZ_READ_PREFIXES = ("list-", "show-", "get-", "check-")
_KUBECTL_READ = {
    "get", "describe", "logs", "top", "version", "explain", "api-resources",
    "api-versions", "cluster-info", "auth", "config", "wait", "diff",
}
_HELM_READ = {"list", "ls", "status", "get", "history", "show", "template", "lint", "version", "search", "repo", "env"}
_GH_BLOCKED = {
    ("pr", "merge"), ("repo", "delete"), ("repo", "edit"), ("repo", "rename"),
    ("repo", "archive"), ("secret", None), ("variable", None), ("ruleset", None),
    ("release", "delete"), ("workflow", "disable"),
}

_GIT_HISTORY_CHANGING = {"merge", "pull", "rebase", "cherry-pick", "revert", "am", "commit", "reset"}

_PATH_TOOLS_WRITE ={"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_PATH_TOOLS_READ = {"Read": "file_path", "Glob": "path", "Grep": "path"}


@dataclass
class GuardContext:
    """What one agent run is allowed to touch."""

    cwd: Path
    readable_roots: list[Path] = field(default_factory=list)
    protected_branches: frozenset[str] = PROTECTED_BRANCHES
    allowed_push_branches: frozenset[str] = frozenset()
    current_branch: Callable[[], str | None] = lambda: None

    def __post_init__(self) -> None:
        self.cwd = Path(self.cwd).resolve()
        self.readable_roots = [Path(p).resolve() for p in self.readable_roots]


def check_tool_call(tool_name: str, tool_input: dict, ctx: GuardContext) -> str | None:
    """Return a reason string if the call must be blocked, else None."""
    if tool_name == "Bash":
        return check_shell_command(str(tool_input.get("command", "")), ctx)
    if tool_name in _PATH_TOOLS_WRITE:
        return _check_path(tool_input.get(_PATH_TOOLS_WRITE[tool_name]), ctx, write=True)
    if tool_name in _PATH_TOOLS_READ:
        return _check_path(tool_input.get(_PATH_TOOLS_READ[tool_name]), ctx, write=False)
    return None


# --------------------------------------------------------------------------- paths


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _check_path(raw: object, ctx: GuardContext, *, write: bool) -> str | None:
    if raw is None or raw == "":
        return None  # tool defaults to the working directory
    path = Path(os.path.expanduser(str(raw)))
    if not path.is_absolute():
        path = ctx.cwd / path
    path = path.resolve()
    roots = [ctx.cwd] if write else [ctx.cwd, *ctx.readable_roots]
    if any(_within(path, root) for root in roots):
        return None
    action = "write to" if write else "read"
    return f"Blocked: agents may only {action} files inside their workspace ({ctx.cwd}); got {path}."


# --------------------------------------------------------------------------- shell


def check_shell_command(command: str, ctx: GuardContext, _depth: int = 0) -> str | None:
    if _depth > 3:
        return "Blocked: command nesting is too deep to check safely."

    # Check command substitutions $(...) and `...` on their own as well.
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command):
        sub = inner[0] or inner[1]
        if reason := check_shell_command(sub, ctx, _depth + 1):
            return reason

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()!\n")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return "Blocked: the command could not be parsed safely (unbalanced quotes)."

    for segment in _split_segments(tokens):
        if reason := _check_segment(segment, ctx, _depth):
            return reason
    return None


def _split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in _SHELL_OPERATORS or set(tok) <= set(";&|()!\n"):
            segments.append([])
        else:
            segments[-1].append(tok)
    return [s for s in segments if s]


def _strip_prefixes(seg: list[str]) -> list[str]:
    """Drop env assignments and wrapper commands like `sudo` or `env`."""
    i = 0
    while i < len(seg):
        tok = seg[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
        elif os.path.basename(tok) in _WRAPPERS:
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                i += 1
            # `timeout 30 cmd`: skip a duration argument
            if i < len(seg) and re.match(r"^\d+[smhd]?$", seg[i]):
                i += 1
        else:
            break
    return seg[i:]


def _positional(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


# Global flags of kubectl/helm that take a separate value (`-n app`).
_CLUSTER_VALUE_FLAGS = {
    "-n", "--namespace", "--context", "--kubeconfig", "--kube-context", "--cluster",
    "--user", "-s", "--server", "--token", "--as", "--as-group", "--request-timeout",
}


def _cluster_positional(args: list[str]) -> list[str]:
    pos, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in _CLUSTER_VALUE_FLAGS:
            skip = True
        elif not a.startswith("-"):
            pos.append(a)
    return pos


def _check_segment(seg: list[str], ctx: GuardContext, depth: int) -> str | None:
    seg = _strip_prefixes(seg)
    if not seg:
        return None
    prog = os.path.basename(seg[0])
    args = seg[1:]

    if prog in _SHELLS and "-c" in args:
        idx = args.index("-c")
        if idx + 1 < len(args):
            return check_shell_command(args[idx + 1], ctx, depth + 1)
        return None
    if prog == "terraform" or prog == "tofu":
        return _check_terraform(args)
    if prog == "az":
        return _check_az(args)
    if prog == "kubectl":
        return _check_kubectl(args)
    if prog == "helm":
        return _check_helm(args)
    if prog == "git":
        return _check_git(args, ctx)
    if prog == "gh":
        return _check_gh(args)
    return None


def _check_terraform(args: list[str]) -> str | None:
    sub = next((a for a in args if not a.startswith("-")), None)
    if sub in _TERRAFORM_BLOCKED:
        return (
            f"Blocked: `terraform {sub}` is not allowed. Agents only write tfvars and reports; "
            "a human runs Terraform."
        )
    return None


def _check_az(args: list[str]) -> str | None:
    words = []
    for a in args:
        if a.startswith("-"):
            break
        words.append(a)
    if not words:
        return None
    verb = words[-1]
    if verb in _AZ_READ_VERBS or verb.startswith(_AZ_READ_PREFIXES):
        return None
    if words[0] == "account" and verb in {"set", "get-access-token"}:
        return None
    return (
        f"Blocked: `az {' '.join(words)}` may change Azure. Agents have read-only Azure access; "
        "only show/list/get-style commands are allowed."
    )


def _check_kubectl(args: list[str]) -> str | None:
    pos = _cluster_positional(args)
    sub = next(iter(pos), None)
    if sub is None or sub in _KUBECTL_READ:
        if sub == "config" and len(pos) > 1 and pos[1] not in {
            "view", "get-contexts", "current-context", "get-clusters",
        }:
            return "Blocked: changing kubectl config is not allowed."
        return None
    return f"Blocked: `kubectl {sub}` can change the cluster. Deployments happen only through the dev pipeline."


def _check_helm(args: list[str]) -> str | None:
    sub = next(iter(_cluster_positional(args)), None)
    if sub is None or sub in _HELM_READ:
        return None
    return f"Blocked: `helm {sub}` can change the cluster. Deployments happen only through the dev pipeline."


def _check_gh(args: list[str]) -> str | None:
    pos = _positional(args)
    if not pos:
        return None
    group, action = pos[0], (pos[1] if len(pos) > 1 else None)
    if (group, action) in _GH_BLOCKED or (group, None) in _GH_BLOCKED:
        return f"Blocked: `gh {group}{' ' + action if action else ''}` is reserved for humans."
    if group == "api":
        method = None
        for i, a in enumerate(args):
            if a in {"-X", "--method"} and i + 1 < len(args):
                method = args[i + 1].upper()
            elif a.startswith("--method="):
                method = a.split("=", 1)[1].upper()
        if method and method != "GET":
            return "Blocked: only read-only (GET) `gh api` calls are allowed."
        if any("merge" in a for a in pos[1:]):
            return "Blocked: merging through the GitHub API is reserved for humans."
    return None


# --------------------------------------------------------------------------- git


def _git_subcommand(args: list[str]) -> tuple[str | None, list[str]]:
    """Skip global options like `-C dir` or `-c key=val`."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            return a, args[i + 1 :]
    return None, []


def _normalize_ref(ref: str) -> str:
    ref = ref.lstrip("+")
    for prefix in ("refs/heads/", "heads/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix) :]
    return ref


def _check_git(args: list[str], ctx: GuardContext) -> str | None:
    sub, rest = _git_subcommand(args)
    if sub == "push":
        return _check_git_push(rest, ctx)
    if sub in _GIT_HISTORY_CHANGING:
        branch = ctx.current_branch()
        if branch in ctx.protected_branches and branch not in ctx.allowed_push_branches:
            return (
                f"Blocked: `git {sub}` while on protected branch `{branch}`. "
                "Work on your feature branch; humans merge into protected branches."
            )
    if sub == "branch":
        if any(a in {"-d", "-D", "--delete", "-m", "-M", "--move"} for a in rest):
            targets = {_normalize_ref(a) for a in _positional(rest)}
            if targets & ctx.protected_branches:
                return "Blocked: deleting or renaming protected branches is not allowed."
    return None


def _check_git_push(rest: list[str], ctx: GuardContext) -> str | None:
    force_flags = {"-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror", "--all", "--prune"}
    for a in rest:
        if a in force_flags or a.startswith("--force-with-lease="):
            return f"Blocked: `git push {a}` is not allowed."
        if a in {"-d", "--delete"}:
            return "Blocked: deleting remote branches is not allowed."

    pos = []
    skip_next = False
    for a in rest:
        if skip_next:
            skip_next = False
            continue
        if a in {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}:
            skip_next = True
            continue
        if a.startswith("-"):
            continue
        pos.append(a)

    refspecs = pos[1:]  # pos[0] is the remote
    targets: list[str] = []
    if not refspecs:
        branch = ctx.current_branch()
        targets.append(branch or "HEAD")
    for spec in refspecs:
        if spec.startswith("+"):
            return "Blocked: force-pushing (`+refspec`) is not allowed."
        if spec.startswith(":"):
            return "Blocked: deleting remote branches is not allowed."
        dst = spec.split(":", 1)[1] if ":" in spec else spec
        if dst == "HEAD":
            dst = ctx.current_branch() or "HEAD"
        targets.append(_normalize_ref(dst))

    for target in targets:
        if target == "HEAD":
            return "Blocked: could not determine which branch is being pushed; push an explicit feature branch."
        if target in ctx.protected_branches and target not in ctx.allowed_push_branches:
            return (
                f"Blocked: pushing to protected branch `{target}`. Push your feature branch "
                "and open a PR; a human merges it."
            )
    return None
