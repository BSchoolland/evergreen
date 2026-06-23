import os
import sys

from evergreen.db import init_db, is_configured, get_cli
from evergreen.setup import run_setup


def launch_cli(cli: str, args: list[str], first_run: bool = False):
    cmd = [cli]

    if cli == "claude":
        cmd += ["--dangerously-skip-permissions", "--name", "evergreen"]
    elif cli == "codex":
        cmd.append("--yolo")

    if first_run:
        cmd.append("/setup-evergreen")
    else:
        cmd.extend(args)

    os.execvp(cmd[0], cmd)


def main():
    init_db()

    first_run = not is_configured()

    if first_run:
        cli = run_setup()
    else:
        cli = get_cli()

    args = sys.argv[1:]
    # `evergreen.py onboard [hint]` drops into an interactive session that explores
    # a new project and writes its data note. Any trailing words are passed to the
    # skill as a starting hint (e.g. a URL or name).
    if args and args[0] == "onboard":
        hint = " ".join(args[1:]).strip()
        args = ["/onboard-project-evergreen" + (f" {hint}" if hint else "")]

    launch_cli(cli, args, first_run=first_run)
