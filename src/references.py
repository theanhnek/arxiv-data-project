"""Member 4: collect and cache Semantic Scholar references by base arXiv ID.

Implement fetch_references(arxiv_id, config). Keep all raw references, including
those without arXiv IDs. Handle throttling, retry delays and missing metadata.
Document that cached publication-level references may not distinguish versions.
Never fabricate a submission date from a publication year.
"""
