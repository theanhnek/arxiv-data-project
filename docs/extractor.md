# Bàn giao phần thành viên 3 — Extraction & Kiểm tra Corpus

## 1. Vị trí trong Pipeline (Discovery $\rightarrow$ Download $\rightarrow$ Extract)

Theo kiến trúc 3 bước của đồ án:
1. **Bước 1 — Discovery (Thành viên 1)**: Đọc danh sách ID / dải phân công, gọi arXiv API để xác định metadata và các phiên bản (`v1..vN`), lưu `data/state/discovery.json`.
2. **Bước 2 — Download (Thành viên 2)**: Tải các gói source từ mirror `https://export.arxiv.org/src/{version_id}`, kiểm tra toàn vẹn và lưu `data/state/downloads.json`.
3. **Bước 3 — Extraction (Thành viên 3)**: Đọc từ `data/state/downloads.json`, giải nén an toàn và **chỉ giữ lại các file `.tex` và `.bib`**, loại bỏ hoàn toàn các file ảnh (`.png`, `.jpg`, `.pdf`, `.eps`) và file binary. Lưu kết quả vào `data/corpus/<paper_id>/<vN>/source/` và ghi báo cáo vào `data/state/extractions.json`.

---

## 2. Hướng dẫn chạy trên PowerShell

Chạy các lệnh PowerShell dưới đây từ thư mục gốc của repository (không chạy từ bên trong `src/`):

### 2.1 Chạy riêng Bước 3 (sau khi đã chạy xong Download)

Khi đã có `data/state/downloads.json` từ Bước 2:

```powershell
.\.venv\Scripts\python.exe -m src.main --extract --output data --resume
```

Hoặc gọi trực tiếp module `src.extractor`:

```powershell
.\.venv\Scripts\python.exe -m src.extractor --output data --resume
```

### 2.2 Chạy gộp Bước 2 (Download) và Bước 3 (Extract)

```powershell
.\.venv\Scripts\python.exe -m src.main --download --extract --output data --resume
```

### 2.3 Chạy trọn gói cả 3 bước (Discovery $\rightarrow$ Download $\rightarrow$ Extract)

Chạy theo khoảng phân công của Thành viên 3 (`2025-10 4401` đến `2025-10 9400`):

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --download --extract --range 2025-10 4401 2025-10 9400 --output data --resume
```

Hoặc chạy theo dòng của Thành viên 3 trong file bảng phân công chung (`--member-index 3`):

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --download --extract --ids data/assigned_ids.txt --member-index 3 --output data --resume
```

---

## 3. Phối hợp trong Python

```python
from pathlib import Path
from src.config import CrawlConfig
from src.discovery import ArxivClient, discover_assigned_range
from src.downloader import download_discovered_sources
from src.extractor import extract_downloaded_sources

config = CrawlConfig(output_dir=Path("data"))
client = ArxivClient(config)

try:
    # Bước 1: Discovery
    discovery_report = discover_assigned_range(
        "2025-10", 4401, "2025-10", 9400, config, resume=True, client=client
    )

    # Bước 2: Download
    download_report = download_discovered_sources(config, resume=True, client=client)

    # Bước 3: Extraction
    extraction_report = extract_downloaded_sources(config, resume=True)

    print("Discovery:", discovery_report["summary"])
    print("Download:", download_report["summary"])
    print("Extraction:", extraction_report["summary"])
finally:
    client.close()
```

---

## 4. Chi tiết module `src/extractor.py`

- `extract_source(source_path, destination) -> ExtractionResult`:
  - Giải nén an toàn gói nguồn (file `.tar.gz`, `.tar`, hoặc file `.tex` đơn lẻ) vào thư mục `destination` (thường là `source/` của version).
  - Sử dụng thư mục tạm (`tempfile.TemporaryDirectory` có tiền tố `.extract-`) bên trong thư mục đích để chuẩn bị file trước khi di chuyển nguyên tử (`os.replace`).
  - Bảo toàn từng byte của file gốc (không chỉnh sửa nội dung, encoding hoặc các lệnh LaTeX `\includegraphics`, `figure`).
  - Bảo toàn cấu trúc thư mục con tương đối để các lệnh `\input{...}` và `\include{...}` hoạt động bình thường.
- `extract_downloaded_sources(config, *, downloads_path=None, resume=False) -> dict`:
  - Đọc `data/state/downloads.json`, duyệt qua tất cả các phiên bản đã tải về.
  - Gọi `extract_source` cho từng phiên bản, ghi snapshot từng phiên bản vào `extraction_state.json` và tổng hợp vào `data/state/extractions.json`.
  - Hỗ trợ cơ chế `--resume`: tự động tái sử dụng các phiên bản đã giải nén thành công trước đó nếu thư mục `source/` vẫn tồn tại nguyên vẹn.
- `detect_source_format(source_path) -> str`:
  - Phát hiện định dạng thực tế bằng magic bytes: `tar_archive`, `plain_source`, `corrupt_archive`, `unsupported_format`, `source_missing`, `source_unreadable`.

### Quy tắc an toàn & bảo mật
1. **Chống Path Traversal**: Từ chối các đường dẫn chứa `..`, đường dẫn tuyệt đối (`/abs` hoặc `C:\`), ký tự NUL (`\0`), hoặc backslash Windows traversal.
2. **Chống Symlink/Hardlink Attack**: Từ chối archive chứa symlink (`SYMTYPE`) hoặc hardlink (`LNKTYPE`).
3. **Chống Ghi đè ngoài thư mục đích**: Mọi đường dẫn trích xuất đều được kiểm tra nghiêm ngặt bằng hàm `_ensure_within` với `path.resolve().relative_to(root.resolve())`.

---

## 5. Schema đầu ra (`data/state/extractions.json`)

Snapshot toàn cục được lưu tại `data/state/extractions.json` với cấu trúc sau:

```json
{
  "schema_version": 1,
  "started_at": "2026-11-02T14:12:00.000000+00:00",
  "downloads_path": "data/state/downloads.json",
  "versions": [
    {
      "version_id": "2510.04401v1",
      "arxiv_id": "2510.04401",
      "version": 1,
      "source_path": "data/corpus/2510.04401/v1/v1.tar.gz",
      "destination": "data/corpus/2510.04401/v1/source",
      "status": "success",
      "input_bytes": 105240,
      "files": [
        "paper/main.tex",
        "paper/refs.bib"
      ],
      "retained_bytes": 45120,
      "archive_uncompressed_bytes": 850200,
      "error": null,
      "cache_hit": false,
      "extracted_at": "2026-11-02T14:12:05.000000+00:00"
    }
  ],
  "summary": {
    "expected_versions": 5000,
    "success": 4950,
    "no_tex_source": 30,
    "skipped_not_downloaded": 20,
    "failed": 0,
    "cache_hits": 0,
    "total_input_bytes": 526200000,
    "total_retained_bytes": 225600000,
    "total_archive_uncompressed_bytes": 4251000000
  },
  "elapsed_seconds": 35.42
}
```

### Các trạng thái của phiên bản (`status`)
- `success`: Giải nén thành công và có ít nhất 1 file `.tex`.
- `no_tex_source`: Gói tải về hợp lệ nhưng không chứa file `.tex` nào (ví dụ bài báo chỉ nộp file PDF/HTML, hoặc archive chỉ có `.bib` hoặc toàn ảnh). Record vẫn được giữ với thư mục `source/` rỗng theo đúng yêu cầu đề bài.
- `skipped_not_downloaded`: Phiên bản không được tải về ở Bước 2 (do 404 Not Found hoặc lỗi kết nối).
- `corrupt_archive`: Gói nén có signature hợp lệ nhưng bị hỏng hoặc dở dang, không thể đọc bằng `tarfile`.
- `unsupported_format`: Gói tải về không phải định dạng archive hay file TeX/Bib hợp lệ.
- `unsafe_archive`: Archive chứa member không an toàn (path traversal, symlink).

---

## 6. Đo lường kích thước & Quản lý Disk

Theo đúng yêu cầu của đề bài và kế hoạch triển khai, module trích xuất ghi nhận **3 chỉ số dung lượng byte độc lập**:

1. **`input_bytes`**: Kích thước gói tải về (đã nén, ví dụ `v1.tar.gz`).
2. **`retained_bytes`**: Tổng số byte của các file `.tex` và `.bib` thực sự được giữ lại trong thư mục `source/`.
3. **`archive_uncompressed_bytes`**: Tổng kích thước uncompressed của toàn bộ các file thông thường trong archive trước khi lọc bỏ hình ảnh.

> [!NOTE]
> So sánh giữa `archive_uncompressed_bytes` và `retained_bytes` cho thấy dung lượng thực tế được tiết kiệm sau khi loại bỏ hình ảnh và file binary khỏi corpus.
