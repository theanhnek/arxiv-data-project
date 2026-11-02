# arXiv Data Project

Thu thập và xử lý dữ liệu bài báo arXiv cho đồ án Nhập môn Khoa học Dữ liệu. Dự án hướng đến xây dựng corpus LaTeX theo từng phiên bản, chuẩn hóa cấu trúc tài liệu và phát triển hệ thống reference matching qua ba milestone.

## Phạm vi triển khai

Milestone 1 thu thập metadata, source LaTeX và reference metadata của các bài báo được phân công. Corpus này là đầu vào cho bước parsing ở Milestone 2 và reference matching ở Milestone 3

Hiện CLI hỗ trợ:
1. **Bước 1 — Discovery (`--discover`)**: Mở rộng khoảng phân công (`start_month start_id end_month end_id`) hoặc đọc danh sách arXiv ID, lấy metadata từ arXiv API, xác định toàn bộ phiên bản (`v1..vN`) và lưu vào `data/state/discovery.json`
2. **Bước 2 — Download (`--download` / `src.downloader`)**: Tải tuần tự các gói source của mọi phiên bản đã được Discovery identify từ mirror `https://export.arxiv.org/src/{version_id}`, kiểm tra toàn vẹn `SHA-256` và lưu trạng thái vào `data/state/downloads.json`

## Yêu cầu môi trường

- Python 3.11+
- Kết nối Internet để truy cập arXiv API và mirror `export.arxiv.org`
- Các thư viện trong `src/requirements.txt`

Các lệnh dưới đây sử dụng PowerShell trên Windows và được chạy từ thư mục gốc của repository.

## Cài đặt

```powershell
git clone https://github.com/theanhnek/arxiv-data-project.git
cd arxiv-data-project
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r src/requirements.txt
Copy-Item .env.example .env
```

Trong `.env`, đặt `ARXIV_USER_AGENT` với tên ứng dụng và địa chỉ liên hệ của nhóm:

```dotenv
ARXIV_USER_AGENT=Lab1ArxivCrawler/0.1 (contact: your-email@example.com)
```

CLI đọc biến này khi khởi động. `SEMANTIC_SCHOLAR_API_KEY` được dành cho module references; discovery và downloader không sử dụng biến đó. File `.env` không được đưa vào Git.

## Sử dụng

### 1. Khởi tạo thư mục dữ liệu

```powershell
.\.venv\Scripts\python.exe -m src.main --init --output data
```

### 2. Chuẩn bị danh sách bài báo được phân công

Bộ đọc `load_ids` trong `src/discovery.py` hỗ trợ cả **bảng khoảng phân công 5 dòng của nhóm** (`start_month start_id end_month end_id`) lẫn **danh sách từng arXiv ID**:

- **Cách A — Lưu bảng phân công 5 dòng vào `data/assigned_ids.txt`**:
  ```text
  2025-10	14401	2025-10	19400
  2025-10	9401	2025-10	14400
  2025-10	4401	2025-10	9400
  2025-09	26047	2025-10	4400
  2025-09	11047	2025-09	16046
  ```
- **Cách B — Hoặc liệt kê từng arXiv ID trên mỗi dòng**:
  ```text
  # Sample IDs; replace with the assigned list before the full crawl.
  1706.03762
  1810.04805
  ```

Bộ đọc hỗ trợ khoảng ID (`YYYY-MM` + số thứ tự), ID mới (`YYMM.NNNNN`), ID cũ như `hep-th/9901001`, suffix phiên bản như `1706.03762v2`, dòng trống và comment bắt đầu bằng `#`. ID có suffix được quy về publication để tìm tất cả phiên bản. ID trùng được loại bỏ; dòng sai định dạng được ghi lại tại `data/state/input_validation.json`.

### 3. Bước 1 — Chạy Discovery (Identify bài báo & các phiên bản trước)

- **Chạy theo dòng thành viên (`1..5`) từ file bảng phân công**:
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --member-index 2 --output data --resume
  ```
- **Hoặc truyền trực tiếp khoảng phân công qua `--range`**:
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --discover --range 2025-10 9401 2025-10 14400 --output data --resume
  ```
- **Hoặc chạy trên file liệt kê từng arXiv ID**:
  ```powershell
  .\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --output data --resume
  ```

Discovery lấy metadata và tạo danh sách phiên bản từ `v1` đến phiên bản mới nhất tại `data/state/discovery.json`.

### 4. Bước 2 — Chạy Download (Tải các version đã được Discovery identify)

Sau khi đã có `data/state/discovery.json`, tải các gói source của những bài `status: resolved`:

```powershell
.\.venv\Scripts\python.exe -m src.main --download --output data --resume
```

Hoặc gọi trực tiếp module `src.downloader`:

```powershell
.\.venv\Scripts\python.exe -m src.downloader --output data --resume
```

Có thể gộp tuần tự Bước 1 (`--discover`) rồi Bước 2 (`--download`) trong một lệnh và cấu hình `--timeout`, `--attempts`:

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --download --range 2025-10 9401 2025-10 14400 --output data --resume --timeout 60 --attempts 3
```

Xem toàn bộ tùy chọn:

```powershell
.\.venv\Scripts\python.exe -m src.main --help
.\.venv\Scripts\python.exe -m src.downloader --help
```

### Đọc kết quả

- `data/state/input_validation.json`: danh sách ID hợp lệ, ID trùng và các dòng không hợp lệ.
- `data/state/discovery.json`: metadata, trạng thái từng bài, danh sách phiên bản (`v1..vN`) và thống kê discovery.
- `data/state/downloads.json`: kết quả tải từng version (`downloaded`, `not_found`, `invalid_response`, `failed`), đường dẫn file nguồn, kích thước byte, mã băm `SHA-256` và thống kê download.

## Cấu trúc repository

```text
arxiv-data-project/
├── src/
│   ├── main.py              # CLI (init, discover trước -> download sau)
│   ├── config.py            # Cấu hình dùng chung
│   ├── discovery.py         # Mở rộng khoảng ID, resolve metadata & liệt kê phiên bản
│   ├── downloader.py        # Tải versioned sources từ state/discovery.json
│   ├── extractor.py         # Trích xuất .tex/.bib và loại bỏ hình ảnh/binary
│   ├── references.py        # Module reference metadata
│   ├── storage.py           # Module lưu dữ liệu
│   ├── metrics.py           # Module đo hiệu năng
│   └── requirements.txt
├── tests/                   # Kiểm tra tự động (offline)
├── docs/
│   ├── discovery.md         # Giao diện, schema và hướng dẫn chạy Discovery (Thành viên 1)
│   └── downloader.md        # Giao diện, schema và hướng dẫn chạy Downloader (Thành viên 2)
├── .env.example
├── .gitignore
├── plan.md                  # Kế hoạch triển khai
└── README.md
```

Thư mục `data/` được tạo cục bộ khi chạy và được loại khỏi Git, cùng với môi trường ảo, thông tin xác thực và output sinh ra.

## Kiểm tra

Chạy toàn bộ các kiểm tra tự động offline (không gửi request thật đến arXiv):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery.py tests/test_downloader.py tests/test_extractor.py -v --basetemp data/test-suite
```

## Schema và giới hạn hiện tại

Đặc tả đầu ra và giao diện tích hợp được mô tả chi tiết trong [docs/discovery.md](docs/discovery.md) và [docs/downloader.md](docs/downloader.md).

- Metadata được lấy ở mức publication theo phiên bản mới nhất, chưa phải metadata riêng cho mỗi revision.
- `latest_updated_date` là thời điểm cập nhật mới nhất. `revised_dates` chưa có lịch sử đầy đủ khi `revision_dates_complete` là `false`.
- `venue` hiện lấy từ journal reference của arXiv và chưa được chuẩn hóa thành tên venue riêng.
- Discovery và Downloader sử dụng chung cơ chế request tuần tự với khoảng cách ít nhất 3 giây (`ArxivClient`).

## Báo cáo và dữ liệu

Báo cáo Milestone 1–2 được xây dựng bằng Jupyter Book trong một repository public riêng và triển khai trên GitHub Pages theo yêu cầu đồ án. Đường dẫn báo cáo sẽ được bổ sung sau khi triển khai.

Corpus được nhóm lưu để sử dụng cho các milestone sau. Milestone 1 nộp link code và báo cáo; dataset hoàn chỉnh được xuất bản trên Hugging Face ở giai đoạn cuối dự án.

## Tài liệu tham khảo

- [arXiv API User Manual](https://info.arxiv.org/help/api/user-manual.html)
- [arXiv API Terms of Use](https://info.arxiv.org/help/api/tou.html)
- [Project Problem Statements](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/)
