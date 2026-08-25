from __future__ import annotations

import argparse
import shutil
from importlib import metadata
from pathlib import Path

LICENSE_NAMES = ("license", "licence", "copying", "notice", "copyright")
FALLBACK_LICENSES = {
    "pyside6_essentials": ("LGPL-3.0.txt", "GPL-3.0.txt"),
    "shiboken6": ("LGPL-3.0.txt", "GPL-3.0.txt"),
}


def collect_licenses(destination: Path, distributions: list[str]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for distribution_name in distributions:
        distribution = metadata.distribution(distribution_name)
        target = destination / f"{distribution.metadata['Name']}-{distribution.version}"
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
        for relative in distribution.files or ():
            lowered_parts = [part.lower() for part in relative.parts]
            if not any(
                any(part.startswith(name) for name in LICENSE_NAMES) for part in lowered_parts
            ):
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file():
                continue
            relative_target = Path(*relative.parts[-2:]) if len(relative.parts) > 1 else relative
            output = target / relative_target
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
            copied += 1
        fallback_names = FALLBACK_LICENSES.get(distribution_name.lower(), ())
        for filename in fallback_names:
            source = Path(__file__).resolve().parents[1] / "legal" / "third_party" / filename
            shutil.copy2(source, target / filename)
            copied += 1
        if copied == 0:
            missing.append(distribution_name)
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"以下发行包未找到许可证文件：{names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="收集发行包第三方许可证")
    parser.add_argument("destination", type=Path)
    parser.add_argument("distributions", nargs="+")
    arguments = parser.parse_args()
    collect_licenses(arguments.destination, arguments.distributions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
