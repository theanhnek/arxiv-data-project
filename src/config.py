"""Shared configuration. All modules must use the same pacing and paths."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CrawlConfig:
    output_dir: Path
    arxiv_delay_seconds: float = 3.0
    request_timeout_seconds: float = 60.0
    max_attempts: int = 3
