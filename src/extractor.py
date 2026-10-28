"""Extract only .tex and .bib files from a paper source."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union


@dataclass
class ExtractionResult:
    """Summary of one extraction attempt."""

    status: str
    files: List[str] = field(default_factory=list)
    retained_bytes: int = 0
    error: Optional[str] = None


def extract_source(
    source_path: Union[str, Path],
    destination: Union[str, Path],
) -> ExtractionResult:
    """Extract allowed source files and return the outcome."""
    raise NotImplementedError("Extraction is not implemented yet")