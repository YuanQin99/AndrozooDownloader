"""Split AndroZoo samples into yearly benign and malware CSV files."""

from __future__ import annotations

import argparse
import csv
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from tqdm import tqdm

from read_csv import load_config, parse_year


def filter_yearly(
    input_path: str | Path,
    output_dir: str | Path,
    start_year: int,
    end_year: int,
    benign_detection: int = 0,
    malware_min_detection: int = 5,
) -> dict[str, Any]:
    if end_year < start_year:
        raise ValueError("end_year 不能小于 start_year")
    if malware_min_detection <= benign_detection:
        raise ValueError("malware_min_vt_detection 必须大于 benign_vt_detection")

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    years = range(start_year, end_year + 1)
    counts = {year: {"benign": 0, "malware": 0} for year in years}
    stats = {
        "total": 0,
        "invalid_date": 0,
        "invalid_detection": 0,
        "outside_year_range": 0,
        "excluded_detection": 0,
        "counts": counts,
    }

    file_size = input_path.stat().st_size
    with input_path.open("r", newline="", encoding="utf-8-sig") as source, ExitStack() as stack, tqdm(
        total=file_size,
        desc="按年份筛选 CSV",
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        dynamic_ncols=True,
    ) as progress:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError(f"CSV 没有标题行: {input_path}")
        missing = {"vt_scan_date", "vt_detection"}.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV 缺少字段: {', '.join(sorted(missing))}")

        writers: dict[tuple[int, str], csv.DictWriter] = {}
        for year in years:
            paths = {
                "benign": output_dir / f"benign_{year}_vt_detection_eq_{benign_detection}.csv",
                "malware": output_dir / f"malware_{year}_vt_detection_gt_{malware_min_detection - 1}.csv",
            }
            for category, path in paths.items():
                if path.resolve() == input_path.resolve():
                    raise ValueError("输入 CSV 不能与任何输出 CSV 相同")
                output_file = stack.enter_context(
                    path.open("w", newline="", encoding="utf-8")
                )
                writer = csv.DictWriter(output_file, fieldnames=reader.fieldnames)
                writer.writeheader()
                writers[(year, category)] = writer

        for row in reader:
            stats["total"] += 1
            if stats["total"] % 10_000 == 0:
                bytes_read = min(source.buffer.tell(), file_size)
                progress.update(bytes_read - progress.n)
                benign_total = sum(item["benign"] for item in counts.values())
                malware_total = sum(item["malware"] for item in counts.values())
                progress.set_postfix(
                    rows=stats["total"], benign=benign_total, malware=malware_total
                )

            try:
                year = parse_year(row["vt_scan_date"])
            except (AttributeError, TypeError, ValueError):
                stats["invalid_date"] += 1
                continue
            if year < start_year or year > end_year:
                stats["outside_year_range"] += 1
                continue
            try:
                detection = int(row["vt_detection"].strip())
            except (AttributeError, TypeError, ValueError):
                stats["invalid_detection"] += 1
                continue

            if detection == benign_detection:
                writers[(year, "benign")].writerow(row)
                counts[year]["benign"] += 1
            elif detection >= malware_min_detection:
                writers[(year, "malware")].writerow(row)
                counts[year]["malware"] += 1
            else:
                stats["excluded_detection"] += 1

        progress.update(file_size - progress.n)
        progress.set_postfix(
            rows=stats["total"],
            benign=sum(item["benign"] for item in counts.values()),
            malware=sum(item["malware"] for item in counts.values()),
        )

    summary_path = output_dir / "yearly_counts.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as summary_file:
        writer = csv.DictWriter(
            summary_file,
            fieldnames=["year", "benign_count", "malware_count", "total_selected"],
        )
        writer.writeheader()
        for year in years:
            benign_count = counts[year]["benign"]
            malware_count = counts[year]["malware"]
            writer.writerow(
                {
                    "year": year,
                    "benign_count": benign_count,
                    "malware_count": malware_count,
                    "total_selected": benign_count + malware_count,
                }
            )
    stats["summary_path"] = str(summary_path.resolve())
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按 vt_scan_date 年份拆分良性和恶意 AndroZoo CSV"
    )
    parser.add_argument("--config", default="config.json", help="JSON 配置文件")
    parser.add_argument("--input", help="覆盖 yearly_filter.input_csv")
    parser.add_argument("--output-dir", help="覆盖 yearly_filter.output_dir")
    parser.add_argument("--start-year", type=int, help="覆盖 yearly_filter.start_year")
    parser.add_argument("--end-year", type=int, help="覆盖 yearly_filter.end_year")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    section = load_config(args.config).get("yearly_filter", {})
    input_path = args.input or section["input_csv"]
    output_dir = args.output_dir or section["output_dir"]
    start_year = args.start_year if args.start_year is not None else int(section["start_year"])
    end_year = args.end_year if args.end_year is not None else int(section["end_year"])
    benign_detection = int(section.get("benign_vt_detection", 0))
    malware_min_detection = int(section.get("malware_min_vt_detection", 5))

    stats = filter_yearly(
        input_path,
        output_dir,
        start_year,
        end_year,
        benign_detection,
        malware_min_detection,
    )
    print(f"按年份筛选完成: {Path(output_dir).resolve()}")
    for year in range(start_year, end_year + 1):
        count = stats["counts"][year]
        print(f"{year}: 良性={count['benign']}, 恶意={count['malware']}")
    print(f"数量汇总: {stats['summary_path']}")
    print(
        f"总行数={stats['total']}, 无效日期={stats['invalid_date']}, "
        f"无效检测数={stats['invalid_detection']}, 年份范围外={stats['outside_year_range']}, "
        f"检测数1-{malware_min_detection - 1}排除={stats['excluded_detection']}"
    )


if __name__ == "__main__":
    main()
