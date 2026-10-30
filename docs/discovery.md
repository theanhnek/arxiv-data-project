# Bàn giao phần thành viên 1

## Hướng dẫn chạy cho thành viên 2

Chạy các lệnh PowerShell dưới đây từ thư mục gốc repository, không chạy từ bên trong `src/`.

### 1. Chuẩn bị môi trường và danh sách ID

Nếu chưa cài môi trường:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r src/requirements.txt
Copy-Item .env.example .env
```

Chỉ copy `.env.example` khi chưa có `.env` để tránh ghi đè cấu hình cá nhân. Điền thông tin liên hệ trong `ARXIV_USER_AGENT` của `.env`.

Tạo thư mục dữ liệu:

```powershell
.\.venv\Scripts\python.exe -m src.main --init --output data
```

Tạo file `data/assigned_ids.txt` theo danh sách được giao, mỗi dòng một ID. Nếu chỉ muốn chạy thử và file chưa tồn tại, có thể tạo mẫu:

```powershell
@'
1706.03762
1810.04805
'@ | Set-Content -LiteralPath data/assigned_ids.txt -Encoding utf8
```

Lệnh tạo mẫu sẽ thay nội dung file nếu đã tồn tại. Hai ID trên chỉ để thử, không phải danh sách phân công chính thức. `data/` không được đưa lên GitHub nên mỗi thành viên cần tự chuẩn bị input và chạy discovery trên máy mình.

### 2. Chạy discovery

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --output data
```

Nếu bị ngắt hoặc có bài lỗi, chạy tiếp bằng:

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --output data --resume
```

Kiểm tra `summary`: `resolved` là số bài lấy metadata thành công, `failed` là số bài lỗi, `expected_versions` là tổng số version cần tải. Với resume, `cache_hits` cho biết số bài được dùng lại từ snapshot. Muốn tìm revision mới, chạy lại không có `--resume`.

### 3. Dùng đầu ra để triển khai downloader

Đầu ra chính là `data/state/discovery.json`. File `data/state/input_validation.json` ghi ID sai định dạng và ID trùng. Nếu discovery báo lỗi, kiểm tra các file này; không coi bài lỗi là bài không có source.

Ví dụ đọc danh sách công việc trong Python, chạy từ thư mục gốc repo:

```python
import json
from pathlib import Path

report = json.loads(
    Path("data/state/discovery.json").read_text(encoding="utf-8")
)

for paper in report["papers"]:
    if paper["status"] != "resolved":
        continue
    for version in paper["versions"]:
        version_id = version["version_id"]
        source_url = version["source_url"]
        print(version_id, source_url)
        # Nối hàm download của thành viên 2 tại đây khi đã triển khai.
```

Đoạn trên chỉ đọc/in công việc, chưa tải file. Downloader cần tải từng `source_url`, ghi kết quả và lỗi riêng; không sửa snapshot discovery thành nơi lưu tiến độ download. Phối hợp pacing arXiv giữa discovery và download khi ghép pipeline.

## Giao diện đã triển khai

- `load_ids(path)` trả `ids`, `invalid`, `duplicates`. Input TXT UTF-8 một ID/dòng; không hỗ trợ CSV hoặc khoảng ID tự suy diễn.
- `normalize_id(value)` trả base ID; hỗ trợ ID mới/cũ và bỏ suffix version vì yêu cầu crawl cả publication.
- `discover_papers(ids, config, resume=False, client=None)` trả report và ghi atomic snapshot sau mỗi bài.
- `ArxivClient(config)` gọi tuần tự Atom API với pacing ít nhất 3 giây; timeout/retry 429, 5xx và lỗi mạng. Khi tích hợp downloader phải dùng chung cơ chế pacing; không tạo các worker gọi arXiv song song.

## Schema report v1

Root có `schema_version`, `started_at` UTC, `papers`, `elapsed_seconds`, `summary` (summary chỉ chốt khi run kết thúc).

Paper resolved có: `arxiv_id`, `title`, `authors`, `abstract`, `submission_date`, `latest_updated_date`, `revised_dates`, `revision_dates_complete`, `venue`, `doi`, `latest_version`, `versions`, `metadata_source`, `fetched_at`, `status`, `cache_hit`, `discovery_seconds`.

Mỗi version có `version`, `version_id`, `source_url`, `download_status=pending`, `extraction_status=pending`. Các trạng thái này chỉ là kế hoạch tải, module khác nên lưu kết quả thực tế riêng để refresh discovery không ghi đè tiến độ download.

Paper failed có `arxiv_id`, `status=failed`, `error_type`, `error`, `cache_hit`, `discovery_seconds`. Không có response phù hợp được báo lỗi, không tự coi là paper không tồn tại chắc chắn.

Summary có `assigned_unique_ids`, `resolved`, `failed`, `expected_versions`, `cache_hits`. `elapsed_seconds` là thời gian discovery lần chạy hiện tại, gồm chờ/retry; không phải thời gian toàn scraper. Với cache hit, `fetched_at` vẫn là thời điểm fetch gốc.

## Giới hạn cần xử lý khi tích hợp

- Atom metadata mặc định là bản mới nhất, không đảm bảo metadata riêng cho từng revision.
- Atom trả published/updated, không trả trọn danh sách ngày revision. `revised_dates=[]` và cờ completeness ngăn hiểu nhầm rằng nhóm đã lấy đủ. Cần bổ sung nếu muốn hoàn thiện trường lịch sử.
- Venue lấy từ journal reference, có thể gồm cả volume/pages, chưa phải venue được chuẩn hóa.
- Resume dùng snapshot đã thành công; muốn phát hiện revision mới phải chạy fresh. Một output dir dùng cho một tiến trình discovery tại một thời điểm.
- References, RAM/disk toàn hệ thống và download/extraction do các thành viên còn lại triển khai. Main hiện chỉ expose init/discovery để không gọi các stub như đã hoàn thành.

## Tài liệu chính thức

- arXiv API User Manual: https://info.arxiv.org/help/api/user-manual.html
- arXiv API Terms of Use: https://info.arxiv.org/help/api/tou.html
