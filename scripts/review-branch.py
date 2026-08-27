#!/usr/bin/env python3
"""Review a branch's changes with pi review agents.

Diffs <branch> against the base branch inside <dir> and runs one or more pi
agents that read the diff plus surrounding code and report findings. The base
defaults to the repo's own default branch, read from origin/HEAD. Prints the
combined review to stdout. Read-only: the reviewers may run git and read files,
but never edit the repo.

Each review lens is recorded in Evergreen's `runs` table with its Pi cost/tokens.
When the script runs inside a skill invocation, `EVERGREEN_RUN_ID` links the
review batch back to the parent triage run; PR URL and branch are recorded too.

Usage:
  review-branch.py --dir /path/to/repo --branch my-branch [--base origin/main]
  review-branch.py --dir /path/to/repo --branch my-branch --lens correctness,security
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evergreen.db import current_project_id, epoch, get_connection, init_db
from evergreen.pi_usage import session_metrics

LENSES = {
    "correctness": "Focus on correctness and regressions: logic errors, broken or unhandled edge cases, behavior silently changed for existing callers, error handling, concurrency.",
    "logging": "Focus on observability: are failures, retries, and new code paths logged with enough context to diagnose them in production? Flag silent failures, swallowed errors, missing/misleading log or trace data, and over-noisy logging.",
    "security": "Focus on security: injection, missing authn/authz, unsafe handling of external input, secrets, SSRF, data exposure.",
}

PROMPT = """Review the changes on branch `{branch}` compared to `{base}` in this repo.

Run `git diff {base}...HEAD` to see the changes, then read the affected files and their callers — review with context, not the diff in isolation.

{lens}

Report only real, actionable findings. For each: `file:line` — what's wrong — why it matters — the fix. If there's nothing real, say "No issues found." Be concrete and skeptical; don't invent findings to look thorough."""

REVIEW_SESSION_ROOT = REPO / ".pi" / "sessions" / "reviews"
REVIEW_TIMEOUT_SECONDS = 900


@dataclass
class ReviewResult:
    name: str
    out: str
    run_id: int | None
    timed_out: bool = False
    returncode: int | None = None
    cost: float | None = None
    tokens: int | None = None
    model: str | None = None
    effort: str | None = None


def run(cmd, cwd=None, timeout=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def safe_slug(value: str, max_len: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return (slug or "branch")[:max_len]


def cleanup_old_sessions(root: Path, keep_days: int) -> None:
    if keep_days <= 0 or not root.exists():
        return
    cutoff = time.time() - keep_days * 86400
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
        except OSError:
            pass


def parse_parent_run_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def insert_run(run_type: str, summary: str, parent_run_id: int | None,
               branch: str | None, pr_url: str | None) -> int | None:
    project_id = current_project_id() or 1
    conn = get_connection()
    try:
        row = conn.execute(
            "INSERT INTO runs (type, summary, parent_run_id, branch, pr_url, project_id) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (run_type, summary, parent_run_id, branch, pr_url, project_id),
        ).fetchone()
        conn.commit()
        return row[0]
    finally:
        conn.close()


def update_run(run_id: int | None, *, summary: str, finished: bool = True,
               cost: float | None = None, tokens: int | None = None,
               model: str | None = None, effort: str | None = None) -> None:
    if run_id is None:
        return
    finished_at = epoch() if finished else None
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE runs SET finished_at = ?, summary = ?, cost = ?, tokens = ?, model = ?, effort = ? "
            "WHERE id = ?",
            (finished_at, summary, cost, tokens, model, effort, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def infer_pr_url(dir_: str, branch: str) -> str | None:
    commands = [
        ["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"],
        ["gh", "pr", "view", "--json", "url", "--jq", ".url"],
    ]
    for cmd in commands:
        try:
            r = run(cmd, cwd=dir_, timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def read_session_metrics(session_dir: Path) -> tuple[float | None, int | None, str | None, str | None]:
    try:
        return session_metrics(session_dir)
    except Exception:
        return None, None, None, None


def resolve_base(dir_: str, base: str | None) -> str:
    """Return a base ref that is guaranteed to resolve in dir_, or exit."""
    if base is None:
        head = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=dir_, timeout=20)
        if head.returncode != 0:
            sys.exit(
                f"Could not read origin/HEAD in {dir_} to pick a default base ref "
                f"(git: {head.stderr.strip()}). Run `git remote set-head origin --auto` "
                f"there, or pass --base explicitly."
            )
        base = head.stdout.strip()
    if run(["git", "rev-parse", "--verify", "-q", f"{base}^{{commit}}"], cwd=dir_, timeout=20).returncode != 0:
        sys.exit(f"Base ref {base!r} does not resolve in {dir_}; the review would diff against nothing.")
    return base


def review(dir_: str, branch: str, base: str, name: str, model: str | None,
           thinking: str | None, session_dir: Path, batch_run_id: int | None,
           pr_url: str | None) -> ReviewResult:
    prompt = PROMPT.format(branch=branch, base=base, lens=LENSES[name])
    session_dir.mkdir(parents=True, exist_ok=True)

    run_id = insert_run(
        f"review:{name}",
        f"Reviewing {branch} vs {base} ({name})",
        batch_run_id,
        branch,
        pr_url,
    )

    cmd = ["pi", "-p", "--session-dir", str(session_dir)]
    if model:
        cmd += ["--model", model]
    if thinking:
        cmd += ["--thinking", thinking]
    cmd += ["--tools", "bash,read", prompt]

    timed_out = False
    returncode = None
    try:
        r = run(cmd, cwd=dir_, timeout=REVIEW_TIMEOUT_SECONDS)
        returncode = r.returncode
        out = (r.stdout or "").strip() or (r.stderr or "").strip() or "(no output)"
        summary = out if returncode == 0 else f"exit={returncode}\n{out}"
    except subprocess.TimeoutExpired as e:
        timed_out = True
        out = f"(review timed out after {REVIEW_TIMEOUT_SECONDS}s)"
        if e.stdout:
            out += "\n\nPartial stdout:\n" + str(e.stdout).strip()
        if e.stderr:
            out += "\n\nPartial stderr:\n" + str(e.stderr).strip()
        summary = f"TIMEOUT after {REVIEW_TIMEOUT_SECONDS}s\n{out}"

    cost, tokens, used_model, effort = read_session_metrics(session_dir)
    update_run(run_id, summary=summary, cost=cost, tokens=tokens,
               model=used_model, effort=effort)
    return ReviewResult(name, out, run_id, timed_out, returncode, cost, tokens, used_model, effort)


def summarize_batch(branch: str, base: str, pr_url: str | None, results: list[ReviewResult]) -> str:
    timeouts = [r.name for r in results if r.timed_out]
    failures = [r.name for r in results if r.returncode not in (0, None)]
    cost = sum(r.cost or 0 for r in results)
    tokens = sum(r.tokens or 0 for r in results)
    bits = [f"Reviewed {branch} vs {base}: {len(results)} lens(es)"]
    if pr_url:
        bits.append(pr_url)
    if cost or tokens:
        bits.append(f"${cost:.4f}, {tokens:,} tokens")
    if timeouts:
        return "TIMEOUT: " + "; ".join(bits) + f"; timed out: {', '.join(timeouts)}"
    if failures:
        return "; ".join(bits) + f"; nonzero exits: {', '.join(failures)}"
    return "; ".join(bits)


def main():
    p = argparse.ArgumentParser(description="Review a branch's changes with pi agents")
    p.add_argument("--dir", required=True, help="Path to the repo to review")
    p.add_argument("--branch", required=True, help="Branch to review")
    p.add_argument("--base", default=None,
                   help="Base ref to diff against (default: the repo's default branch via origin/HEAD)")
    p.add_argument("--lens", default="correctness,logging,security",
                   help="Comma-separated lenses: " + ", ".join(LENSES))
    p.add_argument("--model", help="pi model override (default: pi's configured model)")
    p.add_argument("--thinking", default="high", help="pi thinking level (default: high)")
    p.add_argument("--parent-run-id", type=int, default=parse_parent_run_id(os.environ.get("EVERGREEN_RUN_ID")),
                   help="Evergreen parent run id (defaults to EVERGREEN_RUN_ID)")
    p.add_argument("--pr-url", help="PR URL override (otherwise inferred with gh)")
    p.add_argument("--keep-session-days", type=int, default=30,
                   help="Delete review session dirs older than this many days (default: 30; <=0 disables)")
    args = p.parse_args()

    lenses = [l.strip() for l in args.lens.split(",") if l.strip()]
    bad = [l for l in lenses if l not in LENSES]
    if bad:
        sys.exit(f"Unknown lens(es): {', '.join(bad)}. Available: {', '.join(LENSES)}")

    conn = init_db()
    conn.close()
    cleanup_old_sessions(REVIEW_SESSION_ROOT, args.keep_session_days)

    # Make sure the base is current and the branch is checked out before reviewing.
    remote = args.base.split("/", 1)[0] if args.base and "/" in args.base else "origin"
    fetched = run(["git", "fetch", remote], cwd=args.dir, timeout=120)
    if fetched.returncode != 0:
        sys.exit(f"git fetch {remote} failed in {args.dir}; the base ref would be stale:\n{fetched.stderr.strip()}")
    args.base = resolve_base(args.dir, args.base)
    co = run(["git", "checkout", args.branch], cwd=args.dir, timeout=60)
    if co.returncode != 0:
        sys.exit(f"Could not check out {args.branch} in {args.dir}:\n{co.stderr.strip()}")

    pr_url = args.pr_url or infer_pr_url(args.dir, args.branch)
    batch_dir = REVIEW_SESSION_ROOT / f"run-{args.parent_run_id or 'standalone'}-{safe_slug(args.branch)}-{epoch()}"
    batch_run_id = insert_run(
        "review",
        f"Reviewing {args.branch} vs {args.base}",
        args.parent_run_id,
        args.branch,
        pr_url,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(lenses)) as ex:
        futures = [
            ex.submit(review, args.dir, args.branch, args.base, name, args.model,
                      args.thinking, batch_dir / name, batch_run_id, pr_url)
            for name in lenses
        ]
        results = [f.result() for f in futures]

    update_run(batch_run_id, summary=summarize_batch(args.branch, args.base, pr_url, results),
               cost=None, tokens=None, model=None, effort=None)

    for result in results:
        usage = ""
        if result.cost is not None or result.tokens is not None:
            usage = f" (${result.cost or 0:.4f}, {result.tokens or 0:,} tokens)"
        print(f"\n===== {result.name} review{usage} =====\n{result.out}")


if __name__ == "__main__":
    main()
