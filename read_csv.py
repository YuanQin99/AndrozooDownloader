"""Filter an AndroZoo latest.csv by vt_scan_date year and detection count."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tqdm import tqdm


REQUIRED_COLUMNS = {"vt_scan_date", "vt_detection"}


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def default_output_path(year: int, minimum: int, maximum: int | None) -> Path:
    detection = f"{minimum}-{maximum}" if maximum is not None else f"ge_{minimum}"
    return Path(f"filtered_vt_scan_{year}_detection_{detection}.csv")


def parse_year(value: str) -> int:
    """Parse the common AndroZoo timestamp without a third-party dependency."""
    value = value.strip()
    if len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return datetime.fromisoformat(value.replace("Z", "+00:00")).year


def filter_csv(
    input_path: str | Path,
    output_path: str | Path,
    year: int,
    min_detection: int,
    max_detection: int | None = None,
) -> dict[str, int]:
    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("输入 CSV 和输出 CSV 不能是同一个文件")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats = {"total": 0, "matched": 0, "invalid_date": 0, "invalid_detection": 0}

    file_size = input_path.stat().st_size
    with input_path.open("r", newline="", encoding="utf-8-sig") as source, tqdm(
        total=file_size,
        desc="筛选 CSV",
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        dynamic_ncols=True,
    ) as progress:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError(f"CSV 没有标题行: {input_path}")
        missing = REQUIRED_COLUMNS.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV 缺少字段: {', '.join(sorted(missing))}")

        with output_path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                stats["total"] += 1
                if stats["total"] % 10_000 == 0:
                    bytes_read = min(source.buffer.tell(), file_size)
                    progress.update(bytes_read - progress.n)
                    progress.set_postfix(rows=stats["total"], matched=stats["matched"])
                try:
                    scan_year = parse_year(row["vt_scan_date"])
                except (AttributeError, TypeError, ValueError):
                    stats["invalid_date"] += 1
                    continue
                try:
                    detection = int(row["vt_detection"].strip())
                except (AttributeError, TypeError, ValueError):
                    stats["invalid_detection"] += 1
                    continue

                detection_matches = detection >= min_detection
                if max_detection is not None:
                    detection_matches = detection_matches and detection <= max_detection
                if scan_year == year and detection_matches:
                    writer.writerow(row)
                    stats["matched"] += 1

            progress.update(file_size - progress.n)
            progress.set_postfix(rows=stats["total"], matched=stats["matched"])
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按 vt_scan_date 年份和 vt_detection 数量筛选 AndroZoo CSV"
    )
    parser.add_argument("--config", default="config.json", help="JSON 配置文件")
    parser.add_argument("--input", help="覆盖 filter.input_csv")
    parser.add_argument("--output", help="覆盖 filter.output_csv")
    parser.add_argument("--year", type=int, help="覆盖 filter.year")
    parser.add_argument("--min-detection", type=int, help="覆盖 filter.min_vt_detection")
    parser.add_argument("--max-detection", type=int, help="覆盖 filter.max_vt_detection")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    section = load_config(args.config).get("filter", {})
    year = args.year if args.year is not None else int(section["year"])
    minimum = args.min_detection if args.min_detection is not None else int(
        section.get("min_vt_detection", 0)
    )
    configured_max = section.get("max_vt_detection")
    maximum = args.max_detection if args.max_detection is not None else configured_max
    maximum = int(maximum) if maximum is not None else None
    input_path = args.input or section["input_csv"]
    output_path = args.output or section.get("output_csv")
    output_path = output_path or default_output_path(year, minimum, maximum)

    if maximum is not None and maximum < minimum:
        raise ValueError("max_vt_detection 不能小于 min_vt_detection")

    stats = filter_csv(input_path, output_path, year, minimum, maximum)
    print(f"筛选完成: {Path(output_path).resolve()}")
    print(
        f"总行数={stats['total']}, 命中={stats['matched']}, "
        f"无效日期={stats['invalid_date']}, 无效检测数={stats['invalid_detection']}"
    )


if __name__ == "__main__":
    main()
