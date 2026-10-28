"""Shared storage, coordinated by members 1 and 4.

Implement safe ID-to-folder mapping, JSON persistence and restart state.
Keep original IDs in metadata. Store every paper/version, including empty
source records. Only references with arXiv IDs enter the final references table.
Use atomic writes so interrupted runs do not leave valid-looking partial files.
"""
