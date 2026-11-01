# Bàn giao phần thành viên 1 — Discovery & Metadata

## Hướng dẫn chạy (Discovery trước $\rightarrow$ Download sau)

Chạy các lệnh PowerShell dưới đây từ thư mục gốc repository, không chạy từ bên trong `src/`.

### 1. Chuẩn bị môi trường và thư mục dữ liệu

Nếu chưa cài môi trường:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r src/requirements.txt
Copy-Item .env.example .env
```

Chỉ copy `.env.example` khi chưa có `.env` để tránh ghi đè cấu hình cá nhân. Điền thông tin liên hệ trong `ARXIV_USER_AGENT` của `.env`.

Khởi tạo thư mục dữ liệu cục bộ (`data/corpus`, `data/state`, `data/metrics`, `data/tmp`):

```powershell
.\.venv\Scripts\python.exe -m src.main --init --output data
```

### 2. Chuẩn bị đầu vào (Bảng khoảng phân công hoặc danh sách ID)

`src/discovery.py` hỗ trợ trực tiếp **bảng khoảng phân công** (`start_month - start_id - end_month - end_id`) lẫn **danh sách từng arXiv ID**.

#### Cách A: Lưu bảng phân công 5 dòng của nhóm vào `data/assigned_ids.txt`

```powershell
@'
2025-10	14401	2025-10	19400
2025-10	9401	2025-10	14400
2025-10	4401	2025-10	9400
2025-09	26047	2025-10	4400
2025-09	11047	2025-09	16046
'@ | Set-Content -LiteralPath data/assigned_ids.txt -Encoding utf8
```

| `--member-index` | Thành viên | Khoảng phân công | Khoảng arXiv ID được `discovery.py` mở rộng (`YYMM.NNNNN`) |
| :---: | :--- | :--- | :--- |
| `1` | Thành viên 1 | `2025-10 14401 2025-10 19400` | `2510.14401` $\rightarrow$ `2510.19400` (5.000 ID) |
| `2` | Thành viên 2 | `2025-10 9401 2025-10 14400` | `2510.09401` $\rightarrow$ `2510.14400` (5.000 ID) |
| `3` | Thành viên 3 | `2025-10 4401 2025-10 9400` | `2510.04401` $\rightarrow$ `2510.09400` (5.000 ID) |
| `4` | Thành viên 4 | `2025-09 26047 2025-10 4400` | `2509.26047` $\rightarrow$ `2509.26646` (600 ID) + `2510.00001` $\rightarrow$ `2510.04400` (4.400 ID) |
| `5` | Thành viên 5 | `2025-09 11047 2025-09 16046` | `2509.11047` $\rightarrow$ `2509.16046` (5.000 ID) |

#### Cách B: Tạo mẫu nhanh với vài ID để kiểm thử

```powershell
@'
1706.03762
1810.04805
'@ | Set-Content -LiteralPath data/assigned_ids.txt -Encoding utf8
```

Lệnh tạo mẫu sẽ thay nội dung file nếu đã tồn tại. Hai ID trên chỉ để thử, không phải danh sách phân công chính thức. `data/` không được đưa lên GitHub nên mỗi thành viên cần tự chuẩn bị input và chạy discovery trên máy mình.

### 3. Bước 1 — Chạy Discovery để Identify bài báo và các phiên bản (`v1..vN`)

Tất cả ID trong khoảng phân công **phải đi qua bước Discovery trước** để gọi arXiv API (`https://export.arxiv.org/api/query`), xác nhận bài tồn tại, lấy metadata và liệt kê danh sách phiên bản vào `data/state/discovery.json`:

- **Chạy theo dòng của từng thành viên trong file bảng phân công** (ví dụ `--member-index 2` cho Thành viên 2):
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --member-index 2 --output data --resume
  ```
- **Hoặc truyền trực tiếp khoảng phân công qua `--range`** (tự động ghi `data/assigned_ids.txt` gồm 5.000 ID rồi chạy discovery):
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --discover --range 2025-10 9401 2025-10 14400 --output data --resume
  ```
- **Hoặc chạy trên file danh sách ID đơn lẻ** (không cần `--member-index`):
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --output data --resume
  ```

Kiểm tra `summary`: `resolved` là số bài lấy metadata thành công, `failed` là số bài lỗi, `expected_versions` là tổng số version cần tải. Với `--resume`, `cache_hits` cho biết số bài được dùng lại từ snapshot. Muốn tìm revision mới, chạy lại không có `--resume`.

### 4. Bước 2 — Chạy Downloader từ kết quả `data/state/discovery.json`

Sau khi Discovery đã ghi `data/state/discovery.json`, chạy Downloader để tải các gói source của những bài `status == "resolved"`:

```powershell
.\.venv\Scripts\python.exe -m src.main --download --output data --resume
```

*(Hoặc gọi trực tiếp module của Thành viên 2: `.\.venv\Scripts\python.exe -m src.downloader --output data --resume`, hoặc chạy nối tiếp cả 2 bước trong một lệnh bằng `--discover --download`)*.

Đầu ra chính của Discovery là `data/state/discovery.json`. File `data/state/input_validation.json` ghi danh sách ID hợp lệ, ID sai định dạng và ID trùng. Kết quả tải file được lưu tách biệt tại `data/state/downloads.json` để không ghi đè snapshot discovery.

Ví dụ phối hợp Discovery trước $\rightarrow$ Download sau trong Python, chạy từ thư mục gốc repo:

```python
from pathlib import Path
from src.config import CrawlConfig
from src.discovery import ArxivClient, discover_assigned_range
from src.downloader import download_discovered_sources

config = CrawlConfig(output_dir=Path("data"))
client = ArxivClient(config)
try:
    # Bước 1: Mở rộng khoảng phân công và chạy Discovery trước -> data/state/discovery.json
    discovery_report = discover_assigned_range(
        "2025-10", 9401, "2025-10", 14400, config, resume=True, client=client
    )

    # Bước 2: Tải các version đã được Discovery identify -> data/state/downloads.json
    download_report = download_discovered_sources(
        config, resume=True, client=client
    )
    print(discovery_report["summary"])
    print(download_report["summary"])
finally:
    client.close()
```

## Giao diện đã triển khai (`src/discovery.py`)

- `load_ids(path, *, member_index=None, expected_total=5000, month_max_ids=None)` trả `ids`, `invalid`, `duplicates`. Hỗ trợ cả file liệt kê từng arXiv ID lẫn file bảng khoảng phân công `start_month start_id end_month end_id` (chọn dòng thành viên bằng `member_index`).
- `normalize_id(value)` trả base ID chuẩn hóa (chữ thường với tiền tố archive cũ như `hep-th/9901001`); bỏ suffix version vì yêu cầu crawl cả publication.
- `parse_year_month(value)` & `format_arxiv_id(month, paper_id)` chuyển đổi `("2025-10", 9401)` $\rightarrow$ `"2510.09401"`.
- `parse_assigned_range_line(line)`, `load_assigned_ranges(path)`, `expand_assigned_range(...)`, `prepare_assigned_ids(...)` mở rộng khoảng ID cùng tháng hoặc khác tháng (ví dụ `2025-09 26047 2025-10 4400`) thành danh sách base ID chuẩn.
- `discover_papers(ids, config, resume=False, client=None)` trả report và ghi atomic snapshot `data/state/discovery.json` sau mỗi bài.
- `discover_assigned_range(start_month, start_id, end_month, end_id, config, ...)` mở rộng khoảng phân công, ghi `assigned_ids.txt`, lưu `state/input_validation.json` và gọi `discover_papers`.
- `ArxivClient(config)` gọi tuần tự Atom API với pacing ít nhất 3 giây (`last_request` được cập nhật khi hoàn tất request); timeout/retry 429, 5xx và lỗi mạng. Khi tích hợp downloader dùng chung `ArxivClient` để giữ nhịp $\ge 3$ giây giữa các request.

## Schema report v1 (`data/state/discovery.json`)

Root có `schema_version`, `started_at` UTC, `papers`, `elapsed_seconds`, `summary` (summary chỉ chốt khi run kết thúc).

Paper resolved có: `arxiv_id`, `title`, `authors`, `abstract`, `submission_date`, `latest_updated_date`, `revised_dates`, `revision_dates_complete`, `venue`, `doi`, `latest_version`, `versions`, `metadata_source`, `fetched_at`, `status`, `cache_hit`, `discovery_seconds`.

Mỗi version có `version`, `version_id`, `source_url`, `download_status=pending`, `extraction_status=pending`. Các trạng thái này chỉ là kế hoạch tải, module downloader lưu kết quả thực tế riêng tại `data/state/downloads.json` để refresh discovery không ghi đè tiến độ download.

Paper failed có `arxiv_id`, `status=failed`, `error_type`, `error`, `cache_hit`, `discovery_seconds`. Không có response phù hợp được báo lỗi, không tự coi là paper không tồn tại chắc chắn.

Summary có `assigned_unique_ids`, `resolved`, `failed`, `expected_versions`, `cache_hits`. `elapsed_seconds` là thời gian discovery lần chạy hiện tại, gồm chờ/retry; không phải thời gian toàn scraper. Với cache hit, `fetched_at` vẫn là thời điểm fetch gốc.

## Giới hạn cần xử lý khi tích hợp

- Atom metadata mặc định là bản mới nhất, không đảm bảo metadata riêng cho từng revision.
- Atom trả published/updated, không trả trọn danh sách ngày revision. `revised_dates=[]` và cờ completeness ngăn hiểu nhầm rằng nhóm đã lấy đủ. Cần bổ sung nếu muốn hoàn thiện trường lịch sử.
- Venue lấy từ journal reference, có thể gồm cả volume/pages, chưa phải venue được chuẩn hóa.
- Resume dùng snapshot đã thành công; muốn phát hiện revision mới phải chạy fresh. Một output dir dùng cho một tiến trình discovery tại một thời điểm.
- References, RAM/disk toàn hệ thống và extraction do các thành viên còn lại triển khai.

## Tài liệu chính thức

- arXiv API User Manual: https://info.arxiv.org/help/api/user-manual.html
- arXiv API Terms of Use: https://info.arxiv.org/help/api/tou.html
