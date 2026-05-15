from pathlib import Path

CONFIG_DIR = Path.home() / ".evergreen"
CONFIG_FILE = CONFIG_DIR / "config"


def is_configured() -> bool:
    return CONFIG_FILE.exists()


def get_cli() -> str:
    return CONFIG_FILE.read_text().strip()


def run_setup() -> str:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    print("Welcome to Evergreen.\n")
    print("Which AI CLI do you want to use?")
    print("  1) claude")
    print("  2) codex")
    print()

    while True:
        choice = input("Choice [1/2]: ").strip()
        if choice in ("1", "2", "claude", "codex"):
            break
        print("Please enter 1 or 2.")

    cli = "codex" if choice in ("2", "codex") else "claude"
    CONFIG_FILE.write_text(cli + "\n")
    print(f"\nSaved preference: {cli}")
    return cli
