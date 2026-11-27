from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None  # type: ignore[assignment]
    pq = None  # type: ignore[assignment]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.downloader import safe_paper_dirname  # noqa: E402
from src.metrics import ResourceMonitor  # noqa: E402

SECTION_LEVEL = {
    "part": 1,
    "chapter": 1,
    "section": 2,
    "subsection": 3,
    "subsubsection": 4,
    "paragraph": 5,
    "subparagraph": 6,
}
SECTION_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?"
    r"\s*(?:\[[^\]]*\]\s*)?\{",
    re.I,
)
BEGIN_RE = re.compile(r"\\begin\s*\{([^{}]+)\}")
INPUT_RE = re.compile(r"\\(?:input|include)\s*(?:\{([^{}]+)\}|([^\s%]+))")
BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}", re.I)
DISPLAY_DOLLAR_RE = re.compile(r"\$\$(.+?)\$\$", re.S)
BRACKET_MATH_RE = re.compile(r"\\\[(.+?)\\\]", re.S)
CITE_CMD_RE = re.compile(
    r"\\(cite[a-zA-Z]*|parencite|textcite|autocite)\*?\s*(?:\[[^\]]*\]\s*)*\{([^{}]*)\}"
)
BRACKET_CITE_RE = re.compile(r"\[([A-Za-z0-9_.:,;+\-/ ]+)\]")
FORMULA_ENVS = {
    "equation",
    "equation*",
    "align",
    "align*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "displaymath",
    "math",
    "eqnarray",
    "eqnarray*",
}
LIST_ENVS = {"itemize", "enumerate", "description"}
REFERENCE_TITLES = re.compile(
    r"^(references?|bibliography|works cited|literature cited|references and notes|"
    r"bibliographie|bibliografia|referencias)(\b|\s|[:.-])",
    re.I,
)
TEXT_COMMANDS = re.compile(
    r"\\(?:emph|textbf|textit|texttt|textrm|textsf|textsc|underline|uline|"
    r"mbox|makebox|ensuremath|protect|url|path|href|footnote|thanks|caption|"
    r"centering|small|large|normalsize|noindent|newpage|clearpage|par|item|"
    r"toprule|midrule|bottomrule|hline|cline)\*?"
    r"(?:\[[^\]]*\])?\s*",
    re.I,
)
ABBREVIATIONS = [
    "e.g.",
    "i.e.",
    "et al.",
    "Fig.",
    "Figs.",
    "Eq.",
    "Eqs.",
    "Dr.",
    "Prof.",
    "vs.",
    "approx.",
    "No.",
    "Sec.",
    "Ch.",
]


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        chars = []
        slashes = 0
        for ch in line:
            if ch == "%" and slashes % 2 == 0:
                break
            chars.append(ch)
            slashes = slashes + 1 if ch == "\\" else 0
        out.append("".join(chars))
    return "\n".join(out)


def first_conditional_branch(text: str) -> str:
    """Keep the first branch of TeX conditionals, which is commonly the arXiv path."""
    token_re = re.compile(r"\\(if[a-zA-Z@]*|else|fi)\b")
    output = []
    frames: List[Tuple[bool, bool]] = []
    active = True
    pos = 0
    for match in token_re.finditer(text):
        if active:
            output.append(text[pos : match.start()])
        token = match.group(1).lower()
        if token.startswith("if"):
            parent = active
            frames.append((parent, True))
            active = parent
        elif token == "else" and frames:
            parent, first = frames[-1]
            frames[-1] = (parent, False)
            active = parent and not first
        elif token == "fi" and frames:
            parent, _ = frames.pop()
            active = parent
        pos = match.end()
    if active:
        output.append(text[pos:])
    return "".join(output)


def read_braced(text: str, opening: int) -> Tuple[str, int]:
    if opening >= len(text) or text[opening] != "{":
        return "", opening
    depth = 0
    escaped = False
    for i in range(opening, len(text)):
        ch = text[i]
        if ch == "{" and not escaped:
            depth += 1
        elif ch == "}" and not escaped:
            depth -= 1
            if depth == 0:
                return text[opening + 1 : i], i + 1
        if ch == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    return text[opening + 1 :], len(text)


def resolve_input(root: Path, current: Path, name: str) -> Optional[Path]:
    name = name.strip().strip("\"'")
    if not name or name.startswith("/") or ".." in Path(name).parts:
        return None
    candidates = [
        current.parent / name,
        current.parent / f"{name}.tex",
        root / name,
        root / f"{name}.tex",
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved.suffix.lower() == ".tex":
            return resolved
    return None


def expand_includes(
    path: Path, root: Path, seen: Optional[set[Path]] = None, depth: int = 0
) -> str:
    seen = seen or set()
    resolved = path.resolve()
    if resolved in seen or depth > 40:
        return ""
    seen.add(resolved)
    try:
        source = strip_comments(resolved.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    source = first_conditional_branch(source)
    out = []
    pos = 0
    for match in INPUT_RE.finditer(source):
        out.append(source[pos : match.start()])
        target_name = match.group(1) or match.group(2)
        target = resolve_input(root, resolved, target_name)
        if target is not None:
            out.append(expand_includes(target, root, seen, depth + 1))
        pos = match.end()
    out.append(source[pos:])
    return "\n".join(out)


def choose_root(tex_files: Sequence[Path]) -> Optional[Path]:
    candidates = []
    for path in tex_files:
        try:
            text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if re.search(r"\\documentclass\b", text):
            score = len(INPUT_RE.findall(text)) + (
                10 if r"\begin{document}" in text else 0
            )
            candidates.append((score, -len(path.name), path))
    return max(candidates)[2] if candidates else None


def normalize_display_math(text: str) -> str:
    """Convert $$...$$ and \\[...\\] into \\begin{equation}...\\end{equation} before parsing."""
    text = DISPLAY_DOLLAR_RE.sub(
        lambda m: "\n\\begin{equation}" + m.group(1).strip() + "\\end{equation}\n",
        text,
    )
    text = BRACKET_MATH_RE.sub(
        lambda m: "\n\\begin{equation}" + m.group(1).strip() + "\\end{equation}\n",
        text,
    )
    return text


def rename_citations_in_latex(text: str, key_map: Dict[str, str]) -> str:
    """Rename duplicate citation keys inside \\cite{...} commands to their unified canonical key."""
    if not key_map:
        return text

    def _replace_cite(match: re.Match[str]) -> str:
        cmd = match.group(1)
        raw_keys = match.group(2)
        updated: List[str] = []
        for k in raw_keys.split(","):
            stripped = re.sub(r"\s+", "", k)
            if not stripped:
                continue
            canonical = key_map.get(stripped, stripped)
            if canonical not in updated:
                updated.append(canonical)
        return f"\\{cmd}{{{', '.join(updated)}}}"

    return CITE_CMD_RE.sub(_replace_cite, text)


def rename_citations_in_cleaned_content(content: str, key_map: Dict[str, str]) -> str:
    """Rename bracketed citation keys [k1, k2] in already-cleaned element content."""
    if not key_map or "[" not in content:
        return content

    def _replace_bracket(match: re.Match[str]) -> str:
        inner = match.group(1)
        parts = [p.strip() for p in inner.split(",")]
        if not any(p in key_map for p in parts):
            return match.group(0)
        new_parts: List[str] = []
        for p in parts:
            mapped = key_map.get(p, p)
            if mapped not in new_parts:
                new_parts.append(mapped)
        return "[" + ", ".join(new_parts) + "]"

    return BRACKET_CITE_RE.sub(_replace_bracket, content)


def clean_latex(text: str) -> str:
    text = text.replace("\r", " ")
    text = re.sub(
        r"\\\((.*?)\\\)", lambda m: "$" + m.group(1).strip() + "$", text, flags=re.S
    )
    text = DISPLAY_DOLLAR_RE.sub(
        lambda m: "\\begin{equation}" + m.group(1).strip() + "\\end{equation}",
        text,
    )
    text = BRACKET_MATH_RE.sub(
        lambda m: "\\begin{equation}" + m.group(1).strip() + "\\end{equation}",
        text,
    )
    text = re.sub(r"\\(?:url|path)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\href\s*\{[^{}]*\}\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(
        r"\\(?:cite[a-zA-Z]*|parencite|textcite|autocite|ref|eqref|pageref|label|index|glossary)\*?\s*(?:\[[^\]]*\]\s*)*\{([^{}]*)\}",
        r"[\1]",
        text,
    )
    text = re.sub(
        r"\\begin\s*\{[^{}]+\}\s*(?:\[[htpb!H]+\])?|\\end\s*\{[^{}]+\}", " ", text
    )
    text = TEXT_COMMANDS.sub(" ", text)
    text = re.sub(r"\\[a-zA-Z@]+\*?", " ", text)
    text = re.sub(r"\\(.)", r"\1", text)
    text = text.replace("{", " ").replace("}", " ")
    text = re.sub(r"\\[,;! ]", " ", text)
    text = re.sub(r"~", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_sentences(text: str) -> List[str]:
    protected = text
    stash: Dict[str, str] = {}
    for index, abbreviation in enumerate(ABBREVIATIONS):
        key = f"ZZABBR{index}ZZ"
        protected = re.sub(re.escape(abbreviation), key, protected, flags=re.I)
        stash[key] = abbreviation
    # Protect inline math $...$ from sentence splitting on dots inside formulas
    math_stash: Dict[str, str] = {}
    for index, m in enumerate(re.finditer(r"\$[^$]+\$", protected)):
        mkey = f"ZZMATH{index}ZZ"
        math_stash[mkey] = m.group(0)
    for mkey, mval in math_stash.items():
        protected = protected.replace(mval, mkey)
    protected = re.sub(r"(?<=\d)\.(?=\d)", "ZZDECIMALZZ", protected)
    chunks = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"'])", protected)
    result = []
    for chunk in chunks:
        for mkey, mval in math_stash.items():
            chunk = chunk.replace(mkey, mval)
        for key, value in stash.items():
            chunk = chunk.replace(key, value)
        chunk = chunk.replace("ZZDECIMALZZ", ".").strip()
        if chunk:
            result.append(chunk)
    return result


def trim_references_section(text: str) -> str:
    for match in SECTION_RE.finditer(text):
        title, _ = read_braced(text, match.end() - 1)
        if REFERENCE_TITLES.match(clean_latex(title)):
            return text[: match.start()]
    return text


def find_environment_end(text: str, name: str, start: int) -> Tuple[int, int]:
    token_re = re.compile(r"\\(begin|end)\s*\{" + re.escape(name) + r"\}")
    depth = 1
    for match in token_re.finditer(text, start):
        depth += 1 if match.group(1) == "begin" else -1
        if depth == 0:
            return match.start(), match.end()
    return len(text), len(text)


def command_argument(text: str, command: str) -> str:
    match = re.search(
        r"\\" + re.escape(command) + r"\*?\s*(?:\[[^\]]*\]\s*)?\{", text
    )
    if not match:
        return ""
    value, _ = read_braced(text, match.end() - 1)
    return value


class VersionParser:
    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = [
            {"kind": "document", "content": "Document", "parent": None}
        ]
        self.sections: List[Tuple[int, int]] = [(0, 0)]

    def add(self, kind: str, content: str, parent: int) -> int:
        content = re.sub(r"\s+", " ", content).strip()
        if not content:
            return parent
        self.nodes.append({"kind": kind, "content": content, "parent": parent})
        return len(self.nodes) - 1

    def add_text(self, text: str, parent: int) -> None:
        text = re.sub(r"\\par\b", "\n\n", text)
        for paragraph in re.split(r"\n\s*\n+", text):
            cleaned = clean_latex(paragraph)
            if not cleaned:
                continue
            p_node = self.add("paragraph", cleaned, parent)
            for sentence in split_sentences(cleaned):
                self.add("sentence", sentence, p_node)

    def parse_list(self, env: str, content: str, parent: int) -> None:
        list_id = self.add("list", env, parent)
        items = list(re.finditer(r"\\item\s*(?:\[[^\]]*\]\s*)?", content))
        if not items:
            self.add_text(content, list_id)
            return
        if content[: items[0].start()].strip():
            self.add_text(content[: items[0].start()], list_id)
        for i, match in enumerate(items):
            end = items[i + 1].start() if i + 1 < len(items) else len(content)
            item_text = content[match.end() : end]
            item_id = self.add("list_item", clean_latex(item_text), list_id)
            for sentence in split_sentences(clean_latex(item_text)):
                self.add("sentence", sentence, item_id)

    def parse(self, text: str) -> List[Dict[str, Any]]:
        begin = re.search(r"\\begin\s*\{document\}", text)
        end = text.rfind(r"\end{document}")
        if begin:
            text = text[begin.end() :]
        if end >= 0:
            text = text[:end]
        text = trim_references_section(text)
        text = normalize_display_math(text)
        marker_re = re.compile(
            r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?"
            r"\s*(?:\[[^\]]*\]\s*)?\{|\\begin\s*\{([^{}]+)\}",
            re.I,
        )
        pos = 0
        while True:
            match = marker_re.search(text, pos)
            if not match:
                self.add_text(text[pos:], self.sections[-1][1])
                break
            self.add_text(text[pos : match.start()], self.sections[-1][1])
            if match.group(1):
                name = match.group(1).lower()
                title, after = read_braced(text, match.end() - 1)
                level = SECTION_LEVEL.get(name, 2)
                while len(self.sections) > 1 and self.sections[-1][0] >= level:
                    self.sections.pop()
                parent = self.sections[-1][1]
                section_id = self.add("heading", clean_latex(title), parent)
                self.sections.append((level, section_id))
                pos = after
                continue
            env = (match.group(2) or "").strip()
            inner_start = match.end()
            env_start, env_end = find_environment_end(text, env, inner_start)
            inner = text[inner_start:env_start]
            parent = self.sections[-1][1]
            if env.lower() in {"thebibliography", "bibliography"}:
                pos = env_end
                continue
            if env.lower() in FORMULA_ENVS:
                math = re.sub(r"\s+", " ", inner).strip()
                formula = "\\begin{equation}" + math + "\\end{equation}"
                self.add("formula", formula, parent)
            elif env.lower() in {"figure", "figure*", "table", "table*"}:
                caption = clean_latex(command_argument(inner, "caption"))
                content = caption or clean_latex(inner) or "Figure/Table"
                self.add("figure", content, parent)
            elif env.lower() in LIST_ENVS:
                self.parse_list(env.lower(), inner, parent)
            elif env.lower() in {
                "abstract",
                "theorem",
                "lemma",
                "proof",
                "definition",
                "claim",
                "corollary",
                "remark",
                "example",
            }:
                label = env.lower()
                node = self.add("paragraph", label, parent)
                self.add_text(inner, node)
            else:
                self.add_text(inner, parent)
            pos = env_end
        return self.nodes


def parse_bibtex_file(path: Path) -> List[Dict[str, Any]]:
    text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    entries = []
    start_re = re.compile(r"@([a-zA-Z]+)\s*([({])")
    for match in start_re.finditer(text):
        kind = match.group(1).lower()
        if kind in {"comment", "preamble", "string"}:
            continue
        opening = match.group(2)
        closing = "}" if opening == "{" else ")"
        depth = 1
        quote = False
        escaped = False
        end = match.end()
        while end < len(text) and depth:
            ch = text[end]
            if ch == '"' and not escaped:
                quote = not quote
            elif not quote and ch == opening and not escaped:
                depth += 1
            elif not quote and ch == closing and not escaped:
                depth -= 1
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
            end += 1
        body = text[match.end() : max(match.end(), end - 1)]
        comma = _top_level_comma(body)
        if comma < 0:
            continue
        key = body[:comma].strip()
        fields = _parse_bib_fields(body[comma + 1 :])
        entries.append(_entry_from_fields(key, kind, fields))
    return entries


def _top_level_comma(text: str) -> int:
    depth = 0
    quote = False
    escaped = False
    for i, ch in enumerate(text):
        if ch == '"' and not escaped:
            quote = not quote
        elif not quote and ch == "{":
            depth += 1
        elif not quote and ch == "}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            return i
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    return -1


def _parse_bib_fields(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    i = 0
    while i < len(text):
        while i < len(text) and (text[i].isspace() or text[i] == ","):
            i += 1
        m = re.match(r"([a-zA-Z][a-zA-Z0-9_-]*)\s*=\s*", text[i:])
        if not m:
            break
        key = m.group(1).lower()
        i += m.end()
        parts = []
        while i < len(text):
            while i < len(text) and text[i].isspace():
                i += 1
            if i >= len(text):
                break
            if text[i] == "{":
                value, i = read_braced(text, i)
                parts.append(value)
            elif text[i] == '"':
                j = i + 1
                escaped = False
                while j < len(text):
                    if text[j] == '"' and not escaped:
                        break
                    escaped = text[j] == "\\" and not escaped
                    if text[j] != "\\":
                        escaped = False
                    j += 1
                parts.append(text[i + 1 : j])
                i = min(j + 1, len(text))
            else:
                j = i
                while j < len(text) and text[j] not in ",#":
                    j += 1
                parts.append(text[i:j].strip())
                i = j
            while i < len(text) and text[i].isspace():
                i += 1
            if i < len(text) and text[i] == "#":
                i += 1
                continue
            break
        fields[key] = " ".join(part for part in parts if part)
        while i < len(text) and text[i] != ",":
            i += 1
        if i < len(text):
            i += 1
    return fields


def _entry_from_fields(key: str, kind: str, fields: Dict[str, str]) -> Dict[str, Any]:
    def val(name: str) -> Optional[str]:
        raw = fields.get(name, "")
        cleaned = clean_latex(raw)
        return cleaned or None

    authors_raw = fields.get("author", "")
    authors = [
        clean_latex(a)
        for a in re.split(r"\s+and\s+", authors_raw, flags=re.I)
        if clean_latex(a)
    ]
    year_match = re.search(
        r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", fields.get("year", "")
    )
    return {
        "citation_key": key.strip(),
        "entry_type": kind,
        "title": val("title"),
        "authors": authors,
        "year": int(year_match.group(1)) if year_match else None,
        "doi": val("doi"),
        "journal": val("journal") or val("booktitle"),
        "url": val("url") or val("howpublished"),
    }


def parse_bibitems(text: str) -> List[Dict[str, Any]]:
    matches = list(BIBITEM_RE.finditer(text))
    out = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = clean_latex(text[match.end() : end])
        if not raw:
            continue
        year_match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", raw)
        doi_match = re.search(r"\b10\.\d{4,9}/[^\s,;]+", raw, re.I)
        url_match = re.search(r"https?://\S+", raw)
        out.append(
            {
                "citation_key": match.group(1).strip(),
                "entry_type": "misc",
                "title": raw[:2000],
                "authors": [],
                "year": int(year_match.group(1)) if year_match else None,
                "doi": doi_match.group(0).rstrip(".)],") if doi_match else None,
                "journal": None,
                "url": url_match.group(0).rstrip(".)],") if url_match else None,
            }
        )
    return out


def entry_signatures(entry: Dict[str, Any]) -> List[str]:
    """Return all content-based deduplication signatures (DOI, normalized title, key) for an entry."""
    sigs: List[str] = []
    doi = re.sub(r"[^a-z0-9]", "", (entry.get("doi") or "").lower())
    if doi:
        sigs.append("doi:" + doi)
    title = re.sub(r"\W+", " ", (entry.get("title") or "").lower()).strip()
    if title and len(title) >= 10:
        sigs.append("title:" + title)
    if not sigs:
        key = re.sub(r"\s+", "", (entry.get("citation_key") or "").lower())
        if key:
            sigs.append("key:" + key)
    return sigs


def entry_signature(entry: Dict[str, Any]) -> str:
    """Compute primary content-based deduplication signature across citation keys and versions."""
    sigs = entry_signatures(entry)
    return sigs[0] if sigs else ""


def merge_entry(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Unionize fields from duplicate BibTeX entries across versions."""
    if len(source.get("title") or "") > len(target.get("title") or ""):
        target["title"] = source["title"]
    if target.get("entry_type") in (None, "", "misc") and source.get(
        "entry_type"
    ) not in (None, "", "misc"):
        target["entry_type"] = source["entry_type"]
    for field in ("year", "doi", "journal", "url"):
        if not target.get(field) and source.get(field):
            target[field] = source[field]
    authors = target.setdefault("authors", [])
    for author in source.get("authors") or []:
        if author not in authors:
            authors.append(author)


def deduplicate_paper_bibtex(
    arxiv_id: str, raw_entries: Iterable[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Deduplicate BibTeX entries by citation_key AND by content signatures (DOI / title), returning (entries, key_alias_map)."""
    by_key: Dict[str, Dict[str, Any]] = {}
    sig_to_canonical_key: Dict[str, str] = {}
    key_alias_map: Dict[str, str] = {}

    for entry in raw_entries:
        raw_key = entry.get("citation_key") or ""
        citation_key = re.sub(r"\s+", "", raw_key)
        if not citation_key:
            continue
        normalized = {"arxiv_id": arxiv_id, **entry, "citation_key": citation_key}
        sigs = entry_signatures(normalized)

        canonical_key: Optional[str] = None
        if citation_key in by_key:
            canonical_key = citation_key
        elif citation_key in key_alias_map:
            canonical_key = key_alias_map[citation_key]
        else:
            for s in sigs:
                if s in sig_to_canonical_key:
                    canonical_key = sig_to_canonical_key[s]
                    break

        if canonical_key is None:
            canonical_key = citation_key
            by_key[canonical_key] = normalized
            for s in sigs:
                sig_to_canonical_key.setdefault(s, canonical_key)
            continue

        if citation_key != canonical_key:
            key_alias_map[citation_key] = canonical_key

        target = by_key[canonical_key]
        merge_entry(target, normalized)
        for s in entry_signatures(target):
            sig_to_canonical_key.setdefault(s, canonical_key)

    return list(by_key.values()), key_alias_map


def process_version(
    arxiv_id: str, version: int, source_dir: Path
) -> Tuple[
    List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]
]:
    tex_files = (
        sorted(p for p in source_dir.rglob("*.tex") if p.is_file())
        if source_dir.exists()
        else []
    )
    bib_files = (
        sorted(p for p in source_dir.rglob("*.bib") if p.is_file())
        if source_dir.exists()
        else []
    )
    root = choose_root(tex_files)
    if root is None:
        return (
            [],
            [],
            {
                "arxiv_id": arxiv_id,
                "version": version,
                "status": "empty_or_no_root",
                "tex_files": len(tex_files),
                "bib_files": len(bib_files),
            },
            [],
        )
    expanded = expand_includes(root, source_dir)
    bib_entries = []
    for path in bib_files:
        try:
            bib_entries.extend(parse_bibtex_file(path))
        except OSError:
            pass
    bib_entries.extend(parse_bibitems(expanded))
    # Keep bibliography source separately; do not include the references section in the hierarchy.
    nodes = VersionParser().parse(expanded)
    return (
        nodes,
        [],
        {
            "arxiv_id": arxiv_id,
            "version": version,
            "status": "parsed",
            "tex_files": len(tex_files),
            "bib_files": len(bib_files),
            "root": root.name,
            "elements": len(nodes),
        },
        bib_entries,
    )


def update_dataset_card(readme_path: Path, tables: Sequence[str]) -> None:
    if not readme_path.exists():
        return
    text = readme_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return
    end = text.find("\n---", 3)
    if end < 0:
        return
    metadata = yaml.safe_load(text[3:end]) or {}
    configs = metadata.setdefault("configs", [])
    existing = {
        item.get("config_name") for item in configs if isinstance(item, dict)
    }
    for table in tables:
        if table not in existing:
            configs.append(
                {
                    "config_name": table,
                    "data_files": [
                        {"split": "train", "path": f"data/{table}/*.parquet"}
                    ],
                }
            )
    dumped = yaml.safe_dump(
        metadata, sort_keys=False, allow_unicode=True
    ).rstrip()
    readme_path.write_text("---\n" + dumped + text[end:], encoding="utf-8")


def write_table(
    table: str, rows: List[Dict[str, Any]], out_root: Path, shard_rows: int
) -> List[str]:
    schemas = {
        "elements": pa.schema(
            [
                ("element_id", pa.string()),
                ("arxiv_id", pa.string()),
                ("kind", pa.string()),
                ("content", pa.string()),
            ]
        ),
        "hierarchy": pa.schema(
            [
                ("arxiv_id", pa.string()),
                ("version", pa.int32()),
                ("element_id", pa.string()),
                ("parent_id", pa.string()),
            ]
        ),
        "bibtex": pa.schema(
            [
                ("arxiv_id", pa.string()),
                ("citation_key", pa.string()),
                ("entry_type", pa.string()),
                ("title", pa.string()),
                ("authors", pa.list_(pa.string())),
                ("year", pa.int32()),
                ("doi", pa.string()),
                ("journal", pa.string()),
                ("url", pa.string()),
            ]
        ),
    }
    folder = out_root / "data" / table
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("train-*-of-*.parquet"):
        old.unlink()
    total = max(1, (len(rows) + shard_rows - 1) // shard_rows)
    paths = []
    for index in range(total):
        part = rows[index * shard_rows : (index + 1) * shard_rows]
        table_data = pa.Table.from_pylist(part, schema=schemas[table])
        path = folder / f"train-{index:05d}-of-{total:05d}.parquet"
        pq.write_table(table_data, path, compression="zstd")
        paths.append(str(path))
    return paths


def process_paper(
    args: Tuple[Dict[str, Any], Path]
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    paper, corpus = args
    arxiv_id = paper["arxiv_id"]
    safe_id = safe_paper_dirname(arxiv_id)
    versions = sorted(
        (int(v.get("version", 0)), v) for v in paper.get("versions", [])
    )
    version_nodes_list: List[Tuple[int, List[Dict[str, Any]]]] = []
    coverage: List[Dict[str, Any]] = []
    raw_bib_entries: List[Dict[str, Any]] = []

    for version, _ in versions:
        source_dir = corpus / safe_id / f"v{version}" / "source"
        nodes, _, report, entries = process_version(
            arxiv_id, version, source_dir
        )
        coverage.append(report)
        version_nodes_list.append((version, nodes))
        raw_bib_entries.extend(entries)

    # 1. Deduplicate BibTeX entries across all versions (by key & content) and build \cite rename map
    unified_bibtex, key_alias_map = deduplicate_paper_bibtex(
        arxiv_id, raw_bib_entries
    )

    # 2. Rename duplicate citation keys in parsed elements and deduplicate elements across versions
    element_rows: List[Dict[str, Any]] = []
    hierarchy_rows: List[Dict[str, Any]] = []
    element_lookup: Dict[Tuple[Any, ...], str] = {}
    next_element = 1

    for version, nodes in version_nodes_list:
        local_ids: Dict[int, str] = {}
        occurrences: Counter = Counter()
        for node_index, node in enumerate(nodes):
            kind = node["kind"]
            content = rename_citations_in_cleaned_content(
                node["content"], key_alias_map
            )
            occurrence_key = (kind, content)
            occurrences[occurrence_key] += 1
            key = (
                (version, kind, content)
                if kind == "document"
                else (kind, content, occurrences[occurrence_key])
            )
            if key not in element_lookup:
                element_lookup[key] = f"{arxiv_id}#e{next_element:06d}"
                element_rows.append(
                    {
                        "element_id": element_lookup[key],
                        "arxiv_id": arxiv_id,
                        "kind": kind,
                        "content": content,
                    }
                )
                next_element += 1
            local_ids[node_index] = element_lookup[key]
            parent_index = node.get("parent")
            if parent_index is not None and parent_index in local_ids:
                hierarchy_rows.append(
                    {
                        "arxiv_id": arxiv_id,
                        "version": version,
                        "element_id": local_ids[node_index],
                        "parent_id": local_ids[parent_index],
                    }
                )

    return element_rows, hierarchy_rows, unified_bibtex, coverage


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse local arXiv TeX corpus into Milestone 2 tables."
    )
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--shard-rows", type=int, default=50000)
    args = parser.parse_args(argv)
    discovery_path = args.output / "state" / "discovery.json"
    if not discovery_path.exists():
        parser.error(f"Missing {discovery_path}")
    discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
    papers = [
        p for p in discovery.get("papers", []) if p.get("status") == "resolved"
    ]
    corpus = args.output / "corpus"
    export = args.output / "export"
    parquet = export / "parquet"
    parsed_dir = args.output / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

    schemas = {
        "elements": pa.schema(
            [
                ("element_id", pa.string()),
                ("arxiv_id", pa.string()),
                ("kind", pa.string()),
                ("content", pa.string()),
            ]
        ),
        "hierarchy": pa.schema(
            [
                ("arxiv_id", pa.string()),
                ("version", pa.int32()),
                ("element_id", pa.string()),
                ("parent_id", pa.string()),
            ]
        ),
        "bibtex": pa.schema(
            [
                ("arxiv_id", pa.string()),
                ("citation_key", pa.string()),
                ("entry_type", pa.string()),
                ("title", pa.string()),
                ("authors", pa.list_(pa.string())),
                ("year", pa.int32()),
                ("doi", pa.string()),
                ("journal", pa.string()),
                ("url", pa.string()),
            ]
        ),
    }
    export.mkdir(parents=True, exist_ok=True)
    started_mono = time.monotonic()
    with ResourceMonitor(args.output, 0.5) as monitor:
        handles = {
            name: (export / f"{name}.jsonl").open("w", encoding="utf-8")
            for name in schemas
        }
        coverage: List[Dict[str, Any]] = []
        row_counts: Counter = Counter()
        workers = min(4, max(1, (os.cpu_count() or 2) - 1))
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                papers_iter = iter(papers)
                pending = set()
                for _ in range(workers * 2):
                    paper = next(papers_iter, None)
                    if paper is not None:
                        pending.add(pool.submit(process_paper, (paper, corpus)))
                completed = 0
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        (
                            paper_elements,
                            paper_hierarchy,
                            paper_bibtex,
                            paper_coverage,
                        ) = future.result()
                        for name, rows in (
                            ("elements", paper_elements),
                            ("hierarchy", paper_hierarchy),
                            ("bibtex", paper_bibtex),
                        ):
                            handle = handles[name]
                            for row in rows:
                                handle.write(
                                    json.dumps(row, ensure_ascii=False) + "\n"
                                )
                            row_counts[name] += len(rows)
                        coverage.extend(paper_coverage)
                        completed += 1
                        next_paper = next(papers_iter, None)
                        if next_paper is not None:
                            pending.add(
                                pool.submit(process_paper, (next_paper, corpus))
                            )
                        del (
                            paper_elements,
                            paper_hierarchy,
                            paper_bibtex,
                            paper_coverage,
                        )
                    if completed % 100 < len(done):
                        print(
                            f"Parsed {completed}/{len(papers)} papers; elements={row_counts['elements']}, bibtex={row_counts['bibtex']}",
                            flush=True,
                        )
                    done.clear()
        finally:
            for handle in handles.values():
                handle.close()

        coverage_path = parsed_dir / "version_coverage.jsonl"
        with coverage_path.open("w", encoding="utf-8") as handle:
            for row in coverage:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        parquet_paths_by_table = {}
        for name, schema in schemas.items():
            folder = parquet / "data" / name
            folder.mkdir(parents=True, exist_ok=True)
            for old_file in folder.glob("train-*-of-*.parquet"):
                old_file.unlink()
            jsonl_path = export / f"{name}.jsonl"
            rows_per_shard = args.shard_rows
            total = max(1, (row_counts[name] + rows_per_shard - 1) // rows_per_shard)
            batch = []
            shard_index = 0
            with jsonl_path.open("r", encoding="utf-8") as source:
                for line in source:
                    batch.append(json.loads(line))
                    if len(batch) == rows_per_shard:
                        shard_index += 1
                        table = pa.Table.from_pylist(batch, schema=schema)
                        pq.write_table(
                            table,
                            folder
                            / f"train-{shard_index-1:05d}-of-{total:05d}.parquet",
                            compression="zstd",
                        )
                        batch.clear()
                if batch or shard_index == 0:
                    shard_index += 1
                    table = pa.Table.from_pylist(batch, schema=schema)
                    pq.write_table(
                        table,
                        folder
                        / f"train-{shard_index-1:05d}-of-{total:05d}.parquet",
                        compression="zstd",
                    )
            parquet_paths_by_table[name] = total
            print(
                f"{name}: {row_counts[name]} rows, {total} shards", flush=True
            )

    elapsed = round(time.monotonic() - started_mono, 4)
    res_dict = monitor.snapshot.to_dict() if monitor.snapshot else {}
    summary = {
        "resolved_papers": len(papers),
        "versions_seen": len(coverage),
        "versions_parsed": sum(r["status"] == "parsed" for r in coverage),
        "versions_empty_or_no_root": sum(
            r["status"] != "parsed" for r in coverage
        ),
        "elements_rows": row_counts["elements"],
        "hierarchy_rows": row_counts["hierarchy"],
        "bibtex_rows": row_counts["bibtex"],
        "elapsed_seconds": elapsed,
        "avg_seconds_per_paper": round(elapsed / max(1, len(papers)), 4),
        "resources": res_dict,
    }
    (parsed_dir / "milestone2_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    update_dataset_card(
        parquet / "README.md", ["elements", "hierarchy", "bibtex"]
    )
    print("Summary:", json.dumps(summary, ensure_ascii=False))
    print(f"Coverage report: {coverage_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
