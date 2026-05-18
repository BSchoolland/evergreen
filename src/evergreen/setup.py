from evergreen.db import EVERGREEN_DIR, CONFIG_PATH, is_configured, get_cli


def run_setup() -> str:
    EVERGREEN_DIR.mkdir(parents=True, exist_ok=True)

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
    CONFIG_PATH.write_text(cli + "\n")
    print(f"\nSaved preference: {cli}")
    return cli
