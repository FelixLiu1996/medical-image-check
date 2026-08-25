from pathlib import Path

from medical_image_check.engines.image_exact import ExactImageDuplicateDetector, sha256_file


def test_exact_image_duplicate_detector_groups_matching_files(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.jpg"
    different = tmp_path / "different.png"
    first.write_bytes(b"same-image-bytes")
    second.write_bytes(b"same-image-bytes")
    different.write_bytes(b"different-image-bytes")

    findings, issues = ExactImageDuplicateDetector().scan([first, second, different])

    assert issues == []
    assert len(findings) == 1
    assert findings[0].details["sha256"] == sha256_file(first)
    assert {location.source_path for location in findings[0].locations} == {
        str(first),
        str(second),
    }
