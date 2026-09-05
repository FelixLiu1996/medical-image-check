from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np

DOI_RE = re.compile(r"(?i)\b10\.\d{4,9}/[-._;()/:A-Z0-9]+")
PMID_RE = re.compile(
    r"(?ix)(?:\bPMID\s*(?:ID|编号|索引号)?\s*[:：]?\s*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d{6,9})"
)
FIGURE_RE = re.compile(
    r"(?ix)(?:\b(?:figure|fig\.?)\s*|图\s*)(S?\d+)\s*([A-Z])?(?:\s*[-－]\s*(\d+))?"
)

RELATION_TERMS = (
    "重复",
    "重叠",
    "相似",
    "同一张",
    "相同的图",
    "同一块",
    "同一数据",
    "同一只",
    "same image",
    "same data",
    "overlap",
    "duplicate",
    "duplicated",
    "reuse",
    "reused",
    "similar",
    "identical",
)
CORRECTION_TERMS = ("修正后的图", "修正版图", "勘误发布", "更正声明")
VISUALIZATION_TERMS = ("动画视频", "可视化呈现", "selective erase", "opacity")
REBUTTAL_TERMS = (
    "并非完全相同",
    "不同实验的两组独立",
    "可排除",
    "存在显著差异",
    "实际采用了不同",
)
GENERIC_IMAGE_LABELS = ("📷 相关图片", "相关图片：", "相关图片:")

COLOR_RANGES = (
    ("red", ((0, 12), (168, 179))),
    ("orange", ((13, 24),)),
    ("yellow", ((25, 37),)),
    ("green", ((38, 84),)),
    ("cyan", ((85, 99),)),
    ("blue", ((100, 133),)),
    ("magenta", ((134, 167),)),
)


@dataclass(frozen=True)
class FigureReference:
    figure: str
    panel: str = ""
    subpanel: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "figure": self.figure,
            "panel": self.panel,
            "subpanel": self.subpanel,
        }


@dataclass(frozen=True)
class ArticleImageContext:
    url: str
    preceding_blocks: tuple[str, ...]


class _ArticleContextParser(HTMLParser):
    _block_tags: ClassVar[frozenset[str]] = frozenset(
        {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._active_blocks: list[tuple[str, list[str]]] = []
        self._completed_blocks: list[str] = []
        self.images: list[ArticleImageContext] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower in self._block_tags:
            self._active_blocks.append((lower, []))
        if lower != "img":
            return
        attributes = {key.lower(): value or "" for key, value in attrs}
        url = attributes.get("data-src") or attributes.get("src")
        if not url:
            return
        self.images.append(
            ArticleImageContext(
                url=canonical_image_url(url),
                preceding_blocks=tuple(self._completed_blocks[-8:]),
            )
        )

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        for index in range(len(self._active_blocks) - 1, -1, -1):
            block_tag, pieces = self._active_blocks[index]
            if block_tag != lower:
                continue
            del self._active_blocks[index]
            text = normalized_text("".join(pieces))
            if text:
                self._completed_blocks.append(text)
            return

    def handle_data(self, data: str) -> None:
        for _, pieces in self._active_blocks:
            pieces.append(data)


def normalized_text(value: str) -> str:
    return " ".join(html.unescape(value).replace("\u00a0", " ").split())


def canonical_image_url(value: str) -> str:
    parsed = urlsplit(html.unescape(value).strip())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def parse_article_image_contexts(document: str) -> list[ArticleImageContext]:
    parser = _ArticleContextParser()
    parser.feed(document)
    parser.close()
    return parser.images


def select_claim_text(blocks: tuple[str, ...]) -> str:
    candidates = [
        block
        for block in blocks
        if block and not any(label in block for label in GENERIC_IMAGE_LABELS)
    ]
    for block in reversed(candidates):
        if statement_mentions_relation(block):
            return block
    return candidates[-1] if candidates else ""


def statement_mentions_relation(value: str) -> bool:
    lower = value.lower()
    return any(term in lower for term in RELATION_TERMS)


def classify_statement(value: str) -> tuple[str, str]:
    lower = value.lower()
    if any(term in lower for term in VISUALIZATION_TERMS):
        return "visualization", "excluded_supporting_visualization"
    if any(term in lower for term in CORRECTION_TERMS) and not statement_mentions_relation(value):
        return "correction", "excluded_correction_image"
    if any(term in lower for term in REBUTTAL_TERMS):
        return "rebuttal", "disputed_relation"
    if statement_mentions_relation(value):
        return "source_claim", "candidate_relation"
    return "other", "not_a_relation_statement"


def extract_dois(value: str) -> list[str]:
    result: list[str] = []
    for match in DOI_RE.finditer(value):
        doi = match.group(0).lower().rstrip(".,;:)]}，。；：")
        if doi not in result:
            result.append(doi)
    return result


def extract_pmids(value: str) -> list[str]:
    result: list[str] = []
    for match in PMID_RE.finditer(value):
        pmid = match.group(1)
        if pmid not in result:
            result.append(pmid)
    return result


def extract_figure_references(value: str) -> list[FigureReference]:
    references: list[FigureReference] = []
    seen: set[tuple[str, str, str]] = set()
    matches = list(FIGURE_RE.finditer(value))
    for match in matches:
        reference = FigureReference(
            figure=match.group(1).upper(),
            panel=(match.group(2) or "").upper(),
            subpanel=match.group(3) or "",
        )
        key = (reference.figure, reference.panel, reference.subpanel)
        if key not in seen:
            references.append(reference)
            seen.add(key)

        tail = value[match.end() : match.end() + 80]
        for shorthand in re.finditer(
            r"(?ix)(?:、|,|，|/|与|和|及|and)\s*(S?\d{1,2})(?!\d)\s*([A-Z])?"
            r"(?:\s*[-－]\s*(\d+))?",
            tail,
        ):
            shorthand_reference = FigureReference(
                figure=shorthand.group(1).upper(),
                panel=(shorthand.group(2) or "").upper(),
                subpanel=shorthand.group(3) or "",
            )
            shorthand_key = (
                shorthand_reference.figure,
                shorthand_reference.panel,
                shorthand_reference.subpanel,
            )
            if shorthand_key not in seen:
                references.append(shorthand_reference)
                seen.add(shorthand_key)
    return references


def annotation_boxes(image: np.ndarray) -> dict[str, list[list[int]]]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    height, width = image.shape[:2]
    result: dict[str, list[list[int]]] = {}
    for name, ranges in COLOR_RANGES:
        hue_mask = np.zeros_like(hue, dtype=bool)
        for lower, upper in ranges:
            hue_mask |= (hue >= lower) & (hue <= upper)
        mask = np.asarray(hue_mask & (saturation >= 85) & (value >= 135), dtype=np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        boxes: list[list[int]] = []
        for component in range(1, count):
            x, y, box_width, box_height, area = (int(item) for item in stats[component])
            if area < 45 or box_width < 18 or box_height < 16:
                continue
            if box_width > 0.7 * width or box_height > 0.7 * height:
                continue
            region = mask[y : y + box_height, x : x + box_width]
            band = max(2, min(5, min(box_width, box_height) // 5))
            side_coverage = (
                float(region[:band, :].mean()),
                float(region[-band:, :].mean()),
                float(region[:, :band].mean()),
                float(region[:, -band:].mean()),
            )
            if sum(item >= 0.18 for item in side_coverage) < 3:
                continue
            if area / max(box_width * box_height, 1) > 0.5:
                continue
            boxes.append([x, y, box_width, box_height])
        if boxes:
            result[name] = sorted(boxes, key=lambda item: (item[1], item[0]))
    return result


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_ocr_records(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict) and item.get("source_image_relative"):
            records[str(item["source_image_relative"])] = item
    return records


def build_source_relation_draft(batch: str | Path) -> dict[str, Any]:
    batch_path = Path(batch).expanduser().resolve()
    truth = read_json(batch_path / "ground-truth-sealed.json")
    acquisition = read_json(batch_path / "answer-acquisition-summary.json")
    mappings = read_json(batch_path / "source-reported-pair-candidates.json")
    ocr_by_relative = load_ocr_records(batch_path / "source-image-ocr.jsonl")

    positive_cases = [
        item
        for item in truth.get("cases", [])
        if isinstance(item, dict) and item.get("expected") == "positive"
    ]
    acquisition_by_case = {
        str(item.get("case_id")): item
        for item in acquisition.get("cases", [])
        if isinstance(item, dict)
    }
    mappings_by_case = {
        str(item.get("case_id")): item
        for item in mappings.get("cases", [])
        if isinstance(item, dict)
    }
    cases: list[dict[str, Any]] = []
    for truth_case in positive_cases:
        case_id = str(truth_case["case_id"])
        acquisition_case = acquisition_by_case.get(case_id, {})
        mapping_case = mappings_by_case.get(case_id, {})
        cases.append(
            _build_case_draft(
                batch_path,
                truth_case,
                acquisition_case,
                mapping_case,
                ocr_by_relative,
            )
        )

    statements = [statement for case in cases for statement in case["statements"]]
    source_pairs = [pair for statement in statements for pair in statement["source_box_pairs"]]
    official_pairs = [pair for case in cases for pair in case["official_pair_candidates"]]
    candidate_statements = [
        statement
        for statement in statements
        if statement["benchmark_disposition"] in {"candidate_relation", "disputed_relation"}
    ]
    return {
        "schema_version": 1,
        "artifact_kind": "source_relation_ground_truth_draft",
        "dataset_id": truth.get("dataset_id") or "",
        "evaluation_role": "validation_regression_after_unseal",
        "algorithm_findings_included": False,
        "ground_truth_frozen": False,
        "promotion_rule": (
            "Only source claims with two official endpoints, valid regions and an explicit "
            "review decision may be promoted into the scored relation manifest."
        ),
        "case_count": len(cases),
        "available_source_case_count": sum(
            case["source_evidence_status"] == "available" for case in cases
        ),
        "statement_count": len(statements),
        "candidate_statement_count": len(candidate_statements),
        "source_box_pair_count": len(source_pairs),
        "official_pair_candidate_count": len(official_pairs),
        "cases_with_source_box_pairs": sum(bool(case["source_box_pair_count"]) for case in cases),
        "cases_with_official_pair_candidates": sum(
            bool(case["official_pair_candidate_count"]) for case in cases
        ),
        "cases": cases,
    }


def freeze_confirmed_source_relations(
    batch: str | Path,
    feedback_path: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Freeze the correctly mapped subset from a mapping-review export.

    A partial doctor response can freeze confirmed mappings without promoting
    wrong, pending, or uncertain mappings. The result is deliberately labelled
    validation/regression evidence rather than an independent test set.
    """
    batch_path = Path(batch).expanduser().resolve()
    feedback_file = Path(feedback_path).expanduser().resolve()
    feedback = read_json(feedback_file)
    draft = build_source_relation_draft(batch_path)
    _validate_mapping_feedback(feedback, draft)

    official_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    task_to_official: dict[str, str] = {}
    for case in draft["cases"]:
        case_id = str(case["case_id"])
        for candidate in case["official_pair_candidates"]:
            official_by_id[str(candidate.get("pair_id") or "")] = (case_id, candidate)
        for statement in case["statements"]:
            for source_pair in statement["source_box_pairs"]:
                official_pair_id = str(source_pair.get("official_pair_id") or "")
                if official_pair_id:
                    task_to_official[str(source_pair["source_pair_id"])] = official_pair_id

    pairs_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exclusions: list[dict[str, str]] = []
    decision_counts: dict[str, int] = defaultdict(int)
    for review in feedback["task_reviews"]:
        task_id = str(review["task_id"])
        decision = str(review["decision"])
        note = str(review.get("note") or "")
        decision_counts[decision] += 1
        if decision != "correct":
            exclusions.append(
                {
                    "task_id": task_id,
                    "decision": decision,
                    "note": note,
                    "reason": f"mapping_review_{decision}",
                }
            )
            continue
        official_pair_id = task_to_official[task_id]
        case_id, source_candidate = official_by_id[official_pair_id]
        if source_candidate.get("candidate_valid_for_review") is False:
            raise ValueError(f"已确认任务引用了不可复核映射：{task_id}")
        promoted = deepcopy(source_candidate)
        promoted.update(
            {
                "pair_id": task_id,
                "source_candidate_pair_id": official_pair_id,
                "expected": "positive",
                "mapping_status": "doctor_confirmed",
                "review_status": "confirmed",
                "ground_truth_eligible": True,
                "label_source": "public_source_relation_plus_doctor_mapping_review",
                "review_note": note,
            }
        )
        for endpoint in promoted.get("endpoints", []):
            if isinstance(endpoint, dict):
                endpoint["pre_review_source_label_conflict"] = (
                    endpoint.get("source_label_conflict") is True
                )
                endpoint["source_label_conflict"] = False
                endpoint["ground_truth_eligible"] = True
        pairs_by_case[case_id].append(promoted)

    manifest = {
        "schema_version": 1,
        "artifact_kind": "confirmed_source_relation_manifest",
        "dataset_id": draft["dataset_id"],
        "evaluation_role": "post_unseal_validation_regression",
        "ground_truth_frozen": False,
        "confirmed_subset_frozen": True,
        "created_at": datetime.now(UTC).isoformat(),
        "source_candidate_manifest_sha256": sha256_file(
            batch_path / "source-reported-pair-candidates.json"
        ),
        "mapping_feedback_sha256": sha256_file(feedback_file),
        "mapping_feedback_exported_at": feedback.get("exported_at") or "",
        "review_scope": feedback["review_scope"],
        "review_task_count": len(feedback["task_reviews"]),
        "decision_counts": dict(sorted(decision_counts.items())),
        "confirmed_relation_count": sum(len(items) for items in pairs_by_case.values()),
        "confirmed_case_count": len(pairs_by_case),
        "excluded_relation_count": len(exclusions),
        "exclusions": exclusions,
        "metric_guardrail": (
            "This confirmed positive subset may be used for relation recall only. "
            "Precision and F1 remain unavailable until negative algorithm candidates "
            "receive an explicit review protocol."
        ),
        "cases": [
            {"case_id": case_id, "pairs": pairs_by_case[case_id]}
            for case_id in sorted(pairs_by_case)
        ],
    }
    if output_path is not None:
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return manifest


def _validate_mapping_feedback(feedback: dict[str, Any], draft: dict[str, Any]) -> None:
    if feedback.get("schema_version") != 2:
        raise ValueError("映射反馈 schema_version 必须为 2。")
    if feedback.get("artifact_kind") != "source_relation_doctor_feedback":
        raise ValueError("映射反馈 artifact_kind 不正确。")
    if feedback.get("dataset_id") != draft.get("dataset_id"):
        raise ValueError("映射反馈 dataset_id 与批次不一致。")
    if feedback.get("review_scope") != "verify_prepared_source_to_official_ab_mappings_only":
        raise ValueError("映射反馈 review_scope 不正确。")

    available_task_ids = {
        str(source_pair["source_pair_id"])
        for case in draft["cases"]
        for statement in case["statements"]
        for source_pair in statement["source_box_pairs"]
        if source_pair.get("official_pair_id")
    }
    declared = feedback.get("task_ids")
    reviews = feedback.get("task_reviews")
    if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
        raise ValueError("映射反馈 task_ids 必须是字符串列表。")
    if len(declared) != len(set(declared)):
        raise ValueError("映射反馈 task_ids 存在重复。")
    if set(declared) != available_task_ids:
        missing = sorted(available_task_ids - set(declared))
        extra = sorted(set(declared) - available_task_ids)
        raise ValueError(f"映射反馈任务不完整：missing={missing}, extra={extra}")
    if not isinstance(reviews, list) or len(reviews) != len(declared):
        raise ValueError("映射反馈 task_reviews 数量与 task_ids 不一致。")

    review_ids: list[str] = []
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("映射反馈 task_reviews 每项必须是对象。")
        task_id = str(review.get("task_id") or "")
        decision = str(review.get("decision") or "")
        if decision not in {"correct", "wrong", "uncertain", "pending"}:
            raise ValueError(f"映射反馈决定无效：{task_id}={decision}")
        review_ids.append(task_id)
    if len(review_ids) != len(set(review_ids)) or set(review_ids) != set(declared):
        raise ValueError("映射反馈 task_reviews 的任务 ID 缺失或重复。")


def _build_case_draft(
    batch: Path,
    truth_case: dict[str, Any],
    acquisition_case: dict[str, Any],
    mapping_case: dict[str, Any],
    ocr_by_relative: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    case_id = str(truth_case["case_id"])
    status = str(acquisition_case.get("status") or "missing")
    if status != "complete":
        return {
            "case_id": case_id,
            "source_order": truth_case.get("source_order"),
            "wechat_url": truth_case.get("wechat_url") or "",
            "wechat_title": acquisition_case.get("title") or "",
            "paper_keys": truth_case.get("paper_keys") or [],
            "source_evidence_status": "unavailable",
            "source_evidence_reason": status,
            "statements": [],
            "official_pair_candidates": [],
            "source_box_pair_count": 0,
            "official_pair_candidate_count": 0,
        }

    case_dir = batch / case_id
    article = read_json(case_dir / "article.json")
    context_by_url = _context_by_url(case_dir / "article-body.html")
    mapped_pairs = [item for item in mapping_case.get("pairs", []) if isinstance(item, dict)]
    mapped_by_source: dict[str, list[dict[str, Any]]] = {}
    for pair in mapped_pairs:
        mapped_by_source.setdefault(str(pair.get("source_image_relative") or ""), []).append(pair)

    statements: list[dict[str, Any]] = []
    for download in article.get("image_downloads", []):
        if not isinstance(download, dict) or download.get("status") != "downloaded":
            continue
        filename = str(download.get("filename") or "")
        relative = f"{case_id}/wechat-images/{filename}"
        path = batch / relative
        context = context_by_url.get(canonical_image_url(str(download.get("url") or "")))
        blocks = context.preceding_blocks if context else ()
        claim_text = select_claim_text(blocks)
        ocr = ocr_by_relative.get(relative, {})
        ocr_text = normalized_text(
            " ".join(
                str(line.get("text") or "")
                for line in ocr.get("lines", [])
                if isinstance(line, dict)
            )
        )
        role, disposition = classify_statement(claim_text)
        boxes_by_color: dict[str, list[list[int]]] = {}
        image = cv2.imread(str(path))
        if image is not None:
            boxes_by_color = annotation_boxes(image)
        box_pairs = [
            {
                "source_pair_id": f"{case_id}-source-{int(download.get('order') or 0):03d}-{color}",
                "annotation_color": color,
                "source_regions": boxes,
                "mapping_status": "source_ab_detected_official_mapping_pending",
                "review_status": "not_reviewed",
            }
            for color, boxes in boxes_by_color.items()
            if len(boxes) == 2
        ]
        attached_official = mapped_by_source.get(relative, [])
        if attached_official:
            attached_by_color = {
                str(pair.get("annotation_color") or ""): pair for pair in attached_official
            }
            for pair in box_pairs:
                official = attached_by_color.get(pair["annotation_color"])
                if official:
                    pair["mapping_status"] = "official_ab_machine_mapped_candidate"
                    pair["official_pair_id"] = official.get("pair_id") or ""

        figure_references = [item.as_dict() for item in extract_figure_references(claim_text)]
        statements.append(
            {
                "statement_id": f"{case_id}-statement-{int(download.get('order') or 0):03d}",
                "source_image_relative": relative,
                "source_image_sha256": download.get("sha256") or "",
                "source_image_width": int(ocr.get("width") or 0),
                "source_image_height": int(ocr.get("height") or 0),
                "claim_text": claim_text,
                "context_blocks": list(blocks),
                "ocr_text": ocr_text,
                "statement_role": role,
                "benchmark_disposition": disposition,
                "figure_references": figure_references,
                "dois": extract_dois(claim_text),
                "pmids": extract_pmids(claim_text),
                "annotation_boxes_by_color": boxes_by_color,
                "source_box_pairs": box_pairs,
                "official_pair_candidate_ids": [
                    str(pair.get("pair_id") or "") for pair in attached_official
                ],
                "review_status": "not_reviewed",
            }
        )

    return {
        "case_id": case_id,
        "source_order": truth_case.get("source_order"),
        "wechat_url": truth_case.get("wechat_url") or "",
        "wechat_title": acquisition_case.get("title") or article.get("title") or "",
        "paper_keys": truth_case.get("paper_keys") or [],
        "source_evidence_status": "available",
        "source_evidence_reason": "",
        "statements": statements,
        "official_pair_candidates": mapped_pairs,
        "source_box_pair_count": sum(len(item["source_box_pairs"]) for item in statements),
        "official_pair_candidate_count": len(mapped_pairs),
    }


def _context_by_url(article_body_path: Path) -> dict[str, ArticleImageContext]:
    contexts = parse_article_image_contexts(article_body_path.read_text(encoding="utf-8"))
    result: dict[str, ArticleImageContext] = {}
    for context in contexts:
        result.setdefault(context.url, context)
    return result
