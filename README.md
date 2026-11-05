# arXiv Data Project

Thu thập và xử lý dữ liệu bài báo arXiv cho đồ án Nhập môn Khoa học Dữ liệu. Dự án hướng đến xây dựng corpus LaTeX theo từng phiên bản, chuẩn hóa cấu trúc tài liệu và phát triển hệ thống reference matching qua ba milestone.

## Phạm vi triển khai

Milestone 1 thu thập metadata, source LaTeX và reference metadata của các bài báo được phân công. Corpus này là đầu vào cho bước parsing ở Milestone 2 và reference matching ở Milestone 3.

Hiện CLI hỗ trợ:
1. **Bước 1 — Discovery (`--discover` / `src.discovery`)**: Mở rộng khoảng phân công (`start_month start_id end_month end_id`) hoặc đọc danh sách arXiv ID, lấy metadata từ arXiv API, xác định toàn bộ phiên bản (`v1..vN`) và lưu vào `data/state/discovery.json`
2. **Bước 2 — Download (`--download` / `src.downloader`)**: Tải tuần tự các gói source của mọi phiên bản đã được Discovery identify từ mirror `https://export.arxiv.org/src/{version_id}`, kiểm tra toàn vẹn `SHA-256` và lưu trạng thái vào `data/state/downloads.json`
3. **Bước 3 — Extraction (`--extract` / `src.extractor`)**: Trích xuất an toàn các file `.tex` và `.bib` từ các gói source đã tải, loại bỏ hoàn toàn hình ảnh và file binary, bảo toàn cấu trúc thư mục và bytes nội dung, lưu trạng thái vào `data/state/extractions.json`
4. **Bước 4 — References & Storage (`--references`, `--export` / `src.references`, `src.storage`)**: Thu thập trích dẫn từ Semantic Scholar API theo base arXiv ID, tự động điều tiết rate limit và phân trang, lưu toàn bộ trích dẫn thô vào `corpus/<paper>/references.json`, đồng thời xuất 2 bảng dữ liệu chuẩn `papers.jsonl` và `references.jsonl` vào `data/export/`
5. **Bước 5 — Metrics & Benchmark (`--metrics` / `src.metrics`)**: Đo đạc tài nguyên hệ thống theo thời gian thực (RAM, Disk qua `psutil`), tổng hợp toàn bộ số liệu hiệu năng, tính toán tỷ lệ tiết kiệm dung lượng khi loại bỏ hình ảnh, xuất `data/metrics/benchmark_summary.json` và `data/metrics/per_paper_metrics.json` phục vụ báo cáo Jupyter Book
6. **Toàn tuyến Pipeline (`--all`)**: Chạy xuyên suốt toàn bộ cả 5 bước trên trong một lệnh duy nhất kèm theo dõi tài nguyên hệ thống.

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

### 2. Chuẩn bị danh sách bài báo được phân công (`assigned_ids.txt`)

Hệ thống hỗ trợ 2 cách thiết lập file `data/assigned_ids.txt`:

#### Cách A — Bảng khoảng phân công 5 dòng của nhóm (Mỗi dòng là dải 5.000 bài của 1 thành viên)
Lưu file `data/assigned_ids.txt` với nội dung dạng TSV hoặc khoảng trắng (`start_month  start_id  end_month  end_id`):
```text
2025-10	14401	2025-10	19400    # Thành viên 1: Kiệt
2025-10	9401	2025-10	14400    # Thành viên 2: Dương
2025-10	4401	2025-10	9400     # Thành viên 3: Hà
2025-09	26047	2025-10	4400     # Thành viên 4: Kiên (vắt qua 2 tháng)
2025-09	11047	2025-09	16046    # Thành viên 5: Anh
```
> Khi chạy với file bảng này, sử dụng thêm cờ `--member-index (1..5)` để chọn đúng dải 5.000 bài của thành viên đó.

#### Cách B — Liệt kê danh sách từng arXiv ID cụ thể (Mỗi dòng 1 ID, thích hợp chạy thử tập nhỏ)
```text
# Danh sách chạy thử nghiệm nhanh
2509.11047
2509.11048
```
> Hỗ trợ ID mới (`YYMM.NNNNN`), ID cũ (`hep-th/9901001`), suffix version (`...v2`), comment `#` và dòng trống. ID trùng được tự động loại bỏ.

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

### 5. Bước 3 — Chạy Extraction (Trích xuất TeX/BibTeX & loại bỏ hình ảnh)

Sau khi đã có `data/state/downloads.json`, giải nén và lọc chỉ giữ lại file `.tex` và `.bib` vào thư mục `source/` của từng version:

```powershell
.\.venv\Scripts\python.exe -m src.main --extract --output data --resume
```

Hoặc gọi trực tiếp module `src.extractor`:

```powershell
.\.venv\Scripts\python.exe -m src.extractor --output data --resume
```

### 6. Bước 4 — Thu thập References từ Semantic Scholar và xuất hai bảng

Chỉ cần có `data/state/discovery.json` (không phụ thuộc Download/Extract). Điền `SEMANTIC_SCHOLAR_API_KEY` trong `.env` nếu có để chạy nhanh hơn (1 request/giây; không có khóa là 1 request/3,1 giây):

```powershell
.\.venv\Scripts\python.exe -m src.main --references --output data --resume
.\.venv\Scripts\python.exe -m src.main --export --output data
```

Thử nhanh với một bài trước khi chạy cả dải: `.\.venv\Scripts\python.exe -m src.references --arxiv-id 1706.03762 --output data/smoke`. Chi tiết, schema và hạn chế: [docs/references.md](docs/references.md).

### 7. Bước 5 — Đo đạc Metrics & Hiệu năng

Tổng hợp các chỉ số hiệu năng và tài nguyên từ các bước trước:

```powershell
.\.venv\Scripts\python.exe -m src.main --metrics --output data
```

Hoặc gọi trực tiếp module `src.metrics`:

```powershell
.\.venv\Scripts\python.exe -m src.metrics --output data
```

### 8. Hướng dẫn các cách chạy Pipeline (Demo & Thực tế)

Hệ thống hỗ trợ 3 cách chạy toàn tuyến (**Discovery** $\rightarrow$ **Download** $\rightarrow$ **Extract** $\rightarrow$ **References** $\rightarrow$ **Export** $\rightarrow$ **Metrics**) kèm đo tài nguyên hệ thống bằng cờ `--all`:

---

#### Cách 1: Chạy CLI với `--range` trực tiếp (Khuyên dùng khi test nhanh hoặc chạy theo dải)
Không cần tạo trước file ID; truyền trực tiếp dải ID vào câu lệnh:

- **Lệnh Test nhanh 2 bài (Smoke Test toàn luồng mất ~30 giây):**
  - *PowerShell:*
    ```powershell
    .\.venv\Scripts\python.exe -m src.main --all --range 2025-09 11047 2025-09 11048 --output data --resume
    ```
  - *Command Prompt (CMD):*
    ```cmd
    .venv\Scripts\python.exe -m src.main --all --range 2025-09 11047 2025-09 11048 --output data --resume
    ```

- **Lệnh chạy Full dải 5.000 bài (Ví dụ Thành viên 2: `2025-10 9401` đến `2025-10 14400`):**
  - *PowerShell:*
    ```powershell
    .\.venv\Scripts\python.exe -m src.main --all --range 2025-10 9401 2025-10 14400 --output data --resume
    ```
  - *CMD:*
    ```cmd
    .venv\Scripts\python.exe -m src.main --all --range 2025-10 9401 2025-10 14400 --output data --resume
    ```

---

#### Cách 2: Chạy CLI với file phân công `--ids data/assigned_ids.txt`

- **Nếu file là Bảng 5 dòng phân công:** Dùng kèm `--member-index 1..5`:
  - *PowerShell:*
    ```powershell
    # Ví dụ chạy dải 5.000 bài của Thành viên 2:
    .\.venv\Scripts\python.exe -m src.main --all --ids data/assigned_ids.txt --member-index 2 --output data --resume
    ```
  - *CMD:*
    ```cmd
    .venv\Scripts\python.exe -m src.main --all --ids data/assigned_ids.txt --member-index 2 --output data --resume
    ```

- **Nếu file là danh sách từng arXiv ID cụ thể:** Bỏ cờ `--member-index`:
  - *PowerShell:*
    ```powershell
    .\.venv\Scripts\python.exe -m src.main --all --ids data/assigned_ids.txt --output data --resume
    ```
  - *CMD:*
    ```cmd
    .venv\Scripts\python.exe -m src.main --all --ids data/assigned_ids.txt --output data --resume
    ```

---

#### Cách 3: Chạy trực quan bằng Notebook (`src/colab_benchmark.ipynb`)
- **Chạy trên Google Colab CPU-only:**
  1. Mở file `src/colab_benchmark.ipynb` trên Colab (Runtime: CPU-only).
  2. Chọn `RUN_MODE = "PILOT"` (chạy thử 5 bài) hoặc `"FULL_MEMBER"` (chạy 5.000 bài theo `MEMBER_INDEX`).
  3. Bấm **Run All** để xem tiến trình và tự động sinh file đo lường `data/metrics/benchmark_summary.json`.
- **Chạy trong VS Code:** Mở file notebook và chọn kernel `.venv`.

---

#### Cách 4: Chạy kết hợp các bước đơn lẻ (Phục vụ kiểm tra độc lập từng module)
Có thể gọi riêng lẻ từng bước hoặc ghép nối tùy ý:
```powershell
# Chỉ chạy Download và Extract cho dữ liệu đã Discovery trước đó:
.\.venv\Scripts\python.exe -m src.main --download --extract --output data --resume

# Chỉ chạy thu thập References và xuất 2 bảng dữ liệu:
.\.venv\Scripts\python.exe -m src.main --references --export --output data --resume

# Chỉ tính toán lại bảng metrics hiệu năng:
.\.venv\Scripts\python.exe -m src.main --metrics --output data
```

Xem toàn bộ tùy chọn:

```powershell
.\.venv\Scripts\python.exe -m src.main --help
.\.venv\Scripts\python.exe -m src.downloader --help
.\.venv\Scripts\python.exe -m src.extractor --help
.\.venv\Scripts\python.exe -m src.metrics --help
```

### Đọc kết quả

- `data/state/input_validation.json`: danh sách ID hợp lệ, ID trùng và các dòng không hợp lệ.
- `data/state/discovery.json`: metadata, trạng thái từng bài, danh sách phiên bản (`v1..vN`) và thống kê discovery.
- `data/state/downloads.json`: kết quả tải từng version (`downloaded`, `not_found`, `invalid_response`, `failed`), đường dẫn file nguồn, kích thước byte, mã băm `SHA-256` và thống kê download.
- `data/state/extractions.json`: kết quả giải nén từng version (`success`, `no_tex_source`, `skipped_not_downloaded`, `failed`), danh sách file `.tex`/`.bib`, dung lượng byte giữ lại (`retained_bytes`), tổng dung lượng uncompressed archive (`archive_uncompressed_bytes`) và thống kê extraction.
- `data/corpus/<paper_id>/<vN>/source/`: thư mục chứa các file mã nguồn `.tex` và `.bib` của từng phiên bản sau khi đã lọc bỏ hoàn toàn các file ảnh và file binary.
- `data/metrics/benchmark_summary.json`: báo cáo tổng quan toàn bộ pipeline (thời gian chạy từng bước, tỷ lệ thành công, RAM đỉnh/trung bình, Disk đỉnh/cuối, tỷ lệ tiết kiệm khi lọc ảnh và độ bao phủ trích dẫn).
- `data/metrics/per_paper_metrics.json`: bảng dữ liệu chi tiết cho từng bài báo và từng phiên bản dùng để đưa vào Jupyter Book tạo bảng và biểu đồ tự động.
- `data/state/references.json`: kết quả references từng bài (`success`, `empty_references`, `not_found`, `failed`), số reference và thống kê.
- `data/corpus/<paper_id>/references.json`: toàn bộ reference của bài (cấp publication, áp dụng cho mọi version), gồm cả reference không có arXiv ID và phản hồi thô.
- `data/export/papers.jsonl`, `data/export/references.jsonl`: hai bảng cuối (`references` chỉ gồm reference có arXiv ID).

## Cấu trúc repository

```text
arxiv-data-project/
├── src/
│   ├── main.py                 # CLI (init, discover -> download -> extract -> references -> export -> metrics)
│   ├── config.py               # Cấu hình dùng chung
│   ├── discovery.py            # Mở rộng khoảng ID, resolve metadata & liệt kê phiên bản
│   ├── downloader.py           # Tải versioned sources từ state/discovery.json
│   ├── extractor.py            # Trích xuất .tex/.bib và loại bỏ hình ảnh/binary
│   ├── references.py           # Thu thập references từ Semantic Scholar (Thành viên 4)
│   ├── storage.py              # Lưu JSON an toàn, ánh xạ ID, xuất hai bảng (Thành viên 4)
│   ├── metrics.py              # Module đo hiệu năng & tài nguyên hệ thống
│   ├── colab_benchmark.ipynb   # Notebook chạy benchmark trên Google Colab CPU-only
│   └── requirements.txt
├── tests/                      # Kiểm tra tự động (offline)
├── docs/
│   ├── discovery.md            # Giao diện, schema và hướng dẫn chạy Discovery (Thành viên 1)
│   ├── downloader.md           # Giao diện, schema và hướng dẫn chạy Downloader (Thành viên 2)
│   ├── extractor.md            # Giao diện, schema và hướng dẫn chạy Extractor (Thành viên 3)
│   ├── references.md           # Giao diện, schema và hướng dẫn chạy References & Storage (Thành viên 4)
│   └── metrics.md              # Giao diện, schema và hướng dẫn chạy Metrics (Thành viên 5)
├── .env.example
├── .gitignore
├── plan.md                     # Kế hoạch triển khai
└── README.md
```

Thư mục `data/` được tạo cục bộ khi chạy và được loại khỏi Git, cùng với môi trường ảo, thông tin xác thực và output sinh ra.

## Kiểm tra

Chạy toàn bộ các kiểm tra tự động offline (không gửi request thật đến arXiv):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery.py tests/test_downloader.py tests/test_extractor.py tests/test_storage.py tests/test_references.py tests/test_metrics.py -v --basetemp data/test-suite
```

## Schema và giới hạn hiện tại

Đặc tả đầu ra và giao diện tích hợp được mô tả chi tiết trong [docs/discovery.md](docs/discovery.md), [docs/downloader.md](docs/downloader.md), [docs/extractor.md](docs/extractor.md), [docs/references.md](docs/references.md) và [docs/metrics.md](docs/metrics.md).

- Metadata được lấy ở mức publication theo phiên bản mới nhất, chưa phải metadata riêng cho mỗi revision.
- `latest_updated_date` là thời điểm cập nhật mới nhất. `revised_dates` chưa có lịch sử đầy đủ khi `revision_dates_complete` là `false`.
- `venue` hiện lấy từ journal reference của arXiv và chưa được chuẩn hóa thành tên venue riêng.
- Discovery và Downloader sử dụng chung cơ chế request tuần tự với khoảng cách ít nhất 3 giây (`ArxivClient`).
- Quá trình Extraction lọc bỏ tất cả file không có đuôi `.tex` hoặc `.bib` (như file ảnh `.png`, `.jpg`, `.pdf`, `.eps`), giữ nguyên cấu trúc thư mục con và bảo toàn từng byte của file nguồn.
- Module Metrics tự động tổng hợp số liệu kể cả khi module references của Thành viên 4 chưa hoàn thành (được đánh dấu `references.available = false`).

## Báo cáo và dữ liệu

Báo cáo Milestone 1–2 được xây dựng bằng Jupyter Book trong một repository public riêng và triển khai trên GitHub Pages theo yêu cầu đồ án. Đường dẫn báo cáo sẽ được bổ sung sau khi triển khai.

Corpus được nhóm lưu để sử dụng cho các milestone sau. Milestone 1 nộp link code và báo cáo; dataset hoàn chỉnh được xuất bản trên Hugging Face ở giai đoạn cuối dự án.

## Tài liệu tham khảo

- [arXiv API User Manual](https://info.arxiv.org/help/api/user-manual.html)
- [arXiv API Terms of Use](https://info.arxiv.org/help/api/tou.html)
- [Project Problem Statements](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/)
- References lấy ở mức publication từ Semantic Scholar, không phân biệt version. `ref_submission_date` để trống cho tới khi có ngày nộp từ metadata arXiv (không dùng `publicationDate`/`year` thay thế).
