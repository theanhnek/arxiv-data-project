# Bàn giao phần thành viên 5 — Metrics, Colab Benchmark & Báo cáo

## 1. Vị trí trong Pipeline

Module `src/metrics.py` đảm nhận hai nhiệm vụ chính của Thành viên 5:
1. **Đo tài nguyên hệ thống theo thời gian thực (`ResourceMonitor`)**:
   - Sử dụng `psutil` trên luồng nền (daemon thread) lấy mẫu định kỳ (mặc định mỗi `0.5s`) bộ nhớ RAM (`RSS` của tiến trình chính cộng toàn bộ tiến trình con) và dung lượng ổ đĩa của thư mục đầu ra (`data/`).
   - Ghi nhận: `peak_ram_mb`, `avg_ram_mb`, `peak_working_disk_mb` (bao gồm cả file nén tạm trong `data/tmp/`) và `final_corpus_disk_mb` (dung lượng `.tex`/`.bib` cuối cùng trong `data/corpus/`).
2. **Tổng hợp toàn bộ thống kê Corpus & Hiệu năng (`compute_pipeline_metrics`)**:
   - Đọc kết quả từ `data/state/input_validation.json`, `data/state/discovery.json`, `data/state/downloads.json`, `data/state/extractions.json` và (nếu đã có) `data/state/references.json`.
   - Hoạt động độc lập kể cả khi Bước 4 (`references.json` của Thành viên 4) chưa chạy xong (tự động ghi nhận `references.available = false`, và tự động cập nhật khi có dữ liệu của Thành viên 4).
   - Xuất hai file JSON chuẩn tại `data/metrics/benchmark_summary.json` và `data/metrics/per_paper_metrics.json` (không chứa thông tin nhạy cảm) để đưa sang **Jupyter Book** tự động vẽ bảng biểu và đồ thị khi build.

---

## 2. Hướng dẫn chạy trên PowerShell

Chạy từ thư mục gốc `D:\arxiv-data-project`:

### 2.1 Tính toán thống kê từ dữ liệu đã chạy sẵn trong `data/state/`

```powershell
D:\anaconda\python.exe -m src.main --metrics --output data
```

Hoặc gọi trực tiếp module `src.metrics`:

```powershell
D:\anaconda\python.exe -m src.metrics --output data
```

### 2.2 Chạy gộp toàn tuyến kèm theo dõi RAM/Disk từ đầu đến cuối

Chạy thử với file danh sách ID (`data/test_ids.txt`) hoặc khoảng phân công của Thành viên 5 (`--range 2025-09 11047 2025-09 16046`):

```powershell
# Chạy trên danh sách ID mẫu kèm đo RAM/Disk xuyên suốt
D:\anaconda\python.exe -m src.main --discover --download --extract --metrics --ids data/test_ids.txt --output data --resume

# Chạy trên khoảng phân công của Thành viên 5 (2509.11047 -> 2509.16046)
D:\anaconda\python.exe -m src.main --discover --download --extract --metrics --range 2025-09 11047 2025-09 16046 --output data --resume
```

### 2.3 Chạy kiểm tra tự động (offline)

```powershell
D:\anaconda\python.exe -m pytest tests/test_metrics.py -v --basetemp data/test-suite
```

---

## 3. Các chỉ số được tính trong `data/metrics/benchmark_summary.json`

1. **`discovery`**:
   - `assigned_unique_ids`, `resolved_papers`, `failed_papers`, `paper_discovery_success_rate`
   - `discovered_versions`, `multi_version_papers`, `avg_versions_per_resolved_paper`, `elapsed_seconds`
2. **`download_and_extraction`**:
   - `downloaded_versions`, `version_download_success_rate`
   - `extracted_tex_versions` (`status == "success"`), `no_tex_source_versions` (`status == "no_tex_source"`)
   - `version_completion_rate`, `tex_source_coverage_rate`, `full_paper_completion_count`, `full_paper_completion_rate`
   - `total_extracted_files`, `total_tex_files`, `total_bib_files`
3. **`sizes_bytes` (Trước và sau khi loại bỏ hình ảnh)**:
   - `total_compressed_download_bytes`: Tổng dung lượng các gói `.tar.gz` tải về.
   - `total_uncompressed_archive_bytes_before_filtering`: Tổng dung lượng giải nén trước khi lọc bỏ hình ảnh.
   - `total_retained_tex_bib_bytes_after_filtering`: Tổng dung lượng `.tex`/`.bib` giữ lại trong corpus.
   - `figure_removal_savings_ratio`: Tỷ lệ dung lượng tiết kiệm được nhờ loại bỏ hình ảnh và file binary.
   - Kích thước trung bình tính theo từng **version** (`avg_*_per_version`) và theo từng **paper** (`avg_*_per_paper` — cộng tất cả các version của mỗi bài trước khi chia trung bình).
4. **`references`**:
   - `available`, `queried_papers`, `successful_api_queries`, `reference_scrape_success_rate`
   - `total_references_crawled`, `total_arxiv_references_crawled`, `avg_references_per_queried_paper`, `avg_arxiv_references_per_queried_paper`
   - `field_coverage_rates` (`arxiv_id`, `submission_date`, `s2_paper_id`, `title`, `authors`)
5. **`performance`**:
   - Thời gian chạy từng bước (`discovery_elapsed_seconds`, `download_elapsed_seconds`, `extraction_elapsed_seconds`, `references_elapsed_seconds`) và tổng thời gian toàn cục (`end_to_end_wall_time_seconds`).
   - Thời gian xử lý trung bình mỗi bài báo (`avg_time_per_assigned_paper_seconds`, `avg_time_per_resolved_paper_seconds`).
   - Tài nguyên đo được (`resources`): `peak_ram_mb`, `avg_ram_mb`, `peak_working_disk_mb`, `final_output_disk_mb`, `final_corpus_disk_mb`.
