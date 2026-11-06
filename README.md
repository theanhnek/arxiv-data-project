# Đồ án Nhập môn Khoa học Dữ liệu — Milestone 1: Thu thập dữ liệu arXiv

Dự án xây dựng pipeline tự động thu thập metadata, mã nguồn LaTeX (tất cả các phiên bản) và dữ liệu trích dẫn khoa học từ arXiv và Semantic Scholar, phục vụ các bước xử lý và phân tích dữ liệu ở các milestone tiếp theo.

---

## 1. Yêu cầu môi trường & Cài đặt

- **Python**: 3.11 trở lên
- **Hệ điều hành**: Windows / Linux / macOS
- **Kết nối mạng**: Cần truy cập được `export.arxiv.org` và `api.semanticscholar.org`

### Các bước cài đặt:

1. **Clone repository:**
   ```bash
   git clone https://github.com/theanhnek/arxiv-data-project.git
   cd arxiv-data-project
   ```

2. **Khởi tạo môi trường ảo và cài đặt thư viện:**
   - Trên Windows:
     ```bash
     python -m venv .venv
     .\.venv\Scripts\pip install -r src/requirements.txt
     ```
   - Trên Linux / macOS:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     pip install -r src/requirements.txt
     ```

3. **Cấu hình file `.env`:**
   Tạo file `.env` từ file mẫu `.env.example`:
   ```bash
   cp .env.example .env      # Linux/macOS
   copy .env.example .env    # Windows
   ```
   Nội dung file `.env`:
   ```dotenv
   # User-Agent định danh ứng dụng của nhóm (bắt buộc theo quy định của arXiv)
   ARXIV_USER_AGENT=Lab1ArxivCrawler/0.1 (contact: your-email@example.com)

   # (Tùy chọn) API key Semantic Scholar nếu có để tăng tốc độ cào trích dẫn
   SEMANTIC_SCHOLAR_API_KEY=
   ```

---

## 2. Chuẩn bị dữ liệu đầu vào (Input)

Theo yêu cầu của đề bài, giảng viên cung cấp dải bài báo được phân công (hoặc danh sách các mã arXiv ID). Hệ thống hỗ trợ 3 hình thức cung cấp input linh hoạt:

### Cách A — Bảng phân công 5 dải của nhóm (`data/assigned_ids.txt`)
Mỗi dòng là dải 5.000 bài được phân công của một thành viên theo định dạng `start_month  start_id  end_month  end_id`:
```text
2025-10	14401	2025-10	19400    # Thành viên 1: Kiệt (5.000 bài tháng 10)
2025-10	9401	2025-10	14400    # Thành viên 2: Dương (5.000 bài tháng 10)
2025-10	4401	2025-10	9400     # Thành viên 3: Hà (5.000 bài tháng 10)
2025-09	26047	2025-10	4400     # Thành viên 4: Kiên (5.000 bài vắt qua 2 tháng)
2025-09	11047	2025-09	16046    # Thành viên 5: Anh (5.000 bài tháng 9)
```
> Khi chạy với file bảng này, dùng kèm cờ `--member-index (1..5)` để chọn đúng dải của thành viên đó.

### Cách B — Danh sách từng arXiv ID cụ thể (`data/assigned_ids.txt`)
Mỗi dòng là một mã arXiv ID, phù hợp khi thầy cô hoặc người dùng muốn test một danh sách bài báo bất kỳ:
```text
# Danh sách bài báo cần kiểm tra
2509.11047
2509.11048
1706.03762
```
> Hỗ trợ định dạng ID mới (`YYMM.NNNNN`), ID cũ (`hep-th/9901001`), suffix version (`...v2`), comment `#` và dòng trống. Các ID trùng sẽ tự động được loại bỏ.

### Cách C — Truyền trực tiếp dải ID qua dòng lệnh (Không cần tạo file)
Thầy cô hoặc người chấm có thể truyền trực tiếp 4 tham số dải vào dòng lệnh qua cờ `--range <start_month> <start_id> <end_month> <end_id>` để kiểm tra nhanh.

---

## 3. Hướng dẫn chạy chương trình

> **Lưu ý:** Nếu bạn đang dùng môi trường ảo trên Windows thì gõ `.\.venv\Scripts\python`, còn nếu đã activate môi trường thì gõ `python`.

### Cách 1: Chạy toàn bộ pipeline một lệnh duy nhất (`--all`)

Lệnh `--all` sẽ tự động thực hiện tuần tự và đo đạc tài nguyên: **Discovery** $\rightarrow$ **Downloader** $\rightarrow$ **Extractor** $\rightarrow$ **References** $\rightarrow$ **Export** $\rightarrow$ **Metrics**.

- **Chạy kiểm thử nhanh (Smoke Test 2 bài mẫu):**
  ```bash
  python -m src.main --all --range 2025-09 11047 2025-09 11048 --output data --resume
  ```

- **Chạy kiểm tra theo dải bài báo trực tiếp:**
  ```bash
  python -m src.main --all --range 2025-10 9401 2025-10 14400 --output data --resume
  ```

- **Chạy theo file phân công (`data/assigned_ids.txt`):**
  - Chạy theo dòng thành viên từ Bảng 5 dòng (ví dụ Thành viên 2):
    ```bash
    python -m src.main --all --ids data/assigned_ids.txt --member-index 2 --output data --resume
    ```
  - Chạy theo file danh sách từng arXiv ID cụ thể:
    ```bash
    python -m src.main --all --ids data/assigned_ids.txt --output data --resume
    ```

---

### Cách 2: Chạy trực quan bằng Notebook (Google Colab / VS Code)

Nhóm chuẩn bị sẵn notebook benchmark tại [`src/colab_benchmark.ipynb`](src/colab_benchmark.ipynb):
1. Mở file trên Google Colab (chọn Runtime: **CPU-only** theo đúng quy định đồ án).
2. Chỉnh cấu hình `RUN_MODE = "PILOT"` (chạy thử vài bài) hoặc `"FULL_MEMBER"` (chạy dải phân công của thành viên).
3. Bấm **Run All**. Notebook sẽ chạy toàn tuyến và sinh ra các bảng đo lường hiệu năng, RAM, Disk và CPU phục vụ báo cáo.

---

### Cách 3: Chạy từng bước độc lập (Modular CLI)

Nếu cần kiểm tra riêng từng module, bạn có thể gọi từng lệnh tương ứng:

```bash
# 1. Khởi tạo cấu trúc thư mục data/
python -m src.main --init --output data

# 2. Bước Discovery (Lấy metadata và danh sách các version)
python -m src.main --discover --range 2025-09 11047 2025-09 11048 --output data --resume

# 3. Bước Download (Tải file nguồn của các bài đã discover)
python -m src.main --download --output data --resume

# 4. Bước Extract (Giải nén, chỉ giữ lại file .tex và .bib, loại bỏ toàn bộ ảnh)
python -m src.main --extract --output data --resume

# 5. Bước References (Lấy trích dẫn từ Semantic Scholar)
python -m src.main --references --output data --resume

# 6. Bước Export (Xuất 2 bảng chuẩn papers.jsonl và references.jsonl)
python -m src.main --export --output data

# 7. Bước Metrics (Tổng hợp số liệu benchmark, tỷ lệ tiết kiệm dung lượng)
python -m src.main --metrics --output data
```

---

## 4. Cấu trúc dữ liệu và Kết quả đầu ra

Toàn bộ dữ liệu sau khi chạy sẽ nằm trong thư mục `data/` (thư mục này được đưa vào `.gitignore` để tránh commit dữ liệu nặng lên Git):

```text
data/
├── corpus/                         # Dữ liệu mã nguồn đã làm sạch
│   └── <paper_id>/
│       ├── v1/source/              # Chỉ chứa file .tex và .bib của version 1
│       ├── v2/source/              # Chỉ chứa file .tex và .bib của version 2
│       └── references.json         # Danh sách trích dẫn đầy đủ từ Semantic Scholar
├── export/
│   ├── papers.jsonl                # Bảng metadata các bài báo (chuẩn Milestone 1)
│   └── references.jsonl            # Bảng trích dẫn các bài có arXiv ID (chuẩn Milestone 1)
├── metrics/
│   ├── benchmark_summary.json      # Thống kê tổng quan (thời gian, RAM, Disk, % tiết kiệm dung lượng)
│   └── per_paper_metrics.json      # Số liệu chi tiết từng bài dùng để vẽ biểu đồ báo cáo
└── state/                          # Trạng thái từng bước (cho phép resume khi bị gián đoạn mạng)
    ├── input_validation.json       # Báo cáo hợp lệ/trùng lặp của danh sách input
    ├── discovery.json              # Trạng thái và metadata của các bài đã tìm thấy
    ├── downloads.json              # Trạng thái tải về và mã băm SHA-256
    ├── extractions.json            # Trạng thái giải nén và dung lượng giữ lại
    └── references.json             # Trạng thái cào trích dẫn
```

---

## 5. Cấu trúc mã nguồn

```text
arxiv-data-project/
├── src/
│   ├── main.py                 # CLI điều khiển toàn bộ pipeline
│   ├── config.py               # Cấu hình đường dẫn, tham số mặc định
│   ├── discovery.py            # Module tìm kiếm và nhận diện các phiên bản bài báo (arXiv API)
│   ├── downloader.py           # Module tải gói nguồn các phiên bản từ mirror export.arxiv.org
│   ├── extractor.py            # Module giải nén an toàn, lọc file .tex/.bib và loại bỏ ảnh
│   ├── references.py           # Module thu thập trích dẫn từ Semantic Scholar API
│   ├── storage.py              # Xử lý lưu trữ JSON an toàn và xuất file .jsonl
│   ├── metrics.py              # Đo đạc hiệu năng hệ thống (RAM, CPU, thời gian, Disk)
│   ├── colab_benchmark.ipynb   # Notebook chạy benchmark trên Google Colab CPU-only
│   └── requirements.txt        # Danh sách thư viện cần thiết
├── tests/                      # Bộ kiểm thử tự động (offline)
├── docs/                       # Tài liệu chi tiết từng module (discovery, downloader, extractor,...)
├── crawling.md                 # Đề bài và đặc tả yêu cầu của Milestone 1
├── plan.md                     # Kế hoạch thực hiện đồ án của nhóm
├── .env.example                # File mẫu cấu hình biến môi trường
└── README.md                   # Hướng dẫn cài đặt và sử dụng
```

---

## 6. Kiểm thử tự động (Unit Tests)

Dự án đi kèm bộ 84 kiểm thử tự động offline (dùng mock data, không gọi request thật ra ngoài mạng):

```bash
python -m pytest tests/ -v
```

---

## 7. Báo cáo đồ án (Jupyter Book)

Báo cáo kết quả và thống kê chi tiết được xây dựng bằng Jupyter Book và triển khai tự động qua GitHub Pages tại repository riêng:
- **Repository báo cáo**: [kilabeobeo72/arxiv-data-report](https://github.com/kilabeobeo72/arxiv-data-report)
- Toàn bộ bảng biểu và biểu đồ hiệu năng trong báo cáo được sinh tự động bằng mã Python từ các file `data/metrics/*.json` khi biên dịch sách.
