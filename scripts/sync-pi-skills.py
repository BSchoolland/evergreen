#!/usr/bin/env python3
"""Generate pi-discoverable skills from the canonical .claude/skills source.

Evergreen's skills are authored once in .claude/skills/<name>/SKILL.md (Claude
format: prose, no frontmatter, cross-refs written as /<name>-evergreen). Pi needs
YAML frontmatter (name/description/user-invocable) and invokes skills as
/skill:<name>. This script mirrors each skill into .pi/agent/skills/<name>/ with:

  - frontmatter prepended (name = dir, description = first meaningful line)
  - command cross-refs rewritten  /<x>-evergreen  ->  /skill:<x>-evergreen
    (file paths like .claude/skills/<x>-evergreen/ are left untouched)
  - the Claude-only `/code-review` rewritten to an inline self-review instruction

.pi/agent/skills is a generated artifact (gitignored). Run this at startup of
both the watchdog server and the interactive bot so pi always sees fresh skills.
The agent runs with cwd = this repo, so pi discovers these as project-level skills.
"""

import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / ".claude" / "skills"
# pi discovers project-level skills at <cwd>/.pi/skills (CONFIG_DIR_NAME = ".pi").
DST = REPO / ".pi" / "skills"

# /<x>-evergreen as a slash-command (not preceded by a path/word char, so
# ".claude/skills/triage-evergreen/" is NOT rewritten but " /triage-evergreen" is).
CMD_REF = re.compile(r"(?<![\w./-])/([a-z][a-z0-9-]*-evergreen)\b")

# Claude-only /code-review (optionally with an effort arg) -> inline instruction.
CODE_REVIEW = re.compile(r"`?/code-review(?:\s+\w+)?`?")
CODE_REVIEW_INLINE = (
    "review your own diff for correctness and bugs (read every changed line, check "
    "edge cases and the full call chain, run the tests and typecheck)"
)


def description_for(body: str) -> str:
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            # collapse to a single line, trim trailing punctuation noise
            line = re.sub(r"\s+", " ", line)
            return line[:200]
    return "Evergreen skill."


def transform(body: str) -> str:
    body = CODE_REVIEW.sub(CODE_REVIEW_INLINE, body)
    body = CMD_REF.sub(r"/skill:\1", body)
    return body


def sync() -> int:
    if not SRC.is_dir():
        print(f"No source skills dir at {SRC}", file=sys.stderr)
        return 0
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)

    count = 0
    for skill_dir in sorted(SRC.iterdir()):
        src_skill = skill_dir / "SKILL.md"
        if not src_skill.is_file():
            continue
        name = skill_dir.name
        body = src_skill.read_text()
        # strip any pre-existing frontmatter (none today, but be safe)
        if body.startswith("---\n"):
            end = body.find("\n---", 4)
            if end != -1:
                body = body[end + 4:].lstrip("\n")
        desc = description_for(body)
        desc = desc.replace('"', "'")
        out = (
            "---\n"
            f"name: {name}\n"
            f'description: "{desc}"\n'
            "user-invocable: true\n"
            "---\n\n"
            f"{transform(body).rstrip()}\n"
        )
        dest_dir = DST / name
        dest_dir.mkdir(parents=True)
        (dest_dir / "SKILL.md").write_text(out)
        # carry over any sibling resource files the skill ships with
        for extra in skill_dir.iterdir():
            if extra.name == "SKILL.md":
                continue
            if extra.is_file():
                shutil.copy2(extra, dest_dir / extra.name)
            elif extra.is_dir():
                shutil.copytree(extra, dest_dir / extra.name)
        count += 1

    print(f"Synced {count} skills -> {DST}")
    return count


if __name__ == "__main__":
    sync()
