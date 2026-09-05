from __future__ import annotations

import argparse
import json
from pathlib import Path

from medical_image_check.evaluation.source_relations import freeze_confirmed_source_relations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze doctor-confirmed source-to-official positive relations."
    )
    parser.add_argument("batch", type=Path, help="Prepared evaluation batch directory")
    parser.add_argument("feedback", type=Path, help="Mapping review feedback JSON")
    parser.add_argument("output", type=Path, help="Output confirmed relation manifest JSON")
    arguments = parser.parse_args()
    manifest = freeze_confirmed_source_relations(
        arguments.batch,
        arguments.feedback,
        arguments.output,
    )
    print(
        json.dumps(
            {
                "output": str(arguments.output.expanduser().resolve()),
                "confirmed_relation_count": manifest["confirmed_relation_count"],
                "confirmed_case_count": manifest["confirmed_case_count"],
                "decision_counts": manifest["decision_counts"],
                "excluded_relation_count": manifest["excluded_relation_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
