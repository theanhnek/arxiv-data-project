# Kế hoạch triển khai đồ án — Milestone 1: Data Crawling

## 1. Phạm vi và tình trạng hiện tại

Kế hoạch dựa trên bản đề Project Milestone 1 được cung cấp trong chat. Các endpoint và mức pacing dưới đây được ghi theo đề; trước khi triển khai, người phụ trách phải đối chiếu tài liệu chính thức hiện hành. Đây là kế hoạch làm việc, không phải cam kết các API hoặc thư viện luôn giữ nguyên hành vi.

Mục tiêu là xây corpus từ danh sách/khoảng arXiv ID được giảng viên giao, gồm metadata, source của tất cả phiên bản và references. Corpus được dùng tiếp cho Milestone 2–3.

Hiện repo chỉ có khung `src`, cấu hình mẫu, README và lệnh tạo thư mục dữ liệu. Chưa triển khai discovery, download, extraction, references hoặc benchmark.

Không phải crawl toàn bộ arXiv. Không bắt buộc triển khai đồng thời Kaggle, OAI-PMH và S3. Phương án ban đầu: arXiv API để resolve ID, source endpoint để tải từng version, Semantic Scholar để lấy references.

Các thông tin nhóm cần điền:

- Danh sách/khoảng ID được giao: CHƯA CÓ.
- Deadline và múi giờ nộp bài: CHƯA CÓ.
- Ngôn ngữ chính thức của báo cáo: CHƯA XÁC NHẬN.
- URL repo code public: CHƯA CÓ.
- URL repo báo cáo public và GitHub Pages: CHƯA CÓ.
- Tên thật của 5 người phụ trách: CHƯA ĐIỀN.

Chưa có đề Milestone 2–3 nên chỉ chuẩn bị khả năng dùng lại dữ liệu, không tự đặt yêu cầu parsing hoặc mô hình cho các milestone đó.

## 2. Kết quả phải có khi hoàn thành Milestone 1

- [ ] Một repo code public dùng tích lũy cho toàn dự án, có mã nguồn, dependencies và README chạy được.
- [ ] Một repo Jupyter Book public riêng, được host trên GitHub Pages.
- [ ] Corpus cục bộ có record cho mọi bài được giao và mọi version đã xác định.
- [ ] Các version có source phù hợp được giữ nguyên nội dung `.tex`/`.bib`, không có file ảnh trong corpus.
- [ ] Version không có TeX vẫn có record; lỗi tải không bị đánh đồng với không có source.
- [ ] References thô được giữ đầy đủ; metadata chuẩn hóa có nguồn và trạng thái thiếu rõ ràng.
- [ ] Có state để chạy tiếp khi ngắt và retry các phần thất bại.
- [ ] Có số liệu chất lượng, runtime, RAM, disk đo được trên Colab CPU-only.
- [ ] Báo cáo sinh bảng và biểu đồ từ dữ liệu trong quá trình build.
- [ ] Nộp hai link yêu cầu trên Moodle đúng hạn. Milestone 1 chưa nộp corpus.

## 3. Phân công nhóm 5 người

### Người 1 — Discovery và tích hợp

- File chính: `src/discovery.py`, `src/main.py`, `src/config.py`.
- Nhận danh sách ID; resolve metadata; xác định tất cả version.
- Điều phối schema, CLI và ghép pipeline.
- Review phần references/storage của người 4.
- Hoàn thành khi danh sách bài/version đầy đủ và có lệnh chạy xuyên suốt các module.

### Người 2 — Downloader và HTTP

- File chính: `src/downloader.py`.
- Tải source theo version; xử lý pacing, timeout, retry, User-Agent, file tạm và resume.
- Làm việc với người 1 và 4 để không có các client tự ý phá giới hạn request tổng.
- Review phần discovery của người 1.
- Hoàn thành khi tải có kiểm soát, ghi đủ trạng thái, không đánh dấu response lỗi thành source hợp lệ.

### Người 3 — Extraction và kiểm tra corpus

- File chính: `src/extractor.py`.
- Nhận diện định dạng; trích xuất an toàn `.tex`/`.bib`; bảo toàn thư mục và bytes.
- Ghi số lượng file, kích thước, trường hợp không có TeX và lỗi archive.
- Review downloader của người 2.
- Hoàn thành khi các định dạng hỗ trợ được kiểm tra, trường hợp chưa hỗ trợ có trạng thái riêng.

### Người 4 — References và storage

- File chính: `src/references.py`, `src/storage.py`.
- Crawl Semantic Scholar; cache; chuẩn hóa metadata và references.
- Lưu JSON/state an toàn; kiểm tra khóa và ID gốc; chuẩn bị exporter hai bảng.
- Review extraction của người 3.
- Hoàn thành khi có raw references, trường chuẩn hóa và resume không gây mất/trùng dữ liệu.

### Người 5 — Metrics, Colab và báo cáo

- File chính: `src/metrics.py`, notebook benchmark sẽ được bổ sung; repo báo cáo riêng.
- Thiết kế metrics từ đầu; đo toàn pipeline; kiểm tra kết quả.
- Chuẩn bị Colab CPU-only, Jupyter Book và GitHub Pages.
- Review luồng tích hợp và hướng dẫn chạy.
- Hoàn thành khi có benchmark có thể kiểm tra lại và báo cáo build thành công.

Mỗi người tự viết mô tả phương pháp và hạn chế của phần mình để người 5 tổng hợp. Không dồn toàn bộ viết báo cáo cho một người vào cuối kỳ.

## 4. Giai đoạn 0 — Chuẩn bị repo và môi trường

Phụ trách: người 1; cả nhóm cùng thực hiện. Phụ thuộc: chưa có.

1. Xác nhận danh sách ID, deadline và ngôn ngữ nộp bài từ giảng viên/Moodle. Nếu nhận một khoảng ID, xác nhận cách hiểu hai đầu khoảng và xử lý ID không tồn tại; không suy ra rằng mọi số đều là bài hợp lệ.
2. Tạo repo code public trên GitHub, liên kết repo Git cục bộ và thêm 5 thành viên.
3. Người 5 tạo repo báo cáo public riêng; ghi hai URL vào README.
4. Cả nhóm cài cùng phiên bản Python; tạo `.venv`, cài `src/requirements.txt` theo README.
5. Copy `.env.example` thành `.env`; điền User-Agent liên hệ và API key nếu có. Không commit `.env`.
6. Chạy `python -m src.main --help` và `python -m src.main --init --output data`.
7. Mỗi module có issue, người phụ trách, tiêu chí hoàn thành và nhánh `feat/...`.
8. Sau khi xác nhận dependencies chạy trên local và Colab, ghi phiên bản cụ thể vào requirements để có thể tái lập.

Hoàn thành khi mọi người clone và chạy lệnh khởi tạo được; repo không có dữ liệu hoặc bí mật.

## 5. Giai đoạn 1 — Chốt giao diện và dữ liệu chung

Phụ trách: người 1 + người 4; cả nhóm review. Phải hoàn thành trước khi ghép các module.

### 5.1 Quy ước ID và version

- Giữ `arxiv_id` gốc, không thay dấu trong metadata.
- Tách base ID khỏi suffix version; hỗ trợ cả ID mới và cũ như `hep-th/9901001`.
- Nếu input có version, vẫn áp dụng yêu cầu lấy mọi version của publication theo đề.
- Chỉ đổi ID thành tên thư mục bằng hàm mapping thống nhất, không tạo đường dẫn trực tiếp từ ID có `/`.
- Cố định thời điểm discovery và lưu số version được phát hiện; trước khi chốt kết quả kiểm tra lại nếu có revision mới.

### 5.2 Giao diện hàm dự kiến

Đây là giao diện để nhóm thống nhất, chưa phải API đã được triển khai:

```python
discover_papers(ids, config)
download_source(version_id, config)
extract_source(source_path, destination)
fetch_references(arxiv_id, config)
```

Mỗi kết quả cần chứa dữ liệu, trạng thái, lỗi có cấu trúc và metrics; không chỉ trả `True`/`False`. Thống nhất kiểu trả về trước khi code.

### 5.3 Các record nội bộ

- Paper: ID gốc, title, authors, submission date, revised dates, venue, danh sách version, thời điểm/source lấy metadata.
- Version: version ID, đường dẫn, loại source phát hiện, trạng thái download/extraction, số byte và lỗi.
- References: raw response, thời điểm fetch, ID publication được query, trạng thái và bản chuẩn hóa.
- Run: run ID, commit code, config không chứa secrets, môi trường chạy, thời điểm bắt đầu/kết thúc và metrics.
- State: trạng thái từng bước để resume; chỉ đánh dấu hoàn thành sau khi ghi output thành công.

### 5.4 Cấu trúc lưu dự kiến

```text
data/
  corpus/
    2310-12345/
      v1/
        metadata.json
        references.json
        source/
          main.tex
          sections/introduction.tex
          bibliography.bib
      v2/
        ...
  state/
  metrics/
  tmp/
```

References có thể lưu một bản cache chung theo publication rồi liên kết trong metadata từng version, hoặc sao chép vào mỗi version. Dù chọn cách nào cũng phải ghi rõ dữ liệu là publication-level khi API không phân biệt version.

Hoàn thành khi nhóm đã chốt schema, đường dẫn, kiểu lỗi, giao diện hàm và đơn vị đo byte/giây/RAM.

## 6. Giai đoạn 2 — Triển khai discovery

Phụ trách: người 1. Phụ thuộc: giai đoạn 1.

1. Viết bộ đọc file ID, bỏ dòng trống, phát hiện trùng và chuẩn hóa input mà không làm mất ID gốc.
2. Query arXiv API bằng danh sách ID được giao; không tìm bài gần giống để thay cho ID lỗi.
3. Parse metadata; kiểm tra ID trả về tương ứng với ID yêu cầu, kể cả batch trả thiếu hoặc khác thứ tự.
4. Đọc suffix version mới nhất. Ví dụ latest là `...v4` thì tạo danh sách `v1`, `v2`, `v3`, `v4`.
5. Phân biệt thời điểm submission ban đầu, thời điểm cập nhật và ngày của từng revision. Không coi một giá trị `updated` là toàn bộ danh sách ngày revision; nếu thiếu phải bổ sung bằng nguồn metadata phù hợp hoặc ghi thiếu rõ ràng.
6. Cache metadata, ghi thời điểm discovery, lỗi ID không tồn tại và lỗi request riêng.
7. Ghi tổng thời gian discovery và số bài/version dự kiến.

Kiểm tra: bài một version, nhiều version, ID cũ, ID trùng, ID không tồn tại và response thiếu mục.

Hoàn thành khi mỗi input có kết quả hoặc lỗi giải thích được, và danh sách version có thể đưa thẳng sang downloader.

## 7. Giai đoạn 3 — Triển khai downloader

Phụ trách: người 2. Có thể phát triển song song với discovery sau khi chốt giao diện.

1. Đối chiếu tài liệu chính thức, chọn HTTP client và cơ chế dùng chung limiter arXiv.
2. Theo đề, dùng mirror `https://export.arxiv.org/src/{version_id}`, User-Agent mô tả và ít nhất 3 giây giữa các request arXiv liên tiếp, một kết nối tại một thời điểm. Không chạy 5 máy để tăng tốc trên cùng danh sách.
3. Stream response xuống file tạm để không đọc toàn archive vào RAM.
4. Kiểm tra HTTP status; nhận diện response HTML báo lỗi hoặc throttling; không chỉ dựa vào đuôi file.
5. Xử lý timeout, lỗi mạng, 429, 5xx; tôn trọng `Retry-After` nếu có; retry giới hạn với backoff. Không retry vô hạn ID chắc chắn không tồn tại.
6. Chỉ rename file tạm và ghi trạng thái downloaded khi hoàn tất. Tính checksum nếu cần kiểm tra tính toàn vẹn/resume.
7. Khi chạy lại, kiểm tra output và state trước khi bỏ qua. File tạm hoặc file thiếu không được coi là hoàn thành.
8. Ghi bytes thực tải, duration, attempts, status và lỗi cuối.

Hoàn thành khi tải đúng version, không lưu trang lỗi như archive và có thể phục hồi sau khi bị ngắt.

## 8. Giai đoạn 4 — Extraction và quản lý disk

Phụ trách: người 3. Phụ thuộc: hợp đồng đầu ra downloader.

1. Phát hiện định dạng bằng nội dung thực tế; khảo sát tar/gzip, TeX đơn và các dạng gặp trong mẫu.
2. Chỉ ghi regular files có đuôi `.tex` hoặc `.bib` vào `source/`.
3. Kiểm tra đường dẫn sau resolve nằm trong destination; chặn absolute path, `..`, symlink/hardlink và các member không an toàn.
4. Giữ thư mục tương đối để các lệnh `\\input`/`\\include` còn trỏ đúng.
5. Không sửa encoding, nội dung TeX, lệnh figure hoặc `\\includegraphics` trong bước này.
6. Ghi số file, dung lượng giữ lại; giữ record rỗng nếu xác nhận không có `.tex`/`.bib`.
7. Phân biệt `no_tex_source`, `unsupported_format`, archive hỏng và download thất bại. Không dùng một trạng thái cho mọi trường hợp.
8. Xác nhận extraction đã ghi xong rồi mới xóa archive tạm theo chính sách đã thống nhất; không xóa bằng chứng cần thiết trước khi lưu metrics.
9. Ghi rõ hai thước đo: bytes gói tải về (có thể đã nén) và bytes `.tex`/`.bib` giữ lại (không nén). Nếu so sánh cùng mức nén/giải nén, đo thêm tổng bytes member của archive khi đọc. Không gọi chênh lệch hai thước đo khác nhau là kích thước riêng của ảnh.

Kiểm tra: archive nhiều thư mục, thiếu `.bib`, chỉ có TeX, không có TeX, archive hỏng và path traversal. Đối chiếu bytes file được trích xuất với bytes gốc.

Hoàn thành khi corpus chỉ có loại file cho phép trong thư mục source và không làm thay đổi nội dung file.

## 9. Giai đoạn 5 — References và chuẩn hóa

Phụ trách: người 4. Có thể phát triển song song giai đoạn 2–4.

1. Đối chiếu fields, rate limit và cơ chế pagination hiện hành của Semantic Scholar trước khi triển khai.
2. Query publication bằng base arXiv ID; lấy title, authors, year/publicationDate, venue, external IDs và Semantic Scholar paper ID khi có.
3. Bảo đảm lấy hết references theo cơ chế endpoint thực tế; không coi trang đầu hoặc response bị giới hạn là danh sách đầy đủ.
4. Theo giới hạn nêu trong đề, unauthenticated phải đáp ứng cả 1 request/giây và 100 request/5 phút; chỉ sleep 1 giây là chưa đủ để đáp ứng giới hạn cửa sổ. Luôn xử lý 429/backoff; API key không thay thế error handling.
5. Cache response theo publication; lưu timestamp và trạng thái. Không gọi lại cho mọi version nếu chỉ nhận cùng dữ liệu publication-level.
6. Giữ references không có arXiv ID trong dữ liệu nội bộ; giữ cả raw response để kiểm tra lại.
7. Phân biệt API thành công nhưng references rỗng, publication không tìm thấy và lỗi request.
8. Chuẩn hóa tác giả thành list, giữ ID cũ, tách version khỏi khóa publication nếu cần và lưu provenance.
9. Không gán publicationDate hoặc year thành submission date arXiv. Bổ sung ngày từ metadata arXiv nếu cần, có cache và pacing; nếu không xác định được thì lưu giá trị thiếu theo schema và báo cáo coverage.
10. Chuẩn bị hai bảng cuối: `papers` và `references` theo cột trong đề. Mỗi cặp citing paper/reference là một quan hệ; tránh nhân bản hàng chỉ vì có nhiều version, trừ khi đặc tả sau này yêu cầu khác.

Hoàn thành khi dữ liệu thô còn đầy đủ, bảng chuẩn hóa không có khóa arXiv null và tỷ lệ thiếu trường được ghi nhận.

## 10. Giai đoạn 6 — Ghép pipeline và resume

Phụ trách: người 1; các thành viên phối hợp.

1. Bổ sung CLI đọc danh sách ID, output path, timeout, retry và tùy chọn resume.
2. Luồng chính: input → discovery → từng version download/extract → references theo publication → lưu metadata/state/metrics.
3. Thiết kế lỗi ở một paper/version không làm mất kết quả bài khác; luôn có tổng kết các phần lỗi.
4. Mỗi bước ghi output an toàn rồi mới cập nhật state. Metadata hoàn thành không được xuất hiện trước file thực tế.
5. Khi resume, chạy lại phần thiếu/thất bại, không tạo duplicate và không đánh dấu cache cũ như request mới thành công.
6. Ghi rõ status cuối mỗi paper: đủ version, version có TeX, không có TeX, còn lỗi và references status.
7. Giữ raw archive tạm theo từng bài/version để hạn chế peak disk; không tải tất cả archive rồi mới giải nén nếu không cần.
8. Cập nhật README bằng lệnh đã chạy thực tế. Các lệnh crawling tương lai chưa có trong khung không được ghi như đã hoạt động.

Hoàn thành khi một lệnh có thể chạy toàn luồng và một lần dừng giữa chừng có thể tiếp tục an toàn.

## 11. Giai đoạn 7 — Kiểm tra luồng nhỏ trước khi chạy toàn bộ

Phụ trách: cả nhóm; người 5 tổng hợp.

Chọn vài bài đại diện từ phạm vi được giao; nếu phạm vi không có tình huống cần kiểm tra thì dùng fixture tự tạo trong kiểm tra offline, không giả chúng là dữ liệu đã crawl.

- [ ] Có bài một version và nhiều version nếu tìm được.
- [ ] Có thư mục TeX lồng nhau, `.bib` và trường hợp không có `.bib`.
- [ ] Có xử lý không có source TeX và source hỏng.
- [ ] Có kiểm tra ID cũ, dữ liệu trùng và trường metadata thiếu.
- [ ] Mô phỏng timeout/429 và kiểm tra retry có giới hạn.
- [ ] Ngắt sau download, giữa extraction và trước khi ghi state rồi chạy lại.
- [ ] Corpus không có ảnh, file style hoặc đường dẫn thoát khỏi output.
- [ ] References không có arXiv vẫn còn trong dữ liệu nội bộ.
- [ ] Số version expected/processed khớp và mọi phần chưa xong đều có lỗi rõ ràng.

Chỉ viết kiểm tra cho hành vi quan trọng như enumerate version, extraction an toàn, state/resume và chuẩn hóa ID; không cần test cho mọi dòng cấu hình. Fixture trong repo phải là dữ liệu mô phỏng nhỏ, không phải corpus tải về.

Hoàn thành khi luồng nhỏ chạy ổn và lỗi phát hiện đã được sửa trước khi crawl toàn bộ.

## 12. Giai đoạn 8 — Benchmark và crawl toàn bộ trên Colab

Phụ trách: người 5 + người 1.

### 12.1 Chuẩn bị

1. Tạo notebook Colab chạy CPU-only: clone code, cài dependencies, cấu hình secrets và cấp input ID.
2. Ghi commit, Python/package versions, CPU/RAM/disk khả dụng, số bài và config chạy.
3. Tách benchmark fresh run và resume/cached run. Không dùng kết quả có cache rồi báo như lần crawl mới.
4. Kiểm tra output có nơi lưu bền vững sau khi Colab hết phiên. Không xem ổ tạm Colab là bản lưu duy nhất; kiểm tra thời gian/disk của cơ chế backup và ghi rõ có tính trong benchmark hay không.

### 12.2 Đo trong lúc chạy

- Runtime: dùng wall time cho toàn luồng; bao gồm chờ rate limit, retry và các bước xử lý. Ghi discovery tổng, từng paper và tổng run. Nếu xử lý nhiều bài xen kẽ, giải thích cách quy thời gian cho từng paper.
- RAM: lấy mẫu định kỳ theo khoảng cố định đã ghi lại, đo process chính và các process con. Tính trung bình từ mẫu và ghi peak quan sát được; nêu giới hạn của sampling.
- Disk: đo định kỳ các thư mục làm việc và dung lượng filesystem khả dụng; ghi peak working disk, peak gồm file tạm và final output. Không chỉ đo sau khi đã xóa archive.
- Với mỗi paper/version: status, bytes tải, bytes giữ lại, số file, request/retry count và duration.
- Với references: status API, số references tổng, số có arXiv ID và độ đầy đủ trường metadata.

### 12.3 Cách tính chỉ số cần thống nhất

- Paper coverage: số assigned paper resolve được / số assigned paper hợp lệ; đồng thời công khai input không hợp lệ và lỗi chưa resolve.
- Version completion: số version xử lý xong, kể cả xác nhận không có TeX / tổng version đã phát hiện. Báo riêng tỷ lệ version thực sự có TeX.
- Full-paper completion: số paper có mọi version xử lý xong / số paper được giao theo mẫu số đã công bố.
- Reference request success: số publication lấy API thành công / số publication đã thử query. Báo riêng not-found, empty và request failure.
- Reference field coverage: tỷ lệ references có arXiv ID, ngày hoặc S2 ID. Không gọi thiếu ID là lỗi HTTP.
- Average references: công bố tính trên các bài query thành công hay toàn bộ; báo số bài thiếu dữ liệu.
- Average sizes: công bố đơn vị, compression basis và tính theo paper hay version; nếu theo paper thì cộng các version của bài trước khi lấy trung bình.
- Average paper runtime: chỉ rõ mẫu số và cách tính thời gian discovery dùng chung. Không lấy tổng duration chồng lấn của worker làm tổng wall time.

Sau run: backup corpus/state/metrics, retry phần lỗi có thể phục hồi, đối chiếu danh sách ID/version và lưu summary cuối. Ghi mọi lần retry bổ sung thay vì che khỏi runtime.

Hoàn thành khi có output được bảo toàn, metrics gắn với đúng run/commit và giải thích được tất cả bài/version còn thiếu.

## 13. Giai đoạn 9 — Hoàn thiện báo cáo Jupyter Book

Phụ trách: người 5; mỗi người cung cấp nội dung module của mình.

1. Giới thiệu bài toán, phạm vi được giao và corpus cần cho các milestone sau.
2. Mô tả kiến trúc pipeline, nguồn dữ liệu và lý do chọn phương án.
3. Trình bày discovery/version coverage, tải source, extraction, references, retry/resume.
4. Nêu schema, cách tổ chức corpus và cách xử lý missing/no-source/failure.
5. Sinh bảng/biểu đồ cho số lượng bài/version, tỷ lệ thành công, kích thước, references và missing fields.
6. Sinh kết quả runtime, RAM và disk; mô tả testbed Colab CPU-only và phương pháp đo.
7. Phân tích lỗi, giới hạn coverage, ngày reference thiếu và sự khác nhau giữa publication-level/version-level.
8. Ghi phân công đóng góp và tài liệu tham khảo thực sự sử dụng.
9. Chọn cách cung cấp metrics tổng hợp đã loại secrets cho book build: artifact hoặc dữ liệu tổng hợp trong repo báo cáo nếu phù hợp quy định. Không đưa corpus vào repo code.
10. Build từ môi trường sạch; bảo đảm notebook thực thi và tạo lại bảng/biểu đồ trong lúc build, không chỉ hiển thị ảnh chụp hoặc output notebook cũ.
11. Triển khai GitHub Pages, mở URL bằng phiên chưa đăng nhập và kiểm tra điều hướng, hình, bảng và link code.

Hoàn thành khi báo cáo public truy cập được và quá trình build có thể tạo lại kết quả từ metrics đã cung cấp.

## 14. Giai đoạn 10 — Kiểm tra và nộp bài

Phụ trách: cả nhóm; người 1 chịu trách nhiệm xác nhận cuối.

- [ ] README ghi đúng Python, dependencies, config, input và lệnh chạy/resume/benchmark.
- [ ] Thử clone repo vào môi trường sạch và chạy theo README.
- [ ] Dependencies đã cố định theo môi trường được kiểm tra.
- [ ] Không có `.env`, API key, corpus, archive hoặc output sinh ra trong Git history/commit sẽ public.
- [ ] Có đủ record mọi bài/version; mọi phần chưa lấy được có trạng thái và được giải thích.
- [ ] Code và báo cáo thống nhất số liệu, schema, run và commit.
- [ ] Repo code và repo báo cáo đều public; Pages truy cập không cần tài khoản.
- [ ] Báo cáo đúng ngôn ngữ chương trình và không thiếu phần performance.
- [ ] Corpus/state/metrics có bản backup đã kiểm tra đọc được để dùng tiếp.
- [ ] Tạo tag milestone sau khi chốt code; điền URL code/report vào README.
- [ ] Một thành viên nộp theo nhóm trên Moodle hai link đúng yêu cầu trước deadline, thành viên khác kiểm tra lại màn hình xác nhận.

## 15. Lịch tham khảo và thứ tự ưu tiên

Đây là lịch 10 buổi tham khảo, chưa gắn ngày vì chưa có deadline hoặc số lượng paper. Thời gian crawl thực tế phụ thuộc số version, kích thước, pacing và retry; cần ước lượng sau pilot, không hứa toàn bộ sẽ xong trong một buổi.

1. Buổi 1: xác nhận đề/ID; repo/môi trường; chốt schema và phân công.
2. Buổi 2–3: phát triển song song discovery, download, extraction, references; người 5 chuẩn bị metrics/book.
3. Buổi 4: ghép luồng nhỏ và sửa sai giao diện giữa module.
4. Buổi 5: kiểm tra các trường hợp lỗi, resume, extraction và references completeness.
5. Buổi 6: pilot Colab, dự tính thời gian/disk và điều chỉnh.
6. Buổi 7–8: chạy toàn bộ, theo dõi, backup và xử lý phần lỗi.
7. Buổi 9: build báo cáo, phân tích kết quả, deploy Pages.
8. Buổi 10: clone sạch, kiểm tra cuối và nộp trước hạn.

Ưu tiên nếu thiếu thời gian: luồng đúng và đủ version → dữ liệu bảo toàn → retry/resume → benchmark và báo cáo → tối ưu. Không tăng request vượt pacing để chạy kịp.

## 16. Chuẩn bị cho Milestone 2–3

- Giữ corpus `.tex`/`.bib` nguyên bản, không trộn kết quả parsing vào source gốc.
- Giữ ID publication/version ổn định và raw metadata để truy nguồn.
- Duy trì repo code tích lũy, cùng repo Jupyter Book; không tạo lại từ đầu cho mỗi milestone.
- Chuẩn bị hai bảng đã được đề Milestone 1 quy định để phục vụ matching và xuất dataset sau này.
- Khi có đề Milestone 2–3, bổ sung kế hoạch cụ thể theo yêu cầu thật. Final data trên Hugging Face thuộc bước sau; không cần publish corpus ở Milestone 1.
