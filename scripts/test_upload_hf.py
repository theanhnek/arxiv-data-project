"""Script test upload a small dataset to Hugging Face for Member 2."""

import os
import sys
from huggingface_hub import HfApi, create_repo

def main():
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("[ERROR] Chua thiet lap bien moi truong HF_TOKEN!")
        print("Vui long chay lenh sau trong PowerShell truoc:")
        print('  $env:HF_TOKEN="hf_xxxxxxxxxxxxxxxxxxxx"')
        sys.exit(1)

    print("1. Dang kiem tra xac thuc voi Hugging Face...")
    try:
        api = HfApi(token=token)
        user_info = api.whoami()
        username = user_info["name"]
        print(f"   -> Dang nhap thanh cong voi tai khoan: {username}")
    except Exception as e:
        print(f"[ERROR] Token khong hop le hoac loi mang: {e}")
        sys.exit(1)

    repo_id = f"{username}/test-upload-dataset"
    print(f"\n2. Dang tao repo test tren Hugging Face: {repo_id}...")
    try:
        create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True, token=token, private=False)
        print(f"   -> Repo san sang o che do PUBLIC.")
    except Exception as e:
        print(f"[ERROR] Khong the tao repo: {e}")
        sys.exit(1)

    # Tao file du lieu mau nho
    sample_file = "sample_test.jsonl"
    print(f"\n3. Dang tao file du lieu mau: {sample_file}...")
    sample_data = (
        '{"arxiv_id": "2410.09401", "title": "Test Paper 1", "authors": ["Author A"]}\n'
        '{"arxiv_id": "2410.09402", "title": "Test Paper 2", "authors": ["Author B"]}\n'
    )
    with open(sample_file, "w", encoding="utf-8") as f:
        f.write(sample_data)
    print("   -> Da tao file mau gom 2 dong du lieu.")

    print(f"\n4. Dang upload file len Hugging Face repo {repo_id}...")
    try:
        api.upload_file(
            path_or_fileobj=sample_file,
            path_in_repo="data/sample_test.jsonl",
            repo_id=repo_id,
            repo_type="dataset",
            token=token,
            commit_message="Add sample test dataset for member 2 verification"
        )
        print("\n=======================================================")
        print(" CHUC MUNG! TEST UPLOAD LEN HUGGING FACE THANH CONG!")
        print(f" Link dataset: https://huggingface.co/datasets/{repo_id}")
        print("=======================================================")
    except Exception as e:
        print(f"[ERROR] Upload that bai: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(sample_file):
            os.remove(sample_file)

if __name__ == "__main__":
    main()
