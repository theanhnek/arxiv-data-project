"""Upload the exported Parquet tables as a public dataset on Hugging Face.

Each group member uploads only their own range to a dataset under their personal
account::

    python -m src.hf_upload --repo-id <your-username>/arxiv-g14-references

The access token (a Hugging Face token with write permission) is read from the
``HF_TOKEN`` environment variable, or from ``.env``, or from a prior
``huggingface-cli login``. It is never printed or written to disk by this module.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional


def upload_dataset(
    repo_id: str,
    folder: Path,
    *,
    private: bool = False,
    token: Optional[str] = None,
) -> str:
    """Create the dataset repository if needed, upload ``folder`` and return its URL."""
    folder = Path(folder)
    if not folder.is_dir() or not any(folder.rglob("*.parquet")):
        raise FileNotFoundError(
            f"No Parquet files under {folder}. Run: python -m src.main --export-parquet --output data"
        )
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise RuntimeError(
            "Uploading needs huggingface_hub: pip install huggingface_hub (see src/requirements.txt)"
        ) from exc

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
    api.upload_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(folder),
        commit_message="Upload papers and references tables",
    )
    return f"https://huggingface.co/datasets/{repo_id}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-id", required=True, help="Dataset id, e.g. your-username/arxiv-g14-references"
    )
    parser.add_argument(
        "--folder",
        type=Path,
        default=None,
        help="Folder holding the Parquet tables (default: <output>/export/parquet)",
    )
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument(
        "--private",
        action="store_true",
        help="Create a private dataset (the course requires a PUBLIC link; use only for a dry run)",
    )
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    token = (os.getenv("HF_TOKEN") or "").strip() or None
    folder = args.folder or args.output / "export" / "parquet"
    try:
        url = upload_dataset(args.repo_id, folder, private=args.private, token=token)
    except (FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"Uploaded. Public dataset link to submit on Moodle: {url}")


if __name__ == "__main__":
    main()
