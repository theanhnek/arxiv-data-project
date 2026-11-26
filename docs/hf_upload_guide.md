# 1. Quy tắc chạy cào dữ liệu an toàn (Tránh bị khóa IP arXiv & Semantic Scholar)

Khi cả nhóm cùng chạy thu thập dữ liệu cho 5.000 bài mỗi người, để không bị hệ thống máy chủ chặn (HTTP 429 Too Many Requests / HTTP 403 Forbidden), các thành viên bắt buộc tuân thủ:

1. **Giữ khoảng cách tối thiểu $\ge 3$ giây giữa các request:**
   - Trong mã nguồn `src/downloader.py` và `src/discovery.py`, biến `arxiv_delay_seconds = 3.0` đã được cấu hình mặc định.
   - **Tuyệt đối không tự ý chỉnh giảm thông số này** trong file cấu hình hay tham số dòng lệnh.
2. **CẢNH BÁO QUAN TRỌNG VỀ ĐỊA CHỈ IP MẠNG:**
   - **Không ngồi cào dữ liệu chung một mạng Wi-Fi** (như cùng phòng trọ, quán cà phê, ký túc xá). Toàn bộ các thiết bị trên cùng một Wi-Fi sẽ đi ra ngoài với cùng một địa chỉ Public IP. Nếu 2 người trở lên cùng cào, arXiv sẽ nhận diện là tấn công dồn dập và **khóa IP toàn bộ nhóm ngay lập tức**.
   - *Khuyến nghị:* Chạy cào dữ liệu tại nhà riêng, hoặc sử dụng kết nối 4G/5G độc lập cho từng máy.
3. **Khai báo thông tin định danh `ARXIV_USER_AGENT`:**
   - Copy file `.env.example` thành `.env` (nếu chưa có).
   - Điền email cá nhân thật của bạn vào biến `ARXIV_USER_AGENT` trong `.env` theo định dạng:
     ```env
     ARXIV_USER_AGENT=Lab1ArxivCrawler/0.1 (contact: your_email@student.university.edu.vn)
     ```
4. **Cài đặt chế độ hoạt động liên tục của máy tính:**
   - Thời gian cào 5.000 bài ước tính mất khoảng **8 đến 9 tiếng** (Discovery ~4h, References ~4–5h).
   - Cắm sạc liên tục, vào cài đặt Windows/macOS chỉnh chế độ màn hình / máy tính sang **Never Sleep / Never Hibernate** để tiến trình không bị tắt giữa chừng.
   - Luôn sử dụng cờ `--resume` khi chạy. Nếu bị ngắt kết nối mạng hoặc mất điện, chỉ cần gõ lại lệnh cũ, chương trình sẽ tự động bỏ qua các bài đã tải thành công.

---

## 2. Hướng dẫn từng bước Upload Dataset lên Hugging Face (1 Trang)

### Bước 1: Tạo tài khoản & Access Token (Quyền Write)
1. Đăng ký hoặc đăng nhập tài khoản tại [huggingface.co](https://huggingface.co).
2. Nhấp vào ảnh đại diện góc trên bên phải $\rightarrow$ chọn **Settings** $\rightarrow$ chọn thẻ **Access Tokens** ở menu bên trái.
3. Bấm **Create new token**:
   - **Name**: đặt tên gợi nhớ (ví dụ: `nmkhdl-lab1`).
   - **Type / Permissions**: Chọn quyền **Write** *(Lưu ý: Bắt buộc phải là Write, nếu chọn Read sẽ không upload được dữ liệu)*.
4. Sao chép chuỗi mã Token (dạng `hf_...`) và lưu vào máy cá nhân. Tuyệt đối **không** gửi token vào nhóm chat và **không** commit lên GitHub.

### Bước 2: Thiết lập Token trong môi trường dòng lệnh
Mở PowerShell tại thư mục gốc của project và gán biến môi trường `HF_TOKEN`:
```powershell
$env:HF_TOKEN="hf_your_personal_token_here"
```
*(Trên Linux/macOS: `export HF_TOKEN="hf_your_personal_token_here"`)*.

### Bước 3: Xuất dữ liệu Parquet và chạy kiểm tra
1. Xuất dữ liệu chia shard theo chuẩn:
   ```powershell
   .\.venv\Scripts\python.exe -m src.main --export-parquet --output data
   ```
2. Chạy script kiểm tra chất lượng dữ liệu trước khi upload (đảm bảo không trùng ID, không thiếu cột, đúng dải bài được phân công và đọc lại được bằng `datasets.load_dataset`):
   ```powershell
   .\.venv\Scripts\python.exe scripts/validate_hf_dataset.py --dataset-dir data/export/parquet --member <Kiet|Duong|Ha|Kien|Anh>
   ```
   *(Xem chi tiết trong [docs/hf_validation_guide.md](hf_validation_guide.md)).*

### Bước 4: Upload dữ liệu lên Hugging Face
Thực hiện lệnh tải dữ liệu lên repo cá nhân:
```powershell
.\.venv\Scripts\python.exe -m src.hf_upload --repo-id <USERNAME>/<TEN-DATASET> --output data
```
*(Trong đó thay `<USERNAME>` bằng tên tài khoản Hugging Face của bạn, ví dụ: `theanh/arxiv-dataset-nmkhdl`)*.

### Bước 5: Xác nhận chế độ Public (Điều kiện bắt buộc khi nộp bài)
1. Truy cập vào đường link dataset của bạn trên Hugging Face.
2. Kiểm tra xem có huy hiệu **Public** hay không.
3. **Kiểm tra thực tế:** Mở một **cửa sổ trình duyệt ẩn danh (Incognito)**, dán đường link dataset vào và nhấn Enter.
   - Nếu bạn xem được đầy đủ mô tả và xem trước (preview) được dữ liệu các bảng mà **không cần đăng nhập**, link đã hợp lệ.
   - Sao chép đường link này để dán nộp vào ô nộp bài trên Moodle theo hạn chót.

---

## 3. Các lỗi thường gặp và cách xử lý (Troubleshooting)

| Mã lỗi / Hiện tượng | Nguyên nhân | Cách khắc phục |
| :--- | :--- | :--- |
| **`401 Unauthorized`** | Chưa khai báo biến `HF_TOKEN` hoặc token bị gõ sai / hết hạn. | Kiểm tra lại lệnh `$env:HF_TOKEN="hf_..."` trước khi chạy lệnh upload. |
| **`403 Forbidden`** | Token được tạo với quyền `Read` thay vì `Write`. | Vào lại *Settings > Access Tokens* trên Hugging Face, xóa token cũ và tạo token mới chọn quyền **Write**. |
| **`RepositoryNotFoundError`** | Định dạng tên `--repo-id` không hợp lệ. | Đảm bảo tên repo có đủ cấu trúc `<tên-user>/<tên-repo>`, ví dụ: `myuser/arxiv-subset-2024`. |
| **Bị mất kết nối giữa chừng** | Mạng Wi-Fi chập chờn hoặc timeout khi đẩy file lớn. | Chạy lại lệnh upload. Thư viện Hugging Face Hub hỗ trợ resume các file đã đẩy dở. |
| **Giảng viên không xem được link** | Dataset đang bị đặt ở chế độ `Private`. | Vào mục **Settings** của Dataset trên Hugging Face, cuộn xuống phần **Change visibility** và chuyển sang **Public**. |
