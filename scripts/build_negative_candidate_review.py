from __future__ import annotations

import argparse
import json
from pathlib import Path

from medical_image_check.evaluation.negative_review import build_negative_review_package


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a compact local doctor review package for sampled negative candidates."
    )
    parser.add_argument("batch", type=Path, help="Prepared evaluation batch directory")
    parser.add_argument("output", type=Path, help="New review package directory")
    parser.add_argument("--zip", dest="zip_path", type=Path, help="Optional ZIP output path")
    parser.add_argument("--sample-size", type=int, default=32)
    parser.add_argument("--configuration", default="panel-split-auto")
    parser.add_argument("--seed", default="negative-review-v1")
    parser.add_argument(
        "--findings-summary",
        type=Path,
        help="Optional final regression summary instead of blind-algorithm-findings-summary.json",
    )
    parser.add_argument("--cluster-iou-threshold", type=float, default=0.5)
    arguments = parser.parse_args()
    result = build_negative_review_package(
        arguments.batch,
        arguments.output,
        arguments.zip_path,
        sample_size=arguments.sample_size,
        configuration=arguments.configuration,
        seed=arguments.seed,
        findings_summary=arguments.findings_summary,
        cluster_iou_threshold=arguments.cluster_iou_threshold,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
