"""Command-line entry point; the crawling pipeline is not implemented yet."""

import argparse
from pathlib import Path

from .config import CrawlConfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument(
        "--init", action="store_true", help="Create local output directories only."
    )
    args = parser.parse_args()
    if not args.init:
        parser.error("Pipeline is not implemented. Use --init to prepare local folders.")
    config = CrawlConfig(output_dir=args.output)
    for name in ("corpus", "state", "metrics", "tmp"):
        (config.output_dir / name).mkdir(parents=True, exist_ok=True)
    print(f"Local data directories created: {config.output_dir.resolve()}")
    print("No papers were downloaded. Implement the pipeline before crawling.")


if __name__ == "__main__":
    main()
