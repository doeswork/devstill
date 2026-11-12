"""Keep minimal headless startup independent of Textual and legacy orchestration."""

import sys


def main():
    args = sys.argv[1:]
    minimal = bool(args and args[0] in {"edit", "runs", "session"})
    if args and args[0] == "task":
        minimal = "--engine" in args and args[args.index("--engine") + 1 :][:1] == [
            "minimal"
        ]
        if "--engine" not in args:
            from devstill.config import find_config, load_config

            try:
                minimal = load_config(find_config()).editing.enabled
            except (ValueError, OSError):
                pass
    if minimal:
        from devstill.runtime.cli import main as run
    else:
        from devstill.cli import main as run
    run()


if __name__ == "__main__":
    main()
