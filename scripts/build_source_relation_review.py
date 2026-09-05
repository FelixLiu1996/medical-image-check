from __future__ import annotations

import argparse
import json
from pathlib import Path

from medical_image_check.evaluation.source_relation_review import build_review_package


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a local interactive package for source relation ground-truth review."
    )
    parser.add_argument("batch", type=Path, help="Prepared evaluation batch directory")
    parser.add_argument("output", type=Path, help="New review package directory")
    parser.add_argument("--zip", dest="zip_path", type=Path, help="Optional ZIP output path")
    arguments = parser.parse_args()
    result = build_review_package(arguments.batch, arguments.output, arguments.zip_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
