# Bàn giao phần thành viên 1

## Giao diện đã triển khai

- `load_ids(path)` trả `ids`, `invalid`, `duplicates`. Input TXT UTF-8 một ID/dòng; không hỗ trợ CSV hoặc khoảng ID tự suy diễn.
- `normalize_id(value)` trả base ID; hỗ trợ ID mới/cũ và bỏ suffix version vì yêu cầu crawl cả publication.
- `discover_papers(ids, config, resume=False, client=None)` trả report và ghi atomic snapshot sau mỗi bài.
- `ArxivClient(config)` gọi tuần tự Atom API với pacing ít nhất 3 giây; timeout/retry 429, 5xx và lỗi mạng. Khi tích hợp downloader phải dùng chung cơ chế pacing; không tạo các worker gọi arXiv song song.

## Schema report v1

Root có `schema_version`, `started_at` UTC, `papers`, `elapsed_seconds`, `summary` (summary chỉ chốt khi run kết thúc).

Paper resolved có: `arxiv_id`, `title`, `authors`, `abstract`, `submission_date`, `latest_updated_date`, `revised_dates`, `revision_dates_complete`, `venue`, `doi`, `latest_version`, `versions`, `metadata_source`, `fetched_at`, `status`, `cache_hit`, `discovery_seconds`.

Mỗi version có `version`, `version_id`, `source_url`, `download_status=pending`, `extraction_status=pending`. Các trạng thái này chỉ là kế hoạch tải, module khác nên lưu kết quả thực tế riêng để refresh discovery không ghi đè tiến độ download.

Paper failed có `arxiv_id`, `status=failed`, `error_type`, `error`, `cache_hit`, `discovery_seconds`. Không có response phù hợp được báo lỗi, không tự coi là paper không tồn tại chắc chắn.

Summary có `assigned_unique_ids`, `resolved`, `failed`, `expected_versions`, `cache_hits`. `elapsed_seconds` là thời gian discovery lần chạy hiện tại, gồm chờ/retry; không phải thời gian toàn scraper. Với cache hit, `fetched_at` vẫn là thời điểm fetch gốc.

## Giới hạn cần xử lý khi tích hợp

- Atom metadata mặc định là bản mới nhất, không đảm bảo metadata riêng cho từng revision.
- Atom trả published/updated, không trả trọn danh sách ngày revision. `revised_dates=[]` và cờ completeness ngăn hiểu nhầm rằng nhóm đã lấy đủ. Cần bổ sung nếu muốn hoàn thiện trường lịch sử.
- Venue lấy từ journal reference, có thể gồm cả volume/pages, chưa phải venue được chuẩn hóa.
- Resume dùng snapshot đã thành công; muốn phát hiện revision mới phải chạy fresh. Một output dir dùng cho một tiến trình discovery tại một thời điểm.
- References, RAM/disk toàn hệ thống và download/extraction do các thành viên còn lại triển khai. Main hiện chỉ expose init/discovery để không gọi các stub như đã hoàn thành.

## Tài liệu chính thức

- arXiv API User Manual: https://info.arxiv.org/help/api/user-manual.html
- arXiv API Terms of Use: https://info.arxiv.org/help/api/tou.html
