# Đồ án Nhập môn Khoa học Dữ liệu — Pipeline arXiv Toàn Tuyến (Milestones 1, 2 & 3)

Dự án xây dựng pipeline hoàn chỉnh qua 3 giai đoạn:
1. **Milestone 1 (Data Crawling)**: Thu thập metadata, mã nguồn LaTeX (tất cả các phiên bản `v1..vN`) và dữ liệu trích dẫn từ arXiv & Semantic Scholar, xuất bảng `papers` và `references`.
2. **Milestone 2 (Hierarchical Parsing & Standardization)**: Phân tích cấu trúc phân cấp LaTeX theo đường biên dịch `\documentclass` $\rightarrow$ `\input`/`\include`, chuẩn hóa công thức toán, khử trùng lặp phần tử xuyên phiên bản, hợp nhất mục BibTeX/đổi tên `\cite{}`, xuất bảng `elements`, `hierarchy` và `bibtex`.
3. **Milestone 3 (Reference Matching Pipeline)**: Làm sạch văn bản, gán nhãn thủ công & tự động, trích xuất 8 đặc trưng tương đồng, huấn luyện mô hình xếp hạng ứng viên Logistic Candidate Ranker đánh giá bằng $\text{MRR@5}$, xuất bảng `predictions` và kiểm định toàn bộ 6 bảng Parquet trước khi nộp lên Hugging Face.

---

## 1. Yêu cầu môi trường & Cài đặt

- **Python**: 3.11 trở lên
- **Hệ điều hành**: Windows / Linux / macOS
- **Kết nối mạng**: Cần truy cập được `export.arxiv.org`, `api.semanticscholar.org` và `huggingface.co`

### Các bước cài đặt:

1. **Clone repository:**
   ```bash
   git clone https://github.com/theanhnek/arxiv-data-project.git
   cd arxiv-data-project
   ```

2. **Khởi tạo môi trường ảo và cài đặt thư viện:**
   - Trên Windows:
     ```powershell
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
   ```bash
   cp .env.example .env      # Linux/macOS
   copy .env.example .env    # Windows
   ```
   Nội dung file `.env`:
   ```dotenv
   ARXIV_USER_AGENT=Lab1ArxivCrawler/0.1 (contact: your-email@example.com)
   SEMANTIC_SCHOLAR_API_KEY=
   HF_TOKEN=
   ```

---

## 2. Chuẩn bị dữ liệu đầu vào (Input — Bảng phân công Nhóm 14)

| `--member-index` / `--member` | MSSV | Thành viên | Khoảng phân công chính thức | Dải arXiv ID (`YYMM.NNNNN`) |
| :---: | :--- | :--- | :--- | :--- |
| `1` / `Kiet` | `22120171` | Lê Văn Tuấn Kiệt | `2025-10	14401	2025-10	19400` | `2510.14401` $\rightarrow$ `2510.19400` (5.000 ID) |
| `2` / `Duong` | `23120033` | Huỳnh Võ Đại Dương | `2025-10	9401	2025-10	14400` | `2510.09401` $\rightarrow$ `2510.14400` (5.000 ID) |
| `3` / `Ha` | `23120037` | Nguyễn Thanh Khánh Hà | `2025-10	4401	2025-10	9400` | `2510.04401` $\rightarrow$ `2510.09400` (5.000 ID) |
| `4` / `Kien` | `23120055` | Đào Trung Kiên | `2025-09	26047	2025-10	4400` | `2509.26047`–`2509.26646` (600 ID) + `2510.00001`–`2510.04400` (4.400 ID) |
| `5` / `Anh` | `23120210` | Nguyễn Hoàng Thế Anh | `2025-09	11047	2025-09	16046` | `2509.11047` $\rightarrow$ `2509.16046` (5.000 ID) |

---

## 3. Hướng dẫn chạy toàn bộ 3 Milestone

### 3.1. Milestone 1 — Thu thập dữ liệu & Xuất Parquet (`papers`, `references`)

```powershell
# Chạy toàn tuyến Milestone 1 + Xuất Parquet cho dải được giao (ví dụ Thành viên 2)
python -m src.main --all --export-parquet --range 2025-10 9401 2025-10 14400 --output data --resume
```

### 3.2. Milestone 2 — Phân tích phân cấp LaTeX & Chuẩn hóa BibTeX (`elements`, `hierarchy`, `bibtex`)

Sau khi đã có corpus `.tex`/`.bib` từ Milestone 1, chạy bộ Parser để xây dựng cây phân cấp, chuẩn hóa toán (`$...$` và `\begin{equation}...\end{equation}`), khử trùng lặp phần tử văn bản & mục BibTeX xuyên phiên bản (kèm đổi tên `\cite{}`):

```powershell
# Cách 1: Gọi qua CLI chính
python -m src.main --parse --output data

# Cách 2: Gọi trực tiếp script Milestone 2
python scripts/build_milestone2_tables.py --output data --shard-rows 50000
```
Kết quả sinh ra:
- `data/export/parquet/data/elements/*.parquet`
- `data/export/parquet/data/hierarchy/*.parquet`
- `data/export/parquet/data/bibtex/*.parquet`
- `data/parsed/version_coverage.jsonl` & `data/parsed/milestone2_summary.json` (kèm thời gian chạy và Peak RAM).

### 3.3. Milestone 3 — Reference Matching Pipeline (`predictions`)

Quy trình thực hiện ghép nối `bibtex` với `references`:

```powershell
# Bước 1: Gán nhãn tự động (Automatic Matching >= 10% số bài ngoài tập thủ công, tối thiểu 10 bài)
python scripts/build_automatic_predictions.py --output data --fraction 0.10 --minimum-papers 10 --threshold 0.80

# Bước 2: Tạo bảng gợi ý gán nhãn thủ công (5 bài x 20 entries = 100 entries mỗi thành viên)
python scripts/build_manual_label_review.py --output data --entries-per-paper 20

# Bước 3: Sau khi kiểm tra và xác nhận nhãn thủ công trong data/matching/manual_label_review.csv
# (đặt review_status=CONFIRMED_BY_USER), ghép các dòng thủ công vào predictions và tạo manual_labels.jsonl:
python scripts/build_manual_predictions.py --output data

# Bước 4: Huấn luyện mô hình Logistic Candidate Ranker trên 8 đặc trưng & đánh giá MRR@5
python scripts/train_matcher.py --output data --epochs 250 --learning-rate 0.15 --l2 0.02

# Bước 5: Cập nhật bảng predictions chính thức bằng kết quả xếp hạng của mô hình
python scripts/promote_model_predictions.py --output data
```
*(Hoặc chạy gộp qua CLI: `python -m src.main --match --output data`)*.

### 3.4. Kiểm định đủ 6 bảng Parquet & Upload lên Hugging Face

```powershell
# Kiểm định toàn bộ 6 bảng Parquet (papers, references, elements, hierarchy, bibtex, predictions)
python scripts/validate_hf_dataset.py --dataset-dir data/export/parquet --member Duong --all-six-tables

# Upload bộ dữ liệu công khai lên Hugging Face
$env:HF_TOKEN = "hf_your_write_token"
python -m src.hf_upload --repo-id <username>/<dataset-name> --output data
```

---

## 4. Tài liệu hóa Định dạng Nhãn & Kỹ thuật Đặc trưng (Milestone 3 Documentation)

### 4.1. Định dạng nhãn Ground Truth (`data/matching/manual_label_review.csv` & `manual_labels.jsonl`)

1. **File rà soát thủ công (`data/matching/manual_label_review.csv`)**:
   - Chọn ít nhất $5$ bài báo/thành viên (ngoài tập tự động), mỗi bài $\ge 20$ mục BibTeX ($\ge 100$ mục/thành viên; cả nhóm 5 người $\ge 25$ bài và $\ge 500$ mục).
   - Mỗi dòng gồm `arxiv_id`, `citation_key`, `bibtex_title`, `bibtex_authors`, `year`, 5 ứng viên gợi ý (`candidate_1..5_arxiv_id`, `title`, `score`), cột điền nhãn `manual_groundtruth_arxiv_id` (điền mã `ref_arxiv_id` đúng hoặc `NONE` nếu không khớp với tài liệu tham khảo nào có arXiv ID của bài báo đó) và `review_status` (`CONFIRMED_BY_USER`).
2. **Sổ cái nhãn đã xác nhận (`data/matching/manual_labels.jsonl`)**:
   - Mỗi dòng là một JSON phẳng:
     ```json
     {"arxiv_id": "2510.07077", "citation_key": "vaswani2017transformer", "groundtruth_arxiv_id": "1706.03762", "manual_label": "1706.03762", "label_source": "user_confirmed", "review_status": "confirmed"}
     ```
   - Khi một mục BibTeX không có tài liệu tham khảo arXiv tương ứng (`manual_label: "NONE"`), trường `groundtruth_arxiv_id` nhận giá trị `null` (tạo thành một hàng toàn nhãn âm `0` trong ma trận ứng viên $m \times n$).
3. **Chiến lược chia tập dữ liệu ở mức bài báo (Publication-level Split)**:
   - Mỗi bài báo được gán cố định vào 1 trong 3 phân vùng `train`, `valid`, hoặc `test` (không có hiện tượng rò rỉ mục BibTeX của cùng một bài báo sang 2 phân vùng).
   - Với mỗi thành viên: `test` gồm 1 bài gán nhãn thủ công + 1 bài gán nhãn tự động; `valid` gồm 1 bài gán nhãn thủ công + 1 bài gán nhãn tự động; `train` gồm toàn bộ các bài đã gán nhãn còn lại (tương ứng trên toàn nhóm $k=5$ người: $5$ bài thủ công + $5$ bài tự động cho `test`, $5$ bài thủ công + $5$ bài tự động cho `valid`, và phần còn lại cho `train`).

### 4.2. Định nghĩa & Lý giải 8 Đặc trưng trong Mô hình (`scripts/train_matcher.py`)

Bài toán được mô hình hóa dưới dạng **Pairwise Candidate Ranking** bằng hồi quy Logistic có trọng số (`numpy logistic candidate ranker`, chuẩn hóa Z-score và điều chuẩn $L_2$). Với mỗi cặp `(bibtex_entry, candidate_reference)` trong cùng một bài báo `arxiv_id`, vector đặc trưng $\mathbf{x} \in \mathbb{R}^8$ bao gồm:

| STT | Tên đặc trưng | Công thức / Định nghĩa | Lý giải ý tưởng (Feature Justification) |
| :-: | :--- | :--- | :--- |
| 1 | **`title_dice`** | $\frac{2 \|T_{\text{bib}} \cap T_{\text{ref}}\|}{\|T_{\text{bib}}\| + \|T_{\text{ref}}\|}$ | Đo tỷ lệ chồng lấp từ khóa tiêu đề sau khi bỏ stop-words và lệnh LaTeX; ít nhạy cảm với tiêu đề dài hơn Jaccard. |
| 2 | **`title_jaccard`** | $\frac{\|T_{\text{bib}} \cap T_{\text{ref}}\|}{\|T_{\text{bib}} \cup T_{\text{ref}}\|}$ | Phạt nặng các ứng viên có tiêu đề chứa nhiều từ thừa không nằm trong mục BibTeX. |
| 3 | **`bigram_dice`** | Hệ số Dice trên tập cặp từ liên tiếp (2-grams) của tiêu đề | Bảo toàn thứ tự cụm từ chuyên ngành (ví dụ `"attention is"` vs `"language model"`), phân biệt các bài cùng chủ đề nhưng khác tên. |
| 4 | **`author_dice`** | Hệ số Dice trên tập token họ/tên tác giả (`authors` vs `ref_authors`) | Khắc phục trường hợp tiêu đề bị viết tắt hoặc đổi tên giữa bản preprint arXiv và bản hội nghị bằng cách đối chiếu danh sách tác giả. |
| 5 | **`title_exact`** | $\mathbb{I}(\text{norm\_title}_{\text{bib}} = \text{norm\_title}_{\text{ref}})$ | Đặc trưng nhị phân kích hoạt độ tin cậy cao khi chuỗi tiêu đề sau chuẩn hóa khớp tuyệt đối 100%. |
| 6 | **`length_ratio`** | $\frac{\min(\|T_{\text{bib}}\|, \|T_{\text{ref}}\|)}{\max(\|T_{\text{bib}}\|, \|T_{\text{ref}}\|)}$ | Phát hiện trường hợp `\bibitem` gộp cả tên tạp chí/ghi chú làm tiêu đề dài bất thường so với tiêu đề chuẩn trên Semantic Scholar. |
| 7 | **`year_similarity`** | $\max\left(0, 1 - \frac{\|\text{year}_{\text{bib}} - \text{year}_{\text{ref}}\|}{10}\right)$ | Đo độ gần gũi về thời gian xuất bản (cho phép sai số nhỏ 1–2 năm giữa bản nộp arXiv đầu tiên và năm xuất bản tạp chí). |
| 8 | **`baseline_rank`** | $\frac{1}{\text{rank}_{\text{heuristic}}}$ | Tận dụng thứ hạng nghịch đảo từ điểm kết hợp ban đầu ($0.85 \cdot \text{title\_dice} + 0.15 \cdot \text{author\_dice}$) trên không gian ứng viên của bài báo làm prior cho bộ xếp hạng tuyến tính. |

---

## 5. Cấu trúc dữ liệu đầu ra (Chuẩn 6 bảng Parquet trên Hugging Face)

```text
data/export/parquet/
├── README.md                           # Dataset Card khai báo đủ 6 configs
└── data/
    ├── papers/train-*.parquet          # Bảng 1 (M1): Metadata bài báo
    ├── references/train-*.parquet      # Bảng 2 (M1): Trích dẫn có ref_arxiv_id từ Semantic Scholar
    ├── elements/train-*.parquet        # Bảng 3 (M2): Các phần tử cấu trúc văn bản đã khử trùng lặp
    ├── hierarchy/train-*.parquet       # Bảng 4 (M2): Cạnh cha-con (node-parent edges) theo từng version
    ├── bibtex/train-*.parquet          # Bảng 5 (M2): Các mục BibTeX đã chuẩn hóa & hợp nhất
    └── predictions/train-*.parquet     # Bảng 6 (M3): Kết quả dự đoán top-5 ứng viên (cand_1..cand_5)
```

---

## 6. Kiểm thử tự động (Unit Tests)

Chạy toàn bộ các bài kiểm thử tự động offline:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v --basetemp data/test-suite
```

---

## 7. Tài liệu tham khảo
- [arXiv API User Manual](https://info.arxiv.org/help/api/user-manual.html)
- [arXiv API Terms of Use](https://info.arxiv.org/help/api/tou.html)
- [Project Problem Statements (Crawling, Parsing, Matching)](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/)
