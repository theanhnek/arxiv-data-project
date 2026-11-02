"""Initialize local folders, discover assigned papers, download identified versions, and extract LaTeX sources."""

import argparse
import os
from pathlib import Path

from .config import CrawlConfig
from .discovery import (
    DEFAULT_MEMBER_QUOTA,
    ArxivClient,
    discover_papers,
    load_ids,
    parse_assigned_range_line,
    prepare_assigned_ids,
    write_json,
)
from .downloader import download_discovered_sources
from .extractor import extract_downloaded_sources


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument(
        "--init", action="store_true", help="Create local output directories only."
    )
    parser.add_argument(
        "--ids",
        type=Path,
        help="UTF-8 TXT/TSV file with one arXiv ID or range (start_month start_id end_month end_id) per line",
    )
    parser.add_argument(
        "--range",
        nargs=4,
        metavar=("START_MONTH", "START_ID", "END_MONTH", "END_ID"),
        help="Assigned range, e.g. 2025-10 9401 2025-10 14400",
    )
    parser.add_argument(
        "--member-index",
        type=int,
        default=None,
        help="1-based member row index when --ids points to a multi-row range file",
    )
    parser.add_argument(
        "--expected-total",
        type=int,
        default=DEFAULT_MEMBER_QUOTA,
        help="Expected papers per assigned range for cross-month calculation (default: 5000)",
    )
    parser.add_argument(
        "--discover", action="store_true", help="Resolve IDs and enumerate versions first"
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download versioned sources from state/discovery.json after discovery",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Extract TeX/BibTeX sources from state/downloads.json into corpus/<paper>/<version>/source/",
    )
    parser.add_argument("--resume", action="store_true", help="Reuse successful snapshots")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()

    if not args.init and not args.discover and not args.download and not args.extract:
        parser.error(
            "Choose --init, --discover (--ids FILE or --range ...), --download, or --extract"
        )
    if args.discover and args.ids is None and args.range is None:
        parser.error(
            "--discover requires --ids FILE or --range START_MONTH START_ID END_MONTH END_ID"
        )
    if args.timeout <= 0 or args.attempts < 1:
        parser.error("Timeout must be positive; attempts must be >= 1")

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    config = CrawlConfig(
        output_dir=args.output,
        request_timeout_seconds=args.timeout,
        max_attempts=args.attempts,
        user_agent=os.getenv("ARXIV_USER_AGENT", "Lab1ArxivCrawler/0.1"),
    )
    for name in ("corpus", "state", "metrics", "tmp"):
        (config.output_dir / name).mkdir(parents=True, exist_ok=True)
    print(f"Local data directories created: {config.output_dir.resolve()}")

    client = ArxivClient(config) if (args.discover or args.download) else None
    exit_code = 0
    try:
        if args.discover:
            if args.range is not None:
                try:
                    parsed = parse_assigned_range_line(" ".join(args.range))
                    ids_file = args.ids or (config.output_dir / "assigned_ids.txt")
                    expanded = prepare_assigned_ids(
                        parsed["start_month"],
                        parsed["start_id"],
                        parsed["end_month"],
                        parsed["end_id"],
                        ids_file,
                        expected_total=args.expected_total,
                    )
                except ValueError as exc:
                    parser.error(str(exc))
                inputs = {"ids": expanded, "invalid": [], "duplicates": []}
            else:
                try:
                    inputs = load_ids(
                        args.ids,
                        member_index=args.member_index,
                        expected_total=args.expected_total,
                    )
                except (OSError, UnicodeError) as exc:
                    parser.error(str(exc))

            write_json(args.output / "state" / "input_validation.json", inputs)
            if not inputs["ids"]:
                parser.error("No valid IDs; see state/input_validation.json")
            result = discover_papers(
                inputs["ids"], config, resume=args.resume, client=client
            )
            print(result["summary"])
            print(f"Discovery output: {args.output / 'state' / 'discovery.json'}")
            if inputs["invalid"] or result["summary"]["failed"]:
                exit_code = 1

        if args.download:
            dl_report = download_discovered_sources(
                config, resume=args.resume, client=client
            )
            print(dl_report["summary"])
            print(f"Download output: {args.output / 'state' / 'downloads.json'}")
            if dl_report["summary"]["failed"] or dl_report["summary"]["invalid_response"]:
                exit_code = 1
        elif not args.extract and not args.init:
            print("No sources downloaded: this command only initializes/discovers papers.")

        if args.extract:
            ext_report = extract_downloaded_sources(config, resume=args.resume)
            print(ext_report["summary"])
            print(f"Extraction output: {args.output / 'state' / 'extractions.json'}")
            if ext_report["summary"]["failed"]:
                exit_code = 1
    finally:
        if client is not None:
            client.close()

    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
