# Kế hoạch Thực hiện Đồ án — Milestone 1: Data Crawling

Tài liệu này mô tả kế hoạch triển khai, phân công công việc và quy trình kiểm soát chất lượng của nhóm trong việc xây dựng pipeline thu thập dữ liệu bài báo khoa học từ arXiv và Semantic Scholar (Milestone 1 — Môn học Nhập môn Khoa học Dữ liệu).

---

## 1. Mục tiêu và Phạm vi dự án

- **Xây dựng pipeline tự động hóa**: Thu thập toàn diện dữ liệu bài báo khoa học trong dải được phân công (gồm metadata, toàn bộ các phiên bản mã nguồn LaTeX và danh mục trích dẫn).
- **Tối ưu hóa không gian lưu trữ**: Giải nén và lọc chỉ giữ lại file `.tex` và `.bib`, loại bỏ hoàn toàn các file ảnh và binary để tiết kiệm dung lượng đĩa (>90%), đáp ứng giới hạn tài nguyên của Google Colab CPU-only.
- **Tính ổn định và khả năng phục hồi**: Quản lý trạng thái từng bước qua các file `state/*.json`, hỗ trợ cờ `--resume` để tiếp tục chạy an toàn khi gặp sự cố mạng hoặc gián đoạn phiên làm việc.
- **Sản phẩm bàn giao**:
  1. Repository mã nguồn GitHub sạch, có kiểm thử tự động, tài liệu hướng dẫn rõ ràng.
  2. Repository báo cáo Jupyter Book riêng biệt, triển khai trên GitHub Pages, các bảng biểu và đồ thị hiệu năng được sinh tự động bằng mã Python.

---

## 2. Phân công nhiệm vụ (Nhóm 5 thành viên)

| Thành viên | Trách nhiệm chính | Module & File đảm nhận | Tiêu chí hoàn thành |
| :--- | :--- | :--- | :--- |
| **Thành viên 1** | Trưởng nhóm & Tích hợp | `src/discovery.py`<br>`src/main.py`<br>`src/config.py` | - Xử lý dải ID và danh sách phân công.<br>- Gọi arXiv API lấy metadata & danh sách phiên bản `v1..vN`.<br>- Tích hợp luồng chạy toàn tuyến `--all` và CLI. |
| **Thành viên 2** | Tải mã nguồn | `src/downloader.py` | - Tải gói source từ mirror `export.arxiv.org/src/{version_id}`.<br>- Kiểm soát rate limit (giãn cách $\ge$ 3s), retry với backoff.<br>- Tính mã băm SHA-256 xác thực file tải về. |
| **Thành viên 3** | Giải nén & Làm sạch | `src/extractor.py` | - Giải nén an toàn (chống path traversal/zip slip).<br>- Chỉ giữ lại file `.tex` và `.bib`, loại bỏ toàn bộ file ảnh.<br>- Đo lường dung lượng trước/sau khi lọc. |
| **Thành viên 4** | Trích dẫn & Xuất dữ liệu | `src/references.py`<br>`src/storage.py` | - Gọi Semantic Scholar API lấy danh mục trích dẫn.<br>- Điều tiết tốc độ gọi API và cơ chế cache cục bộ.<br>- Xuất 2 bảng dữ liệu chuẩn: `papers.jsonl` và `references.jsonl`. |
| **Thành viên 5** | Đo lường & Báo cáo | `src/metrics.py`<br>`colab_benchmark.ipynb`<br>Repo Jupyter Book | - Giám sát tài nguyên hệ thống (RAM, Disk, CPU, thời gian).<br>- Xây dựng notebook benchmark trên Google Colab CPU-only.<br>- Thiết lập Jupyter Book và cấu hình deploy GitHub Pages. |

---

## 3. Các giai đoạn triển khai (Timeline)

### Giai đoạn 1: Chuẩn bị môi trường & Kiến trúc dữ liệu
- Khởi tạo repository Git, cấu hình `.gitignore` chặn các file dữ liệu nặng (`data/`) và thông tin bảo mật (`.env`).
- Thiết lập môi trường ảo Python 3.11, file `requirements.txt` và file `.env.example`.
- Thống nhất quy ước lưu trữ dữ liệu cục bộ (`data/corpus/`, `data/state/`, `data/export/`, `data/metrics/`).

### Giai đoạn 2: Phát triển các module độc lập
- **Discovery**: Truy vấn arXiv Query API theo lô (batch), phân tích XML và bóc tách hậu tố version.
- **Downloader**: Viết HTTP client chuyên dụng kèm User-Agent định danh nhóm, cơ chế retry khi gặp lỗi 429/503.
- **Extractor**: Xử lý các định dạng lưu trữ `.tar.gz`, trích xuất file mã nguồn và loại bỏ file media.
- **References**: Kết nối Semantic Scholar Graph API, bóc tách các bài trích dẫn có arXiv ID.
- **Metrics**: Thiết lập lớp `ResourceMonitor` nền đo đạc RAM (qua `psutil`), kích thước thư mục và thời gian chạy.

### Giai đoạn 3: Tích hợp hệ thống & Cơ chế Resume
- Kết nối toàn bộ 5 bước vào một lệnh duy nhất (`--all`) trong `src/main.py`.
- Lưu trạng thái độc lập tại từng bước: `discovery.json`, `downloads.json`, `extractions.json`, `references.json`.
- Kiểm thử cơ chế tiếp tục chạy (`--resume`) khi quy trình bị ngắt đột ngột.

### Giai đoạn 4: Kiểm thử tự động & Chạy thử nghiệm
- Xây dựng bộ test tự động trong thư mục `tests/` (sử dụng dữ liệu giả lập offline, không gọi request thật).
- Chạy smoke test trên dải mẫu nhỏ (2-5 bài) trên máy local để kiểm tra toàn bộ luồng dữ liệu.
- Chạy thử nghiệm trên Google Colab (CPU-only) với notebook `src/colab_benchmark.ipynb`, kiểm tra tính ổn định trong giới hạn tài nguyên Colab.

### Giai đoạn 5: Hoàn thiện báo cáo & Bàn giao
- Xuất file số liệu tổng hợp `benchmark_summary.json` và `per_paper_metrics.json`.
- Xây dựng nội dung báo cáo trong repository Jupyter Book (`arxiv-data-report`):
  + Phương pháp luận (Methodology).
  + Thống kê và đồ thị hiệu năng sinh tự động (Statistics & Performance).
  + Đóng góp của từng thành viên.
- Cấu hình GitHub Actions tự động build sách và xuất bản lên GitHub Pages.
- Kiểm tra toàn diện đường dẫn, tài liệu README và nộp bài trên Moodle đúng thời hạn.

---

## 4. Quản lý rủi ro và Quy chuẩn kỹ thuật

1. **Tuân thủ quy định API (Rate Limiting)**:
   - arXiv API / Mirror: Luôn giữ khoảng cách ít nhất 3 giây giữa hai request liên tiếp, chỉ sử dụng 1 kết nối duy nhất tại một thời điểm.
   - Semantic Scholar API: Tự động điều tiết tần suất 1 req/s (nếu có API key) hoặc 1 req/3.1s (không có key) để tránh lỗi 429.
2. **An toàn dữ liệu & Git Policy**:
   - Tuyệt đối không commit file `.env`, API key cá nhân, hoặc dữ liệu tải về (`data/`) lên Git.
   - Kiểm tra tính toàn vẹn của file tải về qua mã băm SHA-256.
3. **Giới hạn tài nguyên trên Google Colab**:
   - Sử dụng cơ chế streaming khi tải và giải nén theo từng bài/version để tránh chiếm dụng RAM và Disk đột biến.
   - Giải phóng bộ nhớ kịp thời, đảm bảo mức tiêu thụ RAM luôn dưới mức an toàn (< 500 MB).
