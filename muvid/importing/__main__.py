"""The CLI door onto :func:`muvid.importing.import_production`.

``python -m muvid.importing MANIFEST PROJECTS_DIR [--dry-run]``

Prints the import report as JSON. ``--dry-run`` checks every file and every edit
conversion and writes nothing — run it first. argparse from the standard library: the
verb is the function, this is only a door onto it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """The argument parser (exposed so tests can drive it without a subprocess)."""
    p = argparse.ArgumentParser(
        prog="python -m muvid.importing",
        description=(
            "Import a finished music-video production into a host's projects "
            "directory as a muvid.Project. Idempotent."
        ),
    )
    p.add_argument("manifest", type=Path, help="The production manifest (JSON).")
    p.add_argument("projects_dir", type=Path, help="The host's projects directory.")
    p.add_argument(
        "--dry-run", action="store_true", help="Check everything, write nothing."
    )
    return p


def main(argv=None) -> int:
    from muvid.importing import import_production

    args = build_parser().parse_args(argv)
    report = import_production(args.manifest, args.projects_dir, dry_run=args.dry_run)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
