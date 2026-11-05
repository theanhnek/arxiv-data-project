"""Shared configuration. All modules must use the same pacing and paths."""

from dataclasses import dataclass, field
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
    # Semantic Scholar (member 4).
    # Minimum gap between requests. 3.1 s keeps an unauthenticated run below the
    # documented 100 requests / 5 minutes; with an API key 1.0 s is allowed.
    s2_delay_seconds: float = 3.1
    # repr=False so the key never appears in logs or tracebacks that print the config.
    s2_api_key: Optional[str] = field(default=None, repr=False)
    # Without a key the API also allows 100 requests per 5 minutes; stay below that.
    # The window limit is not applied when an API key is configured.
    s2_window_seconds: float = 300.0
    s2_window_max_requests: int = 95
    # Page size for the paginated references endpoint (API maximum is 1000).
    s2_page_limit: int = 500
