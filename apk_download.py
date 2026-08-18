"""Download APK files from a filtered AndroZoo CSV with a process pool."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from tqdm import tqdm

from read_csv import default_output_path


RESULT_FIELDS = [
    "sha256", "file_path", "success", "status", "http_status",
    "file_size", "error", "finished_at",
]
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_result(
    sha256: str,
    file_path: Path,
    success: bool,
    status: str,
    *,
    http_status: int | str = "",
    file_size: int | str = "",
    error: str = "",
) -> dict[str, str | int]:
    return {
        "sha256": sha256,
        "file_path": str(file_path.resolve()),
        "success": "1" if success else "0",
        "status": status,
        "http_status": http_status,
        "file_size": file_size,
        "error": error,
        "finished_at": utc_now(),
    }


def download_one(sha256: str, settings: dict[str, Any]) -> dict[str, str | int]:
    download_dir = Path(settings["download_dir"])
    file_path = download_dir / f"{sha256}.apk"
    part_path = download_dir / f"{sha256}.{os.getpid()}.part"
    download_dir.mkdir(parents=True, exist_ok=True)

    if file_path.is_file() and file_path.stat().st_size > 0:
        return make_result(
            sha256, file_path, True, "already_exists", file_size=file_path.stat().st_size
        )

    proxy = settings.get("proxy", {})
    proxies = None
    if proxy.get("enabled", False):
        proxy_url = proxy.get("url", "").strip()
        if not proxy_url:
            return make_result(
                sha256, file_path, False, "config_error", error="代理已启用但 url 为空"
            )
        proxies = {"http": proxy_url, "https": proxy_url}

    timeout = float(settings.get("timeout_seconds", 120))
    retries = max(0, int(settings.get("retries", 2)))
    chunk_size = max(8192, int(settings.get("chunk_size", 1024 * 1024)))
    last_error = ""
    last_status: int | str = ""

    for attempt in range(retries + 1):
        try:
            with requests.get(
                "https://androzoo.uni.lu/api/download",
                params={"apikey": settings["api_key"], "sha256": sha256},
                proxies=proxies,
                stream=True,
                timeout=(30, timeout),
            ) as response:
                last_status = response.status_code
                response.raise_for_status()
                with part_path.open("wb") as target:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            target.write(chunk)
                if part_path.stat().st_size == 0:
                    raise OSError("服务端返回了空文件")
                os.replace(part_path, file_path)
                return make_result(
                    sha256,
                    file_path,
                    True,
                    "downloaded",
                    http_status=response.status_code,
                    file_size=file_path.stat().st_size,
                )
        except Exception as exc:  # Log both HTTP and filesystem errors.
            last_error = f"{type(exc).__name__}: {exc}"
            if part_path.exists():
                part_path.unlink()
            if attempt < retries:
                time.sleep(min(2**attempt, 10))

    return make_result(
        sha256, file_path, False, "failed", http_status=last_status, error=last_error
    )


def read_successful_sha256(result_path: Path) -> set[str]:
    successful: set[str] = set()
    if not result_path.exists():
        return successful
    with result_path.open("r", newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            if row.get("success", "").strip().lower() in {"1", "true", "yes"}:
                successful.add(row.get("sha256", "").strip().upper())
    return successful


def iter_sha256(input_path: Path, skipped: set[str]) -> Iterable[str]:
    seen: set[str] = set()
    with input_path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "sha256" not in reader.fieldnames:
            raise ValueError("输入 CSV 缺少 sha256 字段")
        for row in reader:
            sha256 = row.get("sha256", "").strip().upper()
            if sha256 in skipped or sha256 in seen:
                continue
            seen.add(sha256)
            yield sha256


def write_result(writer: csv.DictWriter, result_file: Any, result: dict[str, Any]) -> None:
    writer.writerow(result)
    result_file.flush()


def run_downloads(settings: dict[str, Any]) -> dict[str, int]:
    input_path = Path(settings["input_csv"])
    result_path = Path(settings["result_csv"])
    if input_path.resolve() == result_path.resolve():
        raise ValueError("输入 CSV 和下载记录 CSV 不能是同一个文件")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    Path(settings["download_dir"]).mkdir(parents=True, exist_ok=True)

    resume = bool(settings.get("resume", True))
    skipped = read_successful_sha256(result_path) if resume else set()
    append = result_path.exists() and result_path.stat().st_size > 0 and resume
    workers = max(1, int(settings.get("workers", max(1, (os.cpu_count() or 2) // 2))))
    max_pending = workers * 2
    configured_limit = settings.get("max_downloads", 0)
    max_downloads = int(configured_limit) if configured_limit is not None else 0
    if max_downloads < 0:
        raise ValueError("download.max_downloads 不能小于 0")
    stats = {
        "candidates": 0, "submitted": 0, "success": 0, "failed": 0,
        "invalid_sha256": 0, "skipped": len(skipped),
    }

    # Only the main process writes the result CSV, avoiding cross-process corruption.
    with result_path.open("a" if append else "w", newline="", encoding="utf-8") as result_file:
        writer = csv.DictWriter(result_file, fieldnames=RESULT_FIELDS)
        if not append:
            writer.writeheader()
            result_file.flush()

        sha256_source = iter_sha256(input_path, skipped)
        candidates_precounted = max_downloads > 0
        if max_downloads > 0:
            # Uniform reservoir sampling keeps memory bounded by max_downloads.
            selected: list[str] = []
            rng = random.Random(settings.get("random_seed"))
            for sha256 in sha256_source:
                if not SHA256_PATTERN.fullmatch(sha256):
                    result = make_result(
                        sha256,
                        Path(settings["download_dir"]) / f"{sha256}.apk",
                        False,
                        "invalid_sha256",
                        error="sha256 必须是 64 位十六进制字符串",
                    )
                    write_result(writer, result_file, result)
                    stats["invalid_sha256"] += 1
                    stats["failed"] += 1
                    continue

                stats["candidates"] += 1
                if len(selected) < max_downloads:
                    selected.append(sha256)
                else:
                    replacement = rng.randrange(stats["candidates"])
                    if replacement < max_downloads:
                        selected[replacement] = sha256
            rng.shuffle(selected)
            sha256_source = iter(selected)

        download_total = len(selected) if max_downloads > 0 else None
        pending: dict[Future[dict[str, Any]], str] = {}
        with tqdm(
            total=download_total,
            desc="下载 APK",
            unit="apk",
            dynamic_ncols=True,
        ) as download_progress, ProcessPoolExecutor(max_workers=workers) as executor:
            for sha256 in sha256_source:
                if not SHA256_PATTERN.fullmatch(sha256):
                    result = make_result(
                        sha256,
                        Path(settings["download_dir"]) / f"{sha256}.apk",
                        False,
                        "invalid_sha256",
                        error="sha256 必须是 64 位十六进制字符串",
                    )
                    write_result(writer, result_file, result)
                    stats["invalid_sha256"] += 1
                    stats["failed"] += 1
                    continue

                if not candidates_precounted:
                    stats["candidates"] += 1
                future = executor.submit(download_one, sha256, settings)
                pending[future] = sha256
                stats["submitted"] += 1
                if len(pending) >= max_pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        submitted_sha256 = pending.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = make_result(
                                submitted_sha256,
                                Path(settings["download_dir"]) / f"{submitted_sha256}.apk",
                                False,
                                "worker_error",
                                error=f"{type(exc).__name__}: {exc}",
                            )
                        write_result(writer, result_file, result)
                        stats["success" if result["success"] == "1" else "failed"] += 1
                        download_progress.update(1)
                        download_progress.set_postfix(
                            success=stats["success"], failed=stats["failed"]
                        )

            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    submitted_sha256 = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = make_result(
                            submitted_sha256,
                            Path(settings["download_dir"]) / f"{submitted_sha256}.apk",
                            False,
                            "worker_error",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    write_result(writer, result_file, result)
                    stats["success" if result["success"] == "1" else "failed"] += 1
                    download_progress.update(1)
                    download_progress.set_postfix(
                        success=stats["success"], failed=stats["failed"]
                    )
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="多进程下载 AndroZoo APK")
    parser.add_argument("--config", default="config.json", help="JSON 配置文件")
    parser.add_argument("--input", help="覆盖 download.input_csv")
    parser.add_argument("--download-dir", help="覆盖 download.download_dir")
    parser.add_argument("--result", help="覆盖 download.result_csv")
    parser.add_argument("--workers", type=int, help="覆盖 download.workers")
    proxy_group = parser.add_mutually_exclusive_group()
    proxy_group.add_argument("--proxy", help="启用代理并覆盖代理 URL")
    proxy_group.add_argument("--no-proxy", action="store_true", help="禁用代理")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    settings = dict(config.get("download", {}))
    if not settings.get("input_csv"):
        filter_settings = config.get("filter", {})
        filter_output = filter_settings.get("output_csv")
        if filter_output:
            settings["input_csv"] = filter_output
        else:
            configured_max = filter_settings.get("max_vt_detection")
            settings["input_csv"] = str(
                default_output_path(
                    int(filter_settings["year"]),
                    int(filter_settings.get("min_vt_detection", 0)),
                    int(configured_max) if configured_max is not None else None,
                )
            )
    if args.input:
        settings["input_csv"] = args.input
    if args.download_dir:
        settings["download_dir"] = args.download_dir
    if args.result:
        settings["result_csv"] = args.result
    if args.workers is not None:
        settings["workers"] = args.workers

    settings["proxy"] = dict(settings.get("proxy", {}))
    if args.proxy:
        settings["proxy"] = {"enabled": True, "url": args.proxy}
    elif args.no_proxy:
        settings["proxy"]["enabled"] = False

    if not settings.get("api_key"):
        raise ValueError("未配置 API Key，请填写 download.api_key")
    for required in ("input_csv", "download_dir", "result_csv"):
        if not settings.get(required):
            raise ValueError(f"download.{required} 不能为空")

    stats = run_downloads(settings)
    print(f"下载记录: {Path(settings['result_csv']).resolve()}")
    print(
        f"候选={stats['candidates']}, 提交={stats['submitted']}, "
        f"成功={stats['success']}, 失败={stats['failed']}, "
        f"无效SHA256={stats['invalid_sha256']}, 已跳过成功记录={stats['skipped']}"
    )


if __name__ == "__main__":
    # Required by ProcessPoolExecutor on Windows.
    main()
