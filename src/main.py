"""Initialize local folders or discover assigned papers; source download is separate."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from .config import CrawlConfig
from .discovery import discover_papers, load_ids, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument(
        "--init", action="store_true", help="Create local output directories only."
    )
    parser.add_argument("--ids", type=Path, help="UTF-8 TXT file, one arXiv ID per line")
    parser.add_argument("--discover", action="store_true", help="Resolve IDs and enumerate versions")
    parser.add_argument("--resume", action="store_true", help="Reuse successful discovery snapshot")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    if not args.init and not args.discover:
        parser.error("Choose --init or --discover --ids FILE")
    if args.discover and args.ids is None:
        parser.error("--discover requires --ids FILE")
    if args.timeout <= 0 or args.attempts < 1:
        parser.error("Timeout must be positive; attempts must be >= 1")
    load_dotenv()
    config = CrawlConfig(output_dir=args.output, request_timeout_seconds=args.timeout,
                         max_attempts=args.attempts,
                         user_agent=os.getenv("ARXIV_USER_AGENT", "Lab1ArxivCrawler/0.1"))
    for name in ("corpus", "state", "metrics", "tmp"):
        (config.output_dir / name).mkdir(parents=True, exist_ok=True)
    print(f"Local data directories created: {config.output_dir.resolve()}")
    if args.discover:
        try:
            inputs = load_ids(args.ids)
        except (OSError, UnicodeError) as exc:
            parser.error(str(exc))
        write_json(args.output / "state" / "input_validation.json", inputs)
        if not inputs["ids"]:
            parser.error("No valid IDs; see state/input_validation.json")
        result = discover_papers(inputs["ids"], config, resume=args.resume)
        print(result["summary"])
        print(f"Discovery output: {args.output / 'state' / 'discovery.json'}")
        if inputs["invalid"] or result["summary"]["failed"]:
            raise SystemExit(1)
    print("No sources downloaded: this command only initializes/discovers papers.")


if __name__ == "__main__":
    main()
