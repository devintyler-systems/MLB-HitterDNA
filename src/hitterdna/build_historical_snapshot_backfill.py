"""CLI for building a historical snapshot manifest from explicit local paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hitterdna.historical_snapshot_backfill import (
    build_historical_snapshot_backfill_manifest,
    validate_historical_snapshot_backfill_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local historical snapshot backfill manifest")
    parser.add_argument("--index", required=True, help="explicit local JSON index")
    parser.add_argument("--snapshot-root", required=True, help="explicit local snapshot root")
    parser.add_argument("--output", required=True, help="explicit output JSON path")
    args = parser.parse_args(argv)
    manifest = build_historical_snapshot_backfill_manifest(args.index, args.snapshot_root)
    validate_historical_snapshot_backfill_manifest(manifest)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

