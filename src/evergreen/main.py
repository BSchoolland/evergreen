import os
import sys

from evergreen.db import init_db
from evergreen.setup import is_configured, get_cli, run_setup


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

    extra_args = sys.argv[1:]
    launch_cli(cli, extra_args, first_run=first_run)
