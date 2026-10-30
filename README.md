# arXiv Data Project

Thu thập và xử lý dữ liệu bài báo arXiv cho đồ án Nhập môn Khoa học Dữ liệu. Dự án hướng đến xây dựng corpus LaTeX theo từng phiên bản, chuẩn hóa cấu trúc tài liệu và phát triển hệ thống reference matching qua ba milestone.

## Phạm vi triển khai

Milestone 1 thu thập metadata, source LaTeX và reference metadata của các bài báo được phân công. Corpus này là đầu vào cho bước parsing ở Milestone 2 và reference matching ở Milestone 3.

Hiện CLI hỗ trợ đọc danh sách ID, lấy metadata từ arXiv, xác định toàn bộ phiên bản và lưu kết quả discovery để tiếp tục khi bị ngắt. Các bước download, extraction, references và đo hiệu năng chưa được tích hợp thành một lệnh chạy toàn bộ pipeline.

## Yêu cầu môi trường

- Python 3.11.
- Kết nối Internet để truy cập arXiv API.
- Các thư viện trong `src/requirements.txt`.

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

CLI discovery đọc biến này khi khởi động. `SEMANTIC_SCHOLAR_API_KEY` được dành cho module references; discovery không sử dụng biến đó. File `.env` không được đưa vào Git.

## Sử dụng

### Khởi tạo thư mục dữ liệu

```powershell
.\.venv\Scripts\python.exe -m src.main --init --output data
```

### Chuẩn bị danh sách bài báo

Tạo `data/assigned_ids.txt` dưới dạng UTF-8, mỗi dòng một arXiv ID. Ví dụ thử nghiệm:

```text
# Sample IDs; replace with the assigned list before the full crawl.
1706.03762
1810.04805
```

Bộ đọc hỗ trợ ID mới, ID cũ như `hep-th/9901001`, suffix phiên bản như `1706.03762v2`, dòng trống và comment bắt đầu bằng `#`. ID có suffix được quy về publication để tìm tất cả phiên bản. ID trùng được loại bỏ; dòng sai định dạng được ghi lại. Đầu vào hiện là danh sách ID cụ thể, chưa hỗ trợ CSV, URL hoặc tự mở rộng khoảng ID.

### Chạy discovery

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --output data
```

Discovery lấy metadata và tạo danh sách phiên bản từ `v1` đến phiên bản mới nhất. Lệnh này chưa tải source `.tex` hoặc `.bib`.

Để dùng lại kết quả thành công đã lưu và thử lại các bài lỗi:

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --output data --resume
```

Resume sử dụng snapshot cũ. Bỏ `--resume` khi cần cập nhật metadata hoặc kiểm tra phiên bản mới.

Có thể cấu hình timeout mỗi request và số lần thử tối đa:

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --timeout 60 --attempts 3
```

Xem toàn bộ tùy chọn:

```powershell
.\.venv\Scripts\python.exe -m src.main --help
```

### Đọc kết quả

- `data/state/discovery.json`: metadata, trạng thái từng bài, danh sách phiên bản và thống kê lần chạy.
- `data/state/input_validation.json`: danh sách ID hợp lệ, ID trùng và các dòng không hợp lệ.

Trong `discovery.json`, mỗi bài resolve thành công có `status: resolved`. Trường `papers[].versions[]` cung cấp `version_id` và `source_url` cho bước download. Các trạng thái download/extraction ban đầu là `pending`.

Mã thoát `0` cho biết discovery hoàn tất với input hợp lệ và không có bài resolve lỗi. Mã `1` cho biết còn input không hợp lệ hoặc có bài thất bại; kết quả của các bài thành công vẫn được lưu. Lỗi tham số hoặc không có ID hợp lệ dùng mã `2`.

## Cấu trúc repository

```text
arxiv-data-project/
├── src/
│   ├── main.py              # CLI
│   ├── config.py            # Cấu hình dùng chung
│   ├── discovery.py         # Resolve metadata và liệt kê phiên bản
│   ├── downloader.py        # Module tải source
│   ├── extractor.py         # Module trích xuất source
│   ├── references.py        # Module reference metadata
│   ├── storage.py           # Module lưu dữ liệu
│   ├── metrics.py           # Module đo hiệu năng
│   └── requirements.txt
├── tests/                   # Kiểm tra tự động
├── docs/
│   └── discovery.md         # Giao diện và schema discovery
├── .env.example
├── .gitignore
├── plan.md                  # Kế hoạch triển khai
└── README.md
```

Thư mục `data/` được tạo cục bộ khi chạy và được loại khỏi Git, cùng với môi trường ảo, thông tin xác thực và output sinh ra.

## Kiểm tra

Chạy các kiểm tra discovery offline, không gửi request đến arXiv:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery.py -v --basetemp data/test-discovery
```

Các trường hợp được kiểm tra gồm validation và loại trùng ID, liệt kê phiên bản, response không khớp ID và phục hồi sau lỗi từng phần. Test offline không thay thế việc xác nhận kết nối API thật.

## Schema và giới hạn hiện tại

Đặc tả đầu ra và giao diện tích hợp được mô tả trong [docs/discovery.md](docs/discovery.md).

- Metadata được lấy ở mức publication theo phiên bản mới nhất, chưa phải metadata riêng cho mỗi revision.
- `latest_updated_date` là thời điểm cập nhật mới nhất. `revised_dates` chưa có lịch sử đầy đủ khi `revision_dates_complete` là `false`.
- `venue` hiện lấy từ journal reference của arXiv và chưa được chuẩn hóa thành tên venue riêng.
- Discovery sử dụng request tuần tự với khoảng cách ít nhất 3 giây. Khi tích hợp downloader cần phối hợp pacing giữa các request arXiv.

## Báo cáo và dữ liệu

Báo cáo Milestone 1–2 được xây dựng bằng Jupyter Book trong một repository public riêng và triển khai trên GitHub Pages theo yêu cầu đồ án. Đường dẫn báo cáo sẽ được bổ sung sau khi triển khai.

Corpus được nhóm lưu để sử dụng cho các milestone sau. Milestone 1 nộp link code và báo cáo; dataset hoàn chỉnh được xuất bản trên Hugging Face ở giai đoạn cuối dự án.

## Tài liệu tham khảo

- [arXiv API User Manual](https://info.arxiv.org/help/api/user-manual.html)
- [arXiv API Terms of Use](https://info.arxiv.org/help/api/tou.html)
- [Project Problem Statements](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/)
