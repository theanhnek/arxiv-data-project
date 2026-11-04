"""Shared configuration. All modules must use the same pacing and paths."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CrawlConfig:
    output_dir: Path
    arxiv_delay_seconds: float = 3.0
    request_timeout_seconds: float = 60.0
    max_attempts: int = 3
    user_agent: str = "Lab1ArxivCrawler/0.1"
    metrics_sample_interval_seconds: float = 0.5
    s2_delay_seconds: float = 3.1
    s2_api_key: Optional[str] = None
