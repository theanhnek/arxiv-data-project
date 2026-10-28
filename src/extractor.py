"""Member 3: detect source formats and extract only .tex and .bib files.

Implement extract_source(source_path, destination). Preserve relative paths and
file contents. Validate archive paths against traversal before writing files;
reject links. Keep figure commands inside TeX untouched. Distinguish no-TeX
sources from corrupt archives or download failures. Record byte counts.
"""
