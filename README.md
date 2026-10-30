# arXiv Data Project — Milestone 1

Repo code chung cho các milestone. Báo cáo Jupyter Book nằm ở một repo public khác do nhóm tạo riêng.

**Trạng thái:** phần discovery của thành viên 1 đã triển khai: đọc ID, resolve metadata, enumerate mọi version và lưu snapshot để resume. Lệnh CLI chưa nối downloader, extraction hoặc references thành pipeline đầy đủ.

## Chạy discovery (thành viên 1)

Tạo file `data/assigned_ids.txt` theo danh sách giảng viên giao, mỗi dòng một ID. Hỗ trợ ID cũ như `hep-th/9901001`, suffix version, dòng trống và comment bắt đầu bằng `#`. Không tự mở rộng một khoảng số khi chưa xác nhận quy tắc từ giảng viên.

```powershell
.\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt
.\.venv\Scripts\python.exe -m src.main --discover --ids data/assigned_ids.txt --resume
```

Kết quả: `data/state/discovery.json`; lỗi input và ID trùng: `data/state/input_validation.json`. Mã thoát 1 nghĩa là còn input không hợp lệ hoặc bài resolve lỗi; kết quả các bài khác vẫn được lưu. `--resume` tái sử dụng snapshot thành công và thử lại bài lỗi; bỏ `--resume` để kiểm tra revision mới.

`papers[].versions` là danh sách công việc cho downloader. Discovery chỉ ghi trạng thái pending, không tuyên bố đã tải source. `latest_updated_date` là ngày cập nhật mới nhất; `revised_dates` chưa đủ lịch sử nếu `revision_dates_complete=false`. Phần tích hợp cần bổ sung lịch sử ngày revision từ nguồn thích hợp; không suy đoán ngày còn thiếu.

Đặc tả bàn giao: [docs/discovery.md](docs/discovery.md).

Kiểm tra offline (không gọi API):

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp data/test-run
```

## 1. Cài môi trường

Đề xuất dùng Python 3.11. Mở PowerShell tại thư mục repo:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r src/requirements.txt
Copy-Item .env.example .env
```

Sửa thông tin liên hệ trong `.env`; thêm Semantic Scholar API key nếu có. Khi triển khai HTTP client, thành viên phụ trách phải đọc các biến này; khung hiện tại chưa dùng chúng.

Không cần activate môi trường: có thể gọi trực tiếp Python trong `.venv` như các lệnh bên dưới.

## 2. Kiểm tra khung dự án

Chạy từ thư mục gốc, không chạy từ bên trong `src`:

```powershell
.\.venv\Scripts\python.exe -m src.main --help
.\.venv\Scripts\python.exe -m src.main --init --output data
```

Lệnh `--init` chỉ tạo `data/corpus`, `data/state`, `data/metrics`, `data/tmp`. Pipeline tải bài sẽ được nhóm nối vào `src/main.py` sau.

## 3. Cấu trúc và phân công

`src` là thư mục chứa mã nguồn Python. Mỗi người làm module được giao rồi ghép qua `main.py`.

- Người 1: `discovery.py`, điều phối `main.py` và schema chung.
- Người 2: `downloader.py`, pacing, retry và resume download.
- Người 3: `extractor.py`, nhận diện định dạng, giải nén an toàn, giữ `.tex`/`.bib`.
- Người 4: `references.py`, phối hợp `storage.py` để lưu và chuẩn hóa metadata.
- Người 5: `metrics.py`, kiểm tra đầu ra, benchmark Colab và repo báo cáo riêng.
- Chung: `config.py` chứa cấu hình; `requirements.txt` chứa dependencies. Mỗi người bổ sung dependency mình dùng và cùng cố định phiên bản sau khi kiểm tra.

## 4. Những yêu cầu không được bỏ sót

- Nhận danh sách ID được giao và thu thập tất cả phiên bản `v1..vN`.
- Tải từng phiên bản từ mirror `export.arxiv.org`; dùng User-Agent có thông tin liên hệ và pacing theo đề.
- Chỉ trích xuất `.tex` và `.bib`, giữ cấu trúc đường dẫn tương đối và nội dung gốc. Không xóa lệnh hình ảnh trong TeX.
- Không có TeX vẫn giữ record của phiên bản đó. Phân biệt `no_tex_source` và `failed`.
- Giữ toàn bộ references nội bộ; khi xuất bảng cuối chỉ đưa references có arXiv ID vào bảng `references`.
- Không giả định Semantic Scholar phân biệt references theo version. Ghi lại nguồn và giới hạn của dữ liệu.
- Đo runtime, RAM và disk trong toàn pipeline. Benchmark cuối trên Colab CPU-only.
- Không commit corpus, archive, credentials, log hoặc output sinh ra vào repo code.

## 5. Hợp đồng dữ liệu cần chốt trước khi code

Nhóm cần thống nhất schema chi tiết trước khi triển khai các module:

- Paper: `arxiv_id` gốc, title, authors, submission date, revised dates, venue và danh sách versions.
- Version: ID có suffix version, trạng thái, đường dẫn source, lỗi nếu có và metrics.
- Reference: metadata thô từ API và các trường chuẩn hóa; giá trị không biết phải thể hiện là thiếu, không tự suy đoán.
- State: trạng thái riêng cho download, extraction và references để chạy lại phần thất bại mà không mất phần đã thành công.

Hai bảng cuối dự án theo đề:

- `papers`: `arxiv_id`, `title`, `authors`, `submission_date`, `revised_dates`, `venue`.
- `references`: `arxiv_id` (bài trích dẫn), `ref_arxiv_id`, `ref_title`, `ref_authors`, `ref_submission_date`, `ref_s2_id`.

## 6. Làm việc với Git

Mỗi thành viên clone repo, tạo nhánh riêng từ `main` và mở pull request khi một phần nhỏ đã chạy được:

```powershell
git switch main
git pull
git switch -c feat/discovery
```

Thay `discovery` bằng phần được giao. Một người khác review trước khi merge. Không đưa API key hoặc dữ liệu crawl vào commit.

## 7. Mốc đầu tiên

1. Điền tên người phụ trách từng module và lấy danh sách ID từ giảng viên.
2. Chốt schema, giao diện hàm và cách ghi metrics.
3. Làm luồng đầy đủ với vài bài, có bài nhiều version và trường hợp thiếu source.
4. Kiểm tra khả năng chạy tiếp sau khi ngắt.
5. Chạy toàn bộ trên Colab, thu metrics và dùng kết quả để build báo cáo.
