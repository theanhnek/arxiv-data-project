---
configs:
  - config_name: papers
    data_files:
      - split: train
        path: data/papers/*.parquet
  - config_name: references
    data_files:
      - split: train
        path: data/references/*.parquet
  - config_name: elements
    data_files:
      - split: train
        path: data/elements/*.parquet
  - config_name: hierarchy
    data_files:
      - split: train
        path: data/hierarchy/*.parquet
  - config_name: bibtex
    data_files:
      - split: train
        path: data/bibtex/*.parquet
  - config_name: predictions
    data_files:
      - split: train
        path: data/predictions/*.parquet
pretty_name: "Group 14 arXiv Dataset — Member {{MEMBER_NAME}}"
# Add a license only after the group confirms the applicable license.
---

# Group 14 arXiv Dataset — {{MEMBER_NAME}}

> **Before uploading:** replace every `{{...}}` placeholder, verify the six table folders and schemas, and set `license` only after the group confirms the applicable license. This is a template; it does not represent measured results or certify that all tables have been exported.

## Dataset summary

This dataset contains the Group 14 member-specific portion of the arXiv project. It combines paper metadata, arXiv-keyed references, parsed and deduplicated document elements, per-version hierarchy edges, unified BibTeX entries, and reference-matching predictions.

- **Maintainer:** {{HF_USERNAME}}
- **Member:** {{MEMBER_NAME}}
- **Assigned arXiv range:** {{ASSIGNED_RANGE}}
- **Data collection / processing date:** {{YYYY-MM-DD}}
- **Code repository and commit:** {{CODE_REPOSITORY}} at `{{COMMIT_SHA}}`
- **Dataset version:** {{DATASET_VERSION}}

The data covers only the maintainer's assigned range. Each member publishes a separate public dataset as directed by the group submission plan.

## Files and loading

Each table is a separate Hugging Face configuration. Parquet files are sharded under `data/<table>/`; the Hub can read them through the configurations above.

```python
from datasets import load_dataset

papers = load_dataset("{{HF_USERNAME}}/{{DATASET_REPO}}", "papers", split="train")
references = load_dataset("{{HF_USERNAME}}/{{DATASET_REPO}}", "references", split="train")
```

Use the configuration name `elements`, `hierarchy`, `bibtex`, or `predictions` to load the corresponding table.

## Tables and schemas

All tables are stored as sharded Parquet. Lists are flat lists of strings; the tables do not use nested objects or JSON blobs.

### `papers`

One row per assigned arXiv publication.

| Column | Type | Description |
|---|---|---|
| `arxiv_id` | string, non-null | Original publication arXiv ID, without a version suffix. |
| `title` | string | Paper title from the discovery metadata. |
| `authors` | list[string] | Authors in source order. |
| `submission_date` | string | Initial submission date in ISO format. |
| `revised_dates` | list[string] | Known revision dates. |
| `venue` | string, nullable | Publication venue when available. |

### `references`

One row per crawled reference with an arXiv ID, paired with its citing publication. References without an arXiv ID are not included in this published table.

| Column | Type | Description |
|---|---|---|
| `arxiv_id` | string, non-null | Citing publication arXiv ID. |
| `ref_arxiv_id` | string, non-null | Cited reference arXiv ID. |
| `ref_title` | string | Reference title when available. |
| `ref_authors` | list[string] | Reference authors when available. |
| `ref_submission_date` | string | Reference publication/submission date when available. |
| `ref_s2_id` | string | Semantic Scholar paper ID when available. |

### `elements`

One row per distinct parsed element across all versions of a publication. Identical cleaned content shared by versions uses one element ID.

| Column | Type | Description |
|---|---|---|
| `element_id` | string, non-null | Unique element identifier within the publication. |
| `arxiv_id` | string, non-null | Publication arXiv ID. |
| `kind` | string (dictionary) | Element type, such as document, heading, sentence, formula, figure, or list component. |
| `content` | string | Cleaned element content. |

### `hierarchy`

One row per node-parent edge for a publication version.

| Column | Type | Description |
|---|---|---|
| `arxiv_id` | string, non-null | Publication arXiv ID. |
| `version` | int, non-null | arXiv version number. |
| `element_id` | string, non-null | Child/node element ID. |
| `parent_id` | string, non-null | Parent element ID. |

### `bibtex`

One row per unified BibTeX entry after deduplication across versions.

| Column | Type | Description |
|---|---|---|
| `arxiv_id` | string, non-null | Publication arXiv ID. |
| `citation_key` | string, non-null | Chosen citation key for the unified entry. |
| `entry_type` | string (dictionary) | BibTeX entry type. |
| `title` | string | Entry title when available. |
| `authors` | list[string] | Entry authors when available. |
| `year` | int, nullable | Publication year when available. |
| `doi` | string | DOI when available. |
| `journal` | string | Journal or venue when available. |
| `url` | string | URL when available. |

### `predictions`

One row per BibTeX entry in publications selected for the reference-matching task. Publications outside the train, validation, and test pools are not represented here.

| Column | Type | Description |
|---|---|---|
| `arxiv_id` | string, non-null | Publication containing the entry. |
| `partition` | string (dictionary), non-null | `train`, `valid`, or `test`; consistent across a publication. |
| `citation_key` | string, non-null | BibTeX citation key. |
| `groundtruth_arxiv_id` | string, nullable | Labeled correct reference arXiv ID, or null when none is known. |
| `cand_1`–`cand_5` | string, nullable | Ranked top-five candidate reference arXiv IDs; `cand_1` ranks highest. |

## Data collection and processing

Paper metadata and version lists are discovered through the arXiv API. Versioned source files are downloaded and only regular `.tex` and `.bib` files are retained from source archives. Reference metadata is collected through Semantic Scholar. Milestone 2 parses and standardizes the LaTeX content and BibTeX entries; Milestone 3 produces matching labels, partitions, and ranked candidates.

Collection and processing settings used for this member's run:

- **arXiv ID range:** {{ASSIGNED_RANGE}}
- **arXiv request interval:** {{ARXIV_DELAY_SECONDS}} seconds (must follow the group's configured pacing)
- **Semantic Scholar access mode:** {{AUTHENTICATED_OR_UNAUTHENTICATED}}
- **Parquet shard row limit:** {{SHARD_ROWS}}
- **Pipeline code commit:** `{{COMMIT_SHA}}`

## Dataset counts

Fill these values from the actual Parquet export after validation; do not estimate them.

| Table | Rows | Shards |
|---|---:|---:|
| `papers` | {{PAPERS_ROWS}} | {{PAPERS_SHARDS}} |
| `references` | {{REFERENCES_ROWS}} | {{REFERENCES_SHARDS}} |
| `elements` | {{ELEMENTS_ROWS}} | {{ELEMENTS_SHARDS}} |
| `hierarchy` | {{HIERARCHY_ROWS}} | {{HIERARCHY_SHARDS}} |
| `bibtex` | {{BIBTEX_ROWS}} | {{BIBTEX_SHARDS}} |
| `predictions` | {{PREDICTIONS_ROWS}} | {{PREDICTIONS_SHARDS}} |

## Intended use and limitations

This dataset is intended for coursework and research on arXiv metadata, document structure, bibliography normalization, and reference matching. Metadata coverage depends on the upstream APIs. Semantic Scholar may omit references or fields, especially for recent or less-indexed papers. arXiv sources can lack TeX or BibTeX files; these cases should remain represented in the local processing records even when they produce no source-file rows. Matching ground truth depends on the documented manual and automatic labeling procedures. Predictions are model outputs and are not guaranteed to be correct.

The published tables contain the assigned member's range only. Verify that no out-of-range publication IDs are present before publishing.

## Data provenance and citations

- arXiv API and source repository: https://info.arxiv.org/help/api/index.html
- Semantic Scholar Academic Graph API: https://api.semanticscholar.org/api-docs/
- Course project statement: https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/

## Contact

For questions about this member's dataset, contact {{CONTACT_OR_GROUP_CONTACT}}.
