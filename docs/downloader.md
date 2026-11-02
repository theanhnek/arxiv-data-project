# Bàn giao phần thành viên 2 — Downloader & HTTP

## 1. Bảng phân công khoảng ID của nhóm (`start_month - start_id - end_month - end_id`)

Quy tắc ánh xạ chuẩn của arXiv cho các bài báo từ năm 2015 trở đi (`YYMM.NNNNN`):
- `YYYY-MM` $\rightarrow$ `YYMM` (2 chữ số cuối của năm + 2 chữ số tháng, ví dụ `2025-10` $\rightarrow$ `2510`, `2025-09` $\rightarrow$ `2509`).
- `id` $\rightarrow$ đệm số `0` ở đầu cho đủ 5 chữ số (`NNNNN`, ví dụ `9401` $\rightarrow$ `09401`, `1` $\rightarrow$ `00001`).

| `--member-index` | Thành viên | `start_month` | `start_id` | `end_month` | `end_id` | Khoảng arXiv ID đưa vào Discovery (`YYMM.NNNNN`) |
| :---: | :--- | :--- | ---: | :--- | ---: | :--- |
| `1` | **Thành viên 1** | `2025-10` | `14401` | `2025-10` | `19400` | `2510.14401` $\rightarrow$ `2510.19400` (5.000 ID) |
| `2` | **Thành viên 2** | `2025-10` | `9401` | `2025-10` | `14400` | `2510.09401` $\rightarrow$ `2510.14400` (5.000 ID) |
| `3` | **Thành viên 3** | `2025-10` | `4401` | `2025-10` | `9400` | `2510.04401` $\rightarrow$ `2510.09400` (5.000 ID) |
| `4` | **Thành viên 4** | `2025-09` | `26047` | `2025-10` | `4400` | `2509.26047` $\rightarrow$ `2509.26646` (600 ID) + `2510.00001` $\rightarrow$ `2510.04400` (4.400 ID) |
| `5` | **Thành viên 5** | `2025-09` | `11047` | `2025-09` | `16046` | `2509.11047` $\rightarrow$ `2509.16046` (5.000 ID) |

---

## 2. Quy trình bắt buộc: Đưa IDs lên Discovery để Identify trước $\rightarrow$ Download sau

Theo đúng kiến trúc pipeline và `docs/discovery.md`, quá trình luôn đi qua **2 bước tách biệt**:
1. **Bước 1 — Discovery (Identify papers & versions)**: Mở rộng khoảng phân công thành danh sách `data/assigned_ids.txt`, đưa toàn bộ ID qua module `src/discovery.py` (`discover_papers` / `discover_assigned_range`) để gọi arXiv API (`https://export.arxiv.org/api/query`), xác định những ID tồn tại, lấy metadata và liệt kê đủ các phiên bản `v1..vN` vào `data/state/discovery.json`.
2. **Bước 2 — Download (Fetch versioned sources)**: Module `src/downloader.py` đọc danh sách các bài có `status == "resolved"` và từng `version_id` (`v1..vN`) đã được identify trong `data/state/discovery.json` để tải gói source từ `https://export.arxiv.org/src/{version_id}` và ghi kết quả vào `data/state/downloads.json`.

### 2.1 Hướng dẫn chạy từng bước trên PowerShell (Khuyên dùng)

Chạy từ thư mục gốc repository:

```powershell
# Bước 0: Khởi tạo cấu trúc thư mục data/
.\.venv\Scripts\python.exe -m src.main --init --output data

# Bước 1: Đưa khoảng phân công của Thành viên 2 lên Discovery trước để identify vào data/state/discovery.json
.\.venv\Scripts\python.exe -m src.main --discover --range 2025-10 9401 2025-10 14400 --output data --resume

# Bước 2: Sau khi đã có data/state/discovery.json, chạy Downloader để tải các version đã identify
.\.venv\Scripts\python.exe -m src.main --download --output data --resume
# (Hoặc gọi trực tiếp: .\.venv\Scripts\python.exe -m src.downloader --output data --resume)
```

- **Nếu dùng chung file bảng phân công 5 dòng `data/assigned_ids.txt` của nhóm**:
  ```powershell
  # Bước 1: Discovery theo dòng của thành viên (--member-index 1..5)
  .\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --member-index 2 --output data --resume

  # Bước 2: Download từ data/state/discovery.json
  .\.venv\Scripts\python.exe -m src.downloader --output data --resume
  ```
- **Chạy nối tiếp Bước 1 (`--discover`) rồi Bước 2 (`--download`) trong một lệnh**:
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --discover --download --range 2025-10 9401 2025-10 14400 --output data --resume
  ```

### 2.2 Phối hợp trong Python (Discovery trước $\rightarrow$ Download $\rightarrow$ Extract cho Thành viên 3)

```python
from pathlib import Path
from src.config import CrawlConfig
from src.discovery import ArxivClient, discover_assigned_range
from src.downloader import download_discovered_sources, safe_paper_dirname
from src.extractor import extract_source

config = CrawlConfig(
    output_dir=Path("data"),
    arxiv_delay_seconds=3.0,
    request_timeout_seconds=60.0,
    max_attempts=3,
    user_agent="Lab1ArxivCrawler/0.1 (contact: your-email@example.com)",
)

client = ArxivClient(config)
try:
    # Bước 1: Đưa khoảng phân công lên Discovery trước để identify (ghi ra data/state/discovery.json)
    discovery_report = discover_assigned_range(
        "2025-10", 9401, "2025-10", 14400, config, resume=True, client=client
    )

    # Bước 2: Tải toàn bộ các version đã được Discovery identify (ghi ra data/state/downloads.json)
    download_report = download_discovered_sources(config, resume=True, client=client)

    # Bước 3: Chuyển các gói tải thành công sang Extractor của Thành viên 3
    for v in download_report["versions"]:
        if v["status"] == "downloaded" and v["source_path"]:
            safe_id = safe_paper_dirname(v["arxiv_id"])
            ext = extract_source(
                v["source_path"],
                config.output_dir / "corpus" / safe_id / f"v{v['version']}" / "source",
            )
            print(v["version_id"], v["bytes_downloaded"], ext.status)
finally:
    client.close()
```

### 2.3 Chạy kiểm tra tự động (offline)

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery.py tests/test_downloader.py tests/test_extractor.py -v --basetemp data/test-suite
```

---

## 3. Giao diện đã triển khai (`src/downloader.py`)

- `parse_version_id(version_id) -> (base_id, version_num, canonical_version_id)`: kiểm tra và tách mã phiên bản (`"2510.09401v2"` $\rightarrow$ `("2510.09401", 2, "2510.09401v2")`).
- `safe_paper_dirname(arxiv_id) -> str`: chuyển ID sang tên thư mục an toàn (`"hep-th/9901001"` $\rightarrow$ `"hep-th_9901001"`).
- `build_source_url(version_id) -> str`: tạo URL tải từ mirror chính thức `https://export.arxiv.org/src/{version_id}`.
- `download_source(version_id, config, *, resume=False, client=None, source_url=None) -> DownloadResult`:
  - Tải một version (`{base}v{n}`), stream theo chunk 64 KB xuống file tạm `.part` trong `data/tmp/downloads/{safe_id}/`, tính `SHA-256` trên luồng, kiểm tra định dạng thực tế (`.tar.gz`, `.gz`, `.tar`, `.tex`, `.bib`, `.pdf`) để gắn đuôi mở rộng chuẩn cho `src/extractor.py`, rồi đổi tên nguyên tử sang `v{n}.{ext}`.
  - Lưu trạng thái từng version tại `data/state/downloads/{safe_id}/v{n}.json`.
- `download_discovered_sources(config, *, discovery_path=None, resume=False, client=None) -> dict`:
  - Đọc `data/state/discovery.json` (không sửa snapshot của Thành viên 1), tải tuần tự mọi version của các bài `status == "resolved"`, và ghi báo cáo tổng hợp vào `data/state/downloads.json`.
- `discover_and_download(ids, config, *, resume=False, client=None) -> dict`:
  - Thực thi tuần tự Bước 1 (`discover_papers`) rồi mới sang Bước 2 (`download_discovered_sources`) trên cùng một `ArxivClient`.

---

## 4. Schema đầu ra (`data/state/downloads.json`)

Root gồm:
- `schema_version`: `1`
- `started_at`: thời điểm bắt đầu (UTC ISO-8601)
- `discovery_path`: đường dẫn tới `data/state/discovery.json`
- `versions`: danh sách kết quả từng version (`version_id`, `arxiv_id`, `version`, `source_url`, `status`, `source_path`, `state_path`, `bytes_downloaded`, `sha256`, `content_type`, `detected_format`, `http_status`, `attempts`, `duration_seconds`, `cache_hit`, `downloaded_at`, `error_type`, `error`)
- `elapsed_seconds`: tổng thời gian chạy download
- `summary`: `expected_versions`, `downloaded`, `not_found`, `invalid_response`, `failed`, `cache_hits`, `total_bytes_downloaded`

---

## 5. Phương pháp kỹ thuật và Hạn chế (dành cho Thành viên 5 tổng hợp báo cáo)

1. **Tách bạch Discovery và Download nhưng chia sẻ Pacing**:
   - Toàn bộ việc identify bài báo và liệt kê số version (`v1..vN`) qua Atom API do `src/discovery.py` đảm nhiệm trước.
   - `src/downloader.py` chỉ tải các `version_id` đã được xác nhận trong `data/state/discovery.json` qua mirror `https://export.arxiv.org/src/{version_id}`, đồng thời dùng chung `client.last_request` với `ArxivClient` để đảm bảo khoảng nghỉ $\ge 3$ giây giữa mọi request.
2. **Xử lý mã phản hồi HTTP & Retry có giới hạn**:
   - Không retry với `HTTP 404` (`status="not_found"`).
   - Tự động retry với backoff lũy thừa và tôn trọng header `Retry-After` đối với `HTTP 429`, `HTTP 5xx`, lỗi mạng/timeout, hoặc `HTTP 403` do rate-limit.
3. **Streaming, Chặn trang HTML lỗi & Kiểm tra toàn vẹn SHA-256 khi Resume**:
   - Stream theo từng chunk 64 KB xuống file tạm `.part`, tính `SHA-256` trên luồng, kiểm tra chữ ký file/header để loại bỏ trang HTML báo lỗi hoặc throttling (kể cả khi trả về `HTTP 200`), và chỉ đổi tên nguyên tử sang file đích khi hoàn tất.
   - Khi `resume=True`, kiểm tra đồng thời state JSON, sự tồn tại của file đích, kích thước `bytes_downloaded > 0` và mã băm `SHA-256`.
