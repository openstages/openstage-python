"""Console entry point that reports missing WFTNP dependencies clearly."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from .cli import main as cli_main
    except ImportError as exc:
        print(f"openstage-wftnp: cannot start: {exc}", file=sys.stderr)
        return 1
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
