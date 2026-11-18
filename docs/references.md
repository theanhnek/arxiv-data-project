# Bàn giao phần thành viên 4 — References & Storage

## 1. Vị trí trong Pipeline (Discovery → Download → Extract → References → Export)

`src/references.py` thu thập danh sách trích dẫn của từng bài từ **Semantic Scholar Graph API**, `src/storage.py` lo việc lưu an toàn và xuất hai bảng cuối `papers` / `references`.

- **Đầu vào:** `data/state/discovery.json` (các bài có `status == "resolved"`), không phụ thuộc Download hay Extract nên có thể chạy song song với hai bước đó.
- **Mức dữ liệu:** *publication-level*. Mỗi bài chỉ được hỏi **một lần** theo ID gốc; Semantic Scholar không phân biệt reference theo version nên kết quả áp dụng cho mọi version `v1..vN`.
- **Đầu ra:** cache từng bài `data/corpus/<safe_id>/references.json`, báo cáo chạy `data/state/references.json` (đúng tên, trạng thái và trường mà `src/metrics.py` đang đọc), và hai bảng `data/export/papers.jsonl`, `data/export/references.jsonl`.

---

## 2. Hướng dẫn chạy trên PowerShell

Chạy từ thư mục gốc repository (không chạy từ trong `src/`). Cần đã có `data/state/discovery.json` (xem `docs/discovery.md`).

### 2.1 Kiểm tra nhanh với API thật (nên làm đúng 1 lần trước khi chạy dài)

Module được kiểm thử offline và với một máy chủ giả cục bộ; **chưa** được chạy với `api.semanticscholar.org` thật. Hãy thử một bài đã biết:

```powershell
.\.venv\Scripts\python.exe -m src.references --arxiv-id 1706.03762 --output data/smoke
```

Kỳ vọng: trạng thái `success`, có reference và một phần trong đó có arXiv ID. Xem `data/smoke/corpus/1706-03762/references.json`. Nếu trạng thái là `failed` hoặc cấu trúc phản hồi khác (`unexpected_response`), báo ngay cho Thành viên 4 trước khi chạy cả dải.

### 2.2 Chạy cho dải ID của mình

Điền `SEMANTIC_SCHOLAR_API_KEY` trong `.env` nếu có (khuyến nghị, xem mục 5).

```powershell
.\.venv\Scripts\python.exe -m src.main --references --output data --resume
```

Hoặc gọi trực tiếp module:

```powershell
.\.venv\Scripts\python.exe -m src.references --output data --resume
```

Chạy lại sau khi bị ngắt: thêm `--resume`; các bài đã `success` / `empty_references` / `not_found` được dùng lại, bài `failed` hoặc chưa hỏi sẽ được hỏi tiếp. Muốn hỏi lại cả bài `not_found` (ví dụ bài mới chưa được Semantic Scholar lập chỉ mục): `--resume --retry-not-found`.

### 2.3 Xuất hai bảng cuối

```powershell
.\.venv\Scripts\python.exe -m src.main --export --output data
```

### 2.3b Xuất Parquet và upload Hugging Face

Đề yêu cầu mỗi thành viên nộp dataset riêng (bảng Parquet chia shard) trên tài khoản Hugging Face cá nhân:

```powershell
.\.venv\Scripts\python.exe -m src.main --export-parquet --output data
$env:HF_TOKEN = "<token có quyền write>"
.\.venv\Scripts\python.exe -m src.hf_upload --repo-id <ten-tai-khoan>/<ten-dataset> --output data
```

- Thư mục `data/export/parquet/` có đúng cấu trúc repo Hugging Face theo đề: `README.md` (dataset card khai báo config) và `data/papers/*.parquet`, `data/references/*.parquet` với tên shard dạng `train-00000-of-0000N.parquet`; `--shard-rows` đặt số dòng tối đa mỗi shard (mặc định 50.000).
- Cột danh sách (`authors`, `revised_dates`, `ref_authors`) lưu dạng `list<string>`; `arxiv_id` và `ref_arxiv_id` không cho null theo đề, các cột còn lại là chuỗi cho phép null.
- `README.md` ở gốc khai báo hai config `papers` và `references`; file chỉ được tạo khi chưa tồn tại. Khi thêm các bảng Milestone 2 và 3 (`elements`, `hierarchy`, `bibtex`, `predictions`) vào cùng thư mục `data/`, cần bổ sung config tương ứng vào file này.
- Dataset tạo ở chế độ công khai (đề yêu cầu link công khai). Không commit token vào repo; module chỉ đọc `HF_TOKEN`.
- Đề Milestone 3 liệt kê 6 bảng cuối: `papers`, `references` (Milestone 1), `elements`, `hierarchy`, `bibtex` (Milestone 2) và `predictions` (Milestone 3); không có bảng `statements`.

### 2.4 Chạy gộp với các bước khác và đo hiệu năng

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --download --extract --references --export --metrics --range 2024-10 13025 2024-10 18024 --output data --resume
```

`--metrics` đọc `data/state/references.json` để điền khối `references` trong `benchmark_summary.json`.

### 2.5 Chạy kiểm tra tự động (offline)

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_storage.py tests/test_references.py -v --basetemp data/test-suite
```

---

## 3. Giao diện đã triển khai

### `src/references.py`
- `fetch_references(arxiv_id, config, *, client=None) -> ReferencesResult`: hỏi một bài, trả `status`, `references` (đã chuẩn hóa), `raw_pages` (phản hồi thô), số request, lỗi có cấu trúc. Truyền chung `client` để giữ nhịp giữa các lần gọi.
- `collect_references(config, *, ids=None, discovery_file=None, resume=False, retry_not_found=False, client=None) -> dict`: hỏi mọi bài `resolved`, lưu cache từng bài rồi mới ghi vào báo cáo, ghi `state/references.json` định kỳ (mỗi 100 bài) và khi thoát, kể cả khi Ctrl+C.
- `SemanticScholarClient`: tuần tự, một kết nối; `RateLimiter`: giãn cách tối thiểu và cửa sổ trượt.
- `normalize_reference(item, index)`: chuyển một phần tử API thành bản ghi phẳng.

### `src/storage.py`
- `write_json_atomic`, `write_jsonl_atomic`: ghi qua file tạm cùng thư mục rồi `os.replace`; lỗi giữa chừng không để lại file hỏng và không đè dữ liệu cũ. `read_json`: file thiếu hoặc hỏng được coi như chưa có.
- `paper_dirname`, `paper_dir`, `references_cache_path`: ánh xạ ID → thư mục bằng đúng `safe_paper_dirname` của Downloader (`2410.13025` → `2410-13025`, `hep-th/9901001` → `hep-th-9901001`).
- `load_references_cache`, `save_references_cache`: chỉ lưu kết quả **chắc chắn** (`success`, `empty_references`, `not_found`); kết quả `failed` không bao giờ được lưu để không đè dữ liệu tốt của lần chạy trước.
- `build_papers_rows`, `build_references_rows`, `export_tables`: xuất hai bảng theo đặc tả Milestone 1.
- `write_parquet_shards`, `export_parquet`: xuất hai bảng dạng Parquet chia shard (cần `pyarrow`); `src/hf_upload.py` upload lên Hugging Face (cần `huggingface_hub`).

---

## 4. Schema

### 4.1 Cache từng bài `data/corpus/<safe_id>/references.json`

`schema_version`, `arxiv_id`, `queried_id`, `source`, `granularity` (`publication_level`), `version_note`, `status`, `http_status`, `requests`, `pages`, `fetched_at`, `duration_seconds`, `error_type`, `error`, `counts` (`total`, `with_arxiv_id`), `references`, `raw_pages`.

Mỗi phần tử `references` (giữ **toàn bộ**, kể cả reference không có arXiv ID):

| Trường | Ý nghĩa |
| :--- | :--- |
| `index` | thứ tự trong phản hồi API |
| `s2_id` | `paperId` của Semantic Scholar (cột `ref_s2_id`) |
| `title`, `authors` (list), `year`, `venue`, `doi` | metadata; giá trị không biết là `None` hoặc `[]`, không đoán |
| `publication_date` | `publicationDate` của Semantic Scholar, **không phải** ngày nộp arXiv |
| `arxiv_id` | ID arXiv chuẩn hóa (bỏ hậu tố version, giữ dạng cũ `hep-th/9901001`); `None` nếu không có |
| `arxiv_id_raw` | giá trị gốc API trả về |
| `submission_date` | bằng `publicationDate` của Semantic Scholar (giảng viên cho phép dùng cho `ref_submission_date`); `None` nếu API không có `publicationDate`, không bao giờ suy ra từ `year` |
| `external_ids` | toàn bộ `externalIds` |

`raw_pages` giữ nguyên các trang phản hồi thô để kiểm tra lại.

### 4.2 Trạng thái từng bài

| `status` | Ý nghĩa | Lưu cache? |
| :--- | :--- | :---: |
| `success` | API trả lời, có ít nhất 1 reference | có |
| `empty_references` | API trả lời, bài được biết nhưng 0 reference | có |
| `not_found` | HTTP 404 | có |
| `failed` | lỗi mạng, 429/5xx không hết sau số lần thử, JSON hoặc cấu trúc phản hồi sai | không |
| `not_crawled` | chưa được hỏi (chỉ xuất hiện ở báo cáo của `metrics.py`) | — |

### 4.3 Báo cáo chạy `data/state/references.json`

Gốc: `schema_version`, `started_at`, `source`, `api`, `granularity`, `authenticated`, `complete` (`false` nếu bị ngắt hoặc dừng do lỗi khóa API), `papers`, `summary`, `elapsed_seconds`.

Mỗi phần tử `papers`: `arxiv_id`, `safe_id`, `status`, `total_references_count`, `arxiv_references_count`, `pages`, `requests`, `http_status`, `cache_hit`, `fetched_at`, `duration_seconds` (thời gian gọi API lúc lấy dữ liệu, được giữ khi resume), `run_seconds` (thời gian của lần chạy hiện tại), `references_path`, `error_type`, `error`.

`summary`: `assigned_papers`, `queried_papers`, `not_crawled_papers`, `successful_api_queries`, `papers_with_references`, `empty_references_papers`, `not_found_papers`, `failed_queries`, `cache_hits`, `requests_this_run`, `reference_scrape_success_rate`, `total_references_crawled`, `total_arxiv_references_crawled`, các trung bình `avg_*_per_queried_paper` (chia cho mọi bài đã hỏi) và `avg_*_per_successful_paper` (chia cho bài `success` + `empty_references`), `field_coverage_rates` (`arxiv_id`, `submission_date`, `s2_paper_id`, `title`, `authors`, `publication_date`, `doi`), `aborted`.

### 4.4 Hai bảng cuối `data/export/*.jsonl`

- `papers`: `arxiv_id`, `title`, `authors`, `submission_date`, `revised_dates`, `venue` (từ `discovery.json`; chỉ bài `resolved`).
- `references`: `arxiv_id` (bài trích dẫn), `ref_arxiv_id`, `ref_title`, `ref_authors`, `ref_submission_date`, `ref_s2_id`. **Chỉ** reference có arXiv ID, mỗi cặp (bài, reference) một dòng, không nhân bản theo version.

Định dạng JSON Lines không cần thêm thư viện; Milestone 3 sẽ chuyển sang Parquet phân mảnh.

---

## 5. Phương pháp kỹ thuật và Hạn chế (dành cho Thành viên 5 tổng hợp báo cáo)

1. **Endpoint và phân trang.** Dùng `GET /graph/v1/paper/arXiv:{id}/references` với `offset`, `limit` (mặc định 500, tối đa 1000) và đi theo trường `next` cho tới hết, nên danh sách dài không bị cắt âm thầm. Nếu một trang giữa chừng lỗi, cả bài là `failed` (không lưu bản thiếu).
2. **Giới hạn tốc độ.** Khoảng cách tối thiểu `s2_delay_seconds` giữa hai request: **3,1 giây** khi không có khóa (an toàn dưới 100 request/5 phút), **1,0 giây** khi có `SEMANTIC_SCHOLAR_API_KEY`. Khi không có khóa còn có cửa sổ trượt 95 request/300 giây. Mọi request, kể cả bị 429, đều được tính.
3. **Thử lại.** 429, 5xx và lỗi mạng: tối đa `--attempts` lần, backoff lũy thừa và tôn trọng `Retry-After` (tối đa 300 giây). HTTP 401/403 dừng cả lượt chạy ngay (khóa sai), báo `aborted`, thoát với mã 1.
4. **Ước lượng thời gian** (giả định ~1 request/bài, chưa đo thực tế): 5.000 bài ≈ **4,3 giờ** không có khóa, ≈ **1,4 giờ** có khóa. Bài có trên 500 reference cần thêm trang. Nên xin khóa miễn phí trước khi chạy.
5. **Không phân biệt version.** Dữ liệu là cấp bài. Semantic Scholar có thể chưa lập chỉ mục bài mới trong 1–7 ngày (khi đó `not_found` hoặc `empty_references`); dùng `--retry-not-found` để hỏi lại.
6. **`ref_submission_date` là ngày công bố của Semantic Scholar, không phải ngày nộp arXiv.** Giảng viên cho phép dùng `publication_date` của S2 cho cột này (hoặc tra ngược theo arXiv ID, chưa triển khai). Có thể lệch vài ngày hoặc vài tháng so với ngày nộp thật, và để trống khi API không có `publicationDate`; bài báo cáo nên nêu rõ hạn chế này.
7. **ID kiểu cũ** (`hep-th/9901001`) được giữ nguyên ở reference. Việc truy vấn chính các bài có ID kiểu cũ chưa được kiểm thử với API thật (dải phân công hiện nay chỉ có ID mới).
8. **Độ tin cậy dữ liệu.** Semantic Scholar tự trích xuất reference nên có thể thiếu hoặc nhầm; ghi rõ trong báo cáo. Tỷ lệ reference có arXiv ID phụ thuộc lĩnh vực.
9. **Bảo mật.** Khóa API chỉ đọc từ `.env`/biến môi trường, không ghi vào file dữ liệu hay log, và bị ẩn khỏi `repr` của `CrawlConfig`.

## 6. Tích hợp với `metrics.py`

`src/metrics.py` đọc `state/references.json` với các trạng thái `success` / `empty_references` / `not_found` / `failed` và trường `total_references_count`, `arxiv_references_count`, `duration_seconds` — đã khớp với báo cáo này (có test `test_state_report_is_understood_by_the_metrics_module`). Lưu ý `avg_references_per_queried_paper` trong `benchmark_summary.json` do `metrics.py` chia cho số bài hỏi **thành công**, khác với cùng tên trong `summary` của `references.json` (chia cho mọi bài đã hỏi); dùng `avg_*_per_successful_paper` nếu muốn khớp.

Tỷ lệ phủ trường (`title`, `authors`, `s2_paper_id`) trong `benchmark_summary.json` chỉ đúng khi `metrics.py` đọc `summary.field_coverage_rates` của báo cáo này; xem patch đi kèm `metrics-use-references-summary.patch`.
