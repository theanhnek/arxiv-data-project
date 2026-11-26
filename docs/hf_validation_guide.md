## 1. Bảng khoảng ID chính thức của Nhóm 14

| Tham số `--member` | MSSV | Thành viên | Khoảng phân công chính thức | Dải arXiv ID (`YYMM.NNNNN`) |
| :---: | :--- | :--- | :--- | :--- |
| `Kiet` (hoặc `1`) | `22120171` | Lê Văn Tuấn Kiệt | `2025-10 14401` $\rightarrow$ `2025-10 19400` | `2510.14401` $\rightarrow$ `2510.19400` (5.000 ID) |
| `Duong` (hoặc `2`) | `23120033` | Huỳnh Võ Đại Dương | `2025-10 9401` $\rightarrow$ `2025-10 14400` | `2510.09401` $\rightarrow$ `2510.14400` (5.000 ID) |
| `Ha` (hoặc `3`) | `23120037` | Nguyễn Thanh Khánh Hà | `2025-10 4401` $\rightarrow$ `2025-10 9400` | `2510.04401` $\rightarrow$ `2510.09400` (5.000 ID) |
| `Kien` (hoặc `4`) | `23120055` | Đào Trung Kiên | `2025-09 26047` $\rightarrow$ `2025-10 4400` | `2509.26047`–`2509.26646` (600 ID) + `2510.00001`–`2510.04400` (4.400 ID) |
| `Anh` (hoặc `5`) | `23120210` | Nguyễn Hoàng Thế Anh | `2025-09 11047` $\rightarrow$ `2025-09 16046` | `2509.11047` $\rightarrow$ `2509.16046` (5.000 ID) |

---

## 2. Script `scripts/validate_hf_dataset.py` kiểm tra những gì?

Trước khi upload thư mục `data/export/parquet` lên Hugging Face, script tự động kiểm tra **7 nhóm tiêu chí**:

1. **Dataset Card (`README.md`)**: Kiểm tra tồn tại `README.md`, YAML frontmatter `configs:` khai báo đúng từng bảng trỏ tới `data/<table>/*.parquet`, và cảnh báo nếu còn placeholder mẫu `{{...}}` chưa điền.
2. **Định dạng & Schema Parquet phẳng**: Kiểm tra đọc được toàn bộ các shard `*.parquet` bằng `pyarrow`, đúng tên cột và kiểu dữ liệu chuẩn (`string`, `list<string>`, `int`), cấm cột kiểu nested `struct`/`map`.
3. **Số dòng & Giá trị thiếu (`null` / rỗng)**: Thống kê số dòng, số shard, số giá trị `null`/rỗng và tỷ lệ thiếu (%) từng cột; báo lỗi ngay nếu các cột bắt buộc `non-null` (`arxiv_id`, `ref_arxiv_id`,...) bị thiếu.
4. **Kiểm tra dải ID chính thức**: Đảm bảo 100% `arxiv_id` trong tất cả các bảng đều là base ID (không kèm hậu tố `v1`/`v2`) và nằm gọn trong đúng dải 5.000 ID của thành viên đang nộp (tránh lẫn dữ liệu của thành viên khác).
5. **Kiểm tra trùng lặp khóa chính**:
   - `papers`: không trùng `arxiv_id`.
   - `references`: không trùng cặp `(arxiv_id, ref_arxiv_id)`.
   - Hỗ trợ tự động kiểm tra luôn 4 bảng Milestone 2–3 (`elements`, `hierarchy`, `bibtex`, `predictions`) khi các thư mục đó xuất hiện dưới `data/`.
6. **Toàn vẹn khóa ngoại giữa các bảng**: Mọi `arxiv_id` xuất hiện trong `references` (và các bảng con khác) đều phải có mặt trong `papers`. Riêng `predictions` kiểm tra thêm không có bài báo nào bị chia vào 2 partition khác nhau.
7. **Đọc lại bằng Hugging Face `datasets.load_dataset(...)`**: Gọi trực tiếp `load_dataset(<dataset_dir>, name=<table>, split="train")` để xác nhận dữ liệu tương thích 100% với Hugging Face Hub Viewer và người chấm.

---

## 3. Hướng dẫn chạy

### Cài đặt thư viện cần thiết (nếu chưa cài)
```powershell
pip install -r src/requirements.txt
```

### Bước 1: Xuất Parquet chia shard
```powershell
python -m src.main --export-parquet --output data
```

### Bước 2: Chạy kiểm tra trước khi nộp 
```powershell
# Ví dụ
python scripts/validate_hf_dataset.py --dataset-dir data/export/parquet --member <Kiet|Duong|Ha|Kien|Anh> --json-report data/metrics/hf_validation_report.json
```

Các tùy chọn hữu ích khác:
```powershell
# Kiểm tra với dải mẫu nhỏ tùy chỉnh (khi chạy pilot vài bài)
python scripts/validate_hf_dataset.py --dataset-dir data/export/parquet --range 2025-09 26047 2025-09 26051

# Kiểm tra bắt buộc đủ cả 6 bảng (sau khi hoàn thiện Milestone 2 & 3)
python scripts/validate_hf_dataset.py --dataset-dir data/export/parquet --member <Kiet|Duong|Ha|Kien|Anh> --all-six-tables

# Bỏ qua bước gọi datasets.load_dataset nếu máy chưa cài thư viện datasets
python scripts/validate_hf_dataset.py --dataset-dir data/export/parquet --member <Kiet|Duong|Ha|Kien|Anh> --skip-load-dataset
```

### Bước 3: Xem kết quả và Upload
- Nếu dòng cuối in **`RESULT: PASS — Ready for Hugging Face upload!`** (exit code `0`), dữ liệu đã đạt chuẩn $\rightarrow$ tiến hành chạy:
  ```powershell
  python -m src.hf_upload --repo-id <HF_USERNAME>/<TEN-DATASET> --output data
  ```
- Nếu in **`RESULT: FAIL`** (exit code `1`), xử lý toàn bộ các dòng `[ERROR]` được liệt kê rồi chạy kiểm tra lại trước khi upload.

---

## 4. Chạy kiểm thử tự động (Unit Tests)

```powershell
python -m pytest tests/test_validate_hf_dataset.py -v --basetemp data/test-suite
```
