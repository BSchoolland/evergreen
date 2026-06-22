from evergreen.db import EVERGREEN_DIR, CONFIG_PATH, is_configured, get_cli


def run_setup() -> str:
    EVERGREEN_DIR.mkdir(parents=True, exist_ok=True)

    print("Welcome to Evergreen.\n")
    print("Which AI coding harness do you want to use?")
    print("  1) claude   (Anthropic Claude Code / Agent SDK)")
    print("  2) pi")
    print("  3) codex")
    print()

    choices = {
        "1": "claude", "claude": "claude",
        "2": "pi", "pi": "pi",
        "3": "codex", "codex": "codex",
    }
    while True:
        choice = input("Choice [1/2/3]: ").strip().lower()
        if choice in choices:
            break
        print("Please enter 1, 2, or 3.")

    cli = choices[choice]
    CONFIG_PATH.write_text(cli + "\n")
    print(f"\nSaved preference: {cli}")
    return cli
