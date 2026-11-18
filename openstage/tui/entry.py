"""Console entry point that reports missing TUI dependencies clearly."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    try:
        from .app import main as app_main
    except ImportError as exc:
        print(f"openstage-tui: cannot start: {exc}", file=sys.stderr)
        return 1
    return app_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
