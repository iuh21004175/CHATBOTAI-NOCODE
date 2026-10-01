# CLAUDE.md — QUY ĐỊNH BẮT BUỘC CHO CLAUDE CODE

## 1. Mục đích

Tài liệu này quy định cách Claude Code phải điều tra, sửa lỗi và thay đổi code trong project.

Mục tiêu cao nhất:

- Sửa đúng nguyên nhân gốc (root cause).
- Ưu tiên giải pháp tổng quát, có thể áp dụng cho các trường hợp tương tự.
- Không dùng workaround hoặc "quick fix" chỉ để làm lỗi biến mất.
- Không tự ý thêm dependency/công nghệ mới.
- Không làm hỏng chức năng hiện có.
- Luôn kiểm tra regression sau khi sửa.

Nguyên tắc:

> Correctness > Quick Fix  
> Root Cause > Symptom  
> General Solution > Case-specific Workaround  
> Existing Technology > New Dependency  
> Evidence > Guess  
> Regression Safety > Speed

---

# 2. NGUYÊN TẮC XỬ LÝ LỖI

Khi phát hiện bất kỳ lỗi nào, PHẢI ưu tiên tìm và xử lý nguyên nhân gốc thay vì chỉ làm lỗi biến mất ở trường hợp hiện tại.

Mục tiêu của việc sửa lỗi:

- Giải quyết nguyên nhân gây lỗi.
- Không tạo ra lỗi mới.
- Không làm hỏng chức năng đang hoạt động.
- Không tạo workaround chỉ phù hợp với một trường hợp cụ thể.
- Có khả năng áp dụng ổn định cho các trường hợp tương tự.
- Không đánh đổi tính đúng đắn của kiến trúc để lấy tốc độ sửa lỗi.

**Không được coi việc lỗi không còn xuất hiện ở case hiện tại là đã sửa lỗi thành công.**

---

# 3. CẤM SỬ DỤNG PHƯƠNG PHÁP SỬA LỖI NHANH

Không được tự ý sử dụng các phương pháp mang tính workaround, hack hoặc patch tạm thời chỉ để che giấu lỗi.

Ví dụ KHÔNG được tự ý:

- Thêm `try/catch` để nuốt lỗi mà không xử lý nguyên nhân.
- Thêm `if` đặc biệt chỉ để bỏ qua một trường hợp lỗi.
- Hard-code giá trị để làm lỗi biến mất.
- Thêm delay/timeout tùy ý để tránh lỗi timing.
- Retry vô hạn hoặc retry mà không xác định nguyên nhân.
- Disable một chức năng để tránh lỗi.
- Bỏ qua validation.
- Bỏ qua exception.
- Comment out code gây lỗi mà không có lý do kiến trúc rõ ràng.
- Ép kiểu hoặc chuyển đổi dữ liệu chỉ để vượt qua error.
- Thêm CSS/JS/Python workaround chỉ để sửa biểu hiện bên ngoài trong khi nguyên nhân nằm ở tầng khác.
- Sửa trực tiếp file của thư viện/plugin/framework bên thứ ba.
- Tạo logic đặc biệt cho một input cụ thể nếu chưa xác định được nguyên nhân tổng quát.

Nếu workaround thực sự là giải pháp kiến trúc hợp lệ, phải giải thích rõ tại sao nó không phải workaround tạm thời.

---

# 4. PHẢI TÌM ROOT CAUSE TRƯỚC KHI SỬA

Trước khi thay đổi code, PHẢI xác định:

1. Lỗi xảy ra ở đâu?
2. Thành phần nào tạo ra lỗi?
3. Input nào gây ra lỗi?
4. Luồng xử lý dẫn đến lỗi là gì?
5. Nguyên nhân gốc là gì?
6. Vì sao lỗi này có thể xảy ra?
7. Những trường hợp tương tự nào cũng có khả năng bị lỗi?

Không được sửa code ngay chỉ dựa trên thông báo lỗi nếu chưa hiểu nguyên nhân.

Nếu chưa đủ thông tin để xác định root cause:

**PHẢI điều tra thêm trước khi sửa.**

Có thể sử dụng:

- Log.
- Stack trace.
- Source code.
- Configuration.
- Database/schema.
- API response.
- Network request.
- Dependency version.
- Runtime/environment.
- Existing tests.
- Documentation chính thức của thư viện/framework.
- Các call site liên quan.

---

# 5. GIẢI PHÁP PHẢI MANG TÍNH TỔNG QUÁT

Sau khi xác định nguyên nhân, phải chọn giải pháp có khả năng xử lý:

> Root cause → tất cả trường hợp bị ảnh hưởng → không chỉ case đang báo lỗi.

Ví dụ:

Nếu lỗi xảy ra vì dữ liệu có thể là `null`, không được chỉ sửa:

```python
if value is None:
    return ""
```

nếu điều này chỉ che giấu vấn đề.

Phải xác định:

- Tại sao dữ liệu có thể null?
- Contract của dữ liệu là gì?
- Thành phần nào chịu trách nhiệm đảm bảo dữ liệu?
- Có cần validation ở boundary không?
- Các nơi khác có cùng vấn đề không?

Sau đó sửa tại tầng phù hợp.

---

# 6. KHÔNG ĐƯỢC TỰ Ý THÊM THƯ VIỆN HOẶC CÔNG NGHỆ MỚI

Project này sử dụng Python.

Khi xử lý lỗi, **PHẢI ưu tiên sử dụng các thư viện, framework và công nghệ đã có trong project**.

Không được tự ý cài đặt hoặc thêm:

- Python package mới.
- PyPI package mới.
- Framework mới.
- Database driver mới.
- SDK mới.
- API client mới.
- External service mới.
- Docker service mới.
- System dependency mới.
- Công nghệ hoặc infrastructure mới.

Đặc biệt, KHÔNG được tự ý thực hiện:

```bash
pip install ...
pip3 install ...
python -m pip install ...
poetry add ...
uv add ...
conda install ...
```

hoặc thay đổi dependency thông qua:

```text
requirements.txt
requirements-dev.txt
pyproject.toml
poetry.lock
uv.lock
Pipfile
Pipfile.lock
environment.yml
Dockerfile
docker-compose.yml
```

nếu chưa được tôi cho phép.

## Nếu cần dependency mới

Nếu sau khi điều tra root cause, Claude Code xác định rằng cần một thư viện hoặc công nghệ chưa có trong project, **KHÔNG được tự ý cài đặt**.

Phải dừng và báo:

```text
NEW DEPENDENCY REQUIRED

Package:
[Tên package]

Version:
[Version đề xuất]

Purpose:
[Mục đích sử dụng]

Root cause:
[Tại sao vấn đề hiện tại cần package này]

Existing solution:
[Đã kiểm tra những thư viện/cơ chế hiện có nào]

Why existing dependencies are insufficient:
[Tại sao không thể giải quyết bằng dependency hiện tại]

Impact:
[Ảnh hưởng đến project]

Installation command:
[Lệnh dự kiến]

Files affected:
[requirements.txt / pyproject.toml / Dockerfile / ...]

Risk:
[Rủi ro hoặc ảnh hưởng có thể có]
```

**Chỉ được tiếp tục cài đặt sau khi tôi xác nhận.**

---

# 7. ƯU TIÊN PYTHON STANDARD LIBRARY VÀ DEPENDENCY HIỆN CÓ

Khi giải quyết lỗi, ưu tiên theo thứ tự:

```text
1. Python Standard Library
        ↓
2. Code/utility đã có trong project
        ↓
3. Dependency hiện đang được sử dụng
        ↓
4. Framework hiện tại
        ↓
5. Dependency mới — CHỈ SAU KHI USER XÁC NHẬN
```

Không được thêm package mới nếu vấn đề có thể giải quyết hợp lý bằng những thành phần đã có.

Không được có tư duy:

```text
Gặp vấn đề
→ tìm package giải quyết
→ pip install
→ sửa code
```

Mà phải:

```text
Gặp vấn đề
→ tìm root cause
→ kiểm tra code hiện tại
→ kiểm tra dependency hiện tại
→ kiểm tra Python standard library
→ chỉ khi không đủ mới đề xuất dependency mới
```

---

# 8. KHÔNG ĐƯỢC TỰ Ý THAY ĐỔI PYTHON ENVIRONMENT

Không được tự ý:

- Thay đổi Python version.
- Tạo virtual environment mới nếu project đã có environment.
- Xóa virtual environment.
- Thay đổi interpreter.
- Thay đổi package manager.
- Chuyển từ `pip` sang `Poetry`.
- Chuyển từ `pip` sang `uv`.
- Chuyển từ `venv` sang Conda.
- Thay đổi Docker Python image.
- Thay đổi base image.
- Thay đổi dependency source/index.
- Thay đổi environment variables liên quan đến dependency.

Nếu phát hiện environment hiện tại là nguyên nhân gây lỗi, phải báo:

```text
ENVIRONMENT ISSUE

Current environment:
...

Problem:
...

Evidence:
...

Recommended change:
...

Impact:
...

User confirmation required: YES
```

Không tự ý thay đổi environment để làm lỗi biến mất.

---

# 9. KHÔNG ĐƯỢC TỰ Ý THAY ĐỔI DEPENDENCY VERSION

Không được tự ý:

- Upgrade package.
- Downgrade package.
- Pin package version.
- Remove package.
- Replace package.

chỉ vì một lỗi xảy ra.

Không được tự động làm:

```bash
pip install package==old_version
```

hoặc:

```bash
pip install -U package
```

nếu chưa xác định rõ:

- Version hiện tại.
- Version gây lỗi.
- Version tương thích.
- Lý do version là nguyên nhân.
- Các package khác có bị ảnh hưởng hay không.

Nếu dependency version thực sự là root cause, phải báo cáo trước khi thay đổi.

---

# 10. PHẢI KIỂM TRA DEPENDENCY CONFLICT

Nếu lỗi liên quan đến package, phải kiểm tra dependency tree và compatibility trước khi sửa.

Cần xác định:

```text
Python version
↓
Framework version
↓
Direct dependencies
↓
Transitive dependencies
↓
Conflicting versions
```

Không được giải quyết conflict bằng cách hạ/nâng một package ngẫu nhiên.

Nếu phát hiện conflict:

```text
DEPENDENCY CONFLICT

Package A:
Current version: ...

Package B:
Current version: ...

Conflict:
...

Root cause:
...

Possible solutions:
1. ...
2. ...
3. ...

Recommended approach:
...

User confirmation required: YES
```

---

# 11. KHÔNG ĐƯỢC SỬA PACKAGE BÊN NGOÀI PROJECT

Không được sửa trực tiếp source code nằm trong:

```text
venv/
.venv/
site-packages/
```

hoặc source code của package/framework bên thứ ba.

Không được thực hiện kiểu:

```text
venv/Lib/site-packages/...
```

→ sửa trực tiếp để project chạy được.

Nếu phát hiện bug trong thư viện bên thứ ba:

1. Xác định bằng evidence.
2. Kiểm tra version.
3. Kiểm tra documentation/changelog nếu cần.
4. Xác định workaround hợp lệ trong code project.
5. Hoặc đề xuất upgrade/downgrade/package replacement.

Nếu cần thay đổi dependency → phải báo tôi trước.

---

# 12. KHÔNG ĐƯỢC CHE GIẤU LỖI BẰNG EXCEPTION HANDLING

Không được sử dụng:

```python
try:
    ...
except:
    pass
```

hoặc:

```python
except Exception:
    return None
```

chỉ để làm chương trình không crash.

Exception handling phải:

- Bắt đúng loại exception.
- Xử lý nguyên nhân phù hợp.
- Không che giấu lỗi.
- Không làm mất thông tin debugging.
- Có logging khi cần.
- Không biến lỗi hệ thống thành kết quả giả.

Ví dụ không được:

```python
try:
    result = process_data()
except Exception:
    result = {}
```

nếu việc này chỉ khiến lỗi biến mất và tạo ra dữ liệu giả.

---

# 13. KHÔNG ĐƯỢC DÙNG HARD-CODE ĐỂ CHE LỖI

Không được tự ý thêm:

```python
if value == "specific_value":
    ...
```

hoặc:

```python
DEFAULT_VALUE = "..."
```

chỉ để xử lý một case đang lỗi.

Nếu giá trị mặc định thực sự thuộc business logic thì phải xác định rõ contract và phạm vi áp dụng.

---

# 14. KHÔNG ĐƯỢC SỬA CONFIGURATION CHỈ ĐỂ LÀM LỖI BIẾN MẤT

Không được tự ý thay đổi:

```text
.env
config.py
settings.py
Dockerfile
docker-compose.yml
environment variables
database configuration
logging configuration
CORS
authentication configuration
timeout
retry
```

chỉ để bypass lỗi.

Nếu configuration là nguyên nhân thực sự:

1. Xác định configuration hiện tại.
2. Xác định expected configuration.
3. Giải thích nguyên nhân.
4. Đề xuất thay đổi.
5. Nếu thay đổi có ảnh hưởng hệ thống → báo tôi trước.

---

# 15. PHẢI KIỂM TRA CÁC TRƯỜNG HỢP TƯƠNG TỰ

Sau khi sửa một lỗi, không chỉ test đúng input gây lỗi ban đầu.

Phải xác định các trường hợp có cùng root cause.

Ví dụ lỗi:

```text
API trả về null
```

Không chỉ test:

```text
null
```

mà phải xem xét tùy contract thực tế:

```text
missing field
null
empty string
empty list
invalid type
malformed response
unexpected response
```

Mục tiêu là đảm bảo giải pháp xử lý đúng **nhóm trường hợp có cùng nguyên nhân**, thay vì chỉ xử lý một input cụ thể.

---

# 16. QUY TRÌNH KIỂM THỬ CHỨC NĂNG BẮT BUỘC (KHÔNG TỰ CHẠY TEST CODE ĐỂ TỰ KẾT LUẬN)

Claude Code KHÔNG được tự viết test code (pytest/unittest/script mới) rồi tự chạy để kết luận rằng một bug đã được sửa đúng hoặc một tính năng hoạt động đúng. Bộ test tự động (unit test) hiện có trong project chỉ dùng để phát hiện lỗi cú pháp/crash/regression ở mức code (xem mục 27), KHÔNG được dùng làm bằng chứng "đã sửa đúng hành vi nghiệp vụ" hoặc "tính năng hoạt động đúng như mong đợi".

Sau khi implement xong một fix/tính năng, Claude Code PHẢI viết một **TEST PROCEDURE PROMPT** — quy trình kiểm thử chức năng từng bước — để người dùng (Claude Cowork) thực hiện kiểm thử thực tế (qua giao diện thật, API thật, dữ liệu thật) và quan sát/phân tích kết quả thực tế.

TEST PROCEDURE PROMPT phải bao gồm tối thiểu 5 nhóm case sau, diễn đạt bằng THAO TÁC THỰC TẾ chứ không phải đoạn code:

## Case 1 — Original bug

Mô tả chính xác thao tác/tình huống đã gây ra lỗi ban đầu, và kết quả mong đợi sau khi sửa.

## Case 2 — Similar cases

Các thao tác/tình huống khác có cùng root cause, và kết quả mong đợi.

## Case 3 — Normal case

Thao tác/luồng sử dụng bình thường (không liên quan trực tiếp đến bug) để đảm bảo không bị ảnh hưởng, và kết quả mong đợi.

## Case 4 — Edge cases

Các trường hợp biên liên quan, và kết quả mong đợi.

## Case 5 — Regression

Các chức năng khác có khả năng bị ảnh hưởng bởi thay đổi, và kết quả mong đợi.

### Format bắt buộc của TEST PROCEDURE PROMPT

```text
TÊN TÍNH NĂNG / BUG:
[Mô tả ngắn gọn]

MỤC TIÊU KIỂM THỬ:
[Kiểm thử cái gì, tại sao]

ĐIỀU KIỆN TIÊN QUYẾT:
[Setup cần thiết: tài khoản, bot_id/team_id, dữ liệu mẫu, cấu hình, biến môi trường liên quan, trạng thái hệ thống cần có trước khi test...]

CASE 1 — ORIGINAL BUG
Bước thực hiện:
1. ...
2. ...
Kết quả mong đợi:
...

CASE 2 — SIMILAR CASES
Bước thực hiện:
...
Kết quả mong đợi:
...

CASE 3 — NORMAL CASE
Bước thực hiện:
...
Kết quả mong đợi:
...

CASE 4 — EDGE CASES
Bước thực hiện:
...
Kết quả mong đợi:
...

CASE 5 — REGRESSION
Bước thực hiện:
...
Kết quả mong đợi:
...
```

Mỗi bước thực hiện phải đủ cụ thể để người thực hiện (không đọc code) vẫn làm theo được chính xác — ví dụ: "Gửi request `POST /api/...` với body `{...}`", "Mở giao diện chatbot, nhập câu hỏi: '...'", "Kiểm tra bảng `messages` trong DB có bản ghi mới với `role='assistant'` và nội dung không rỗng", "Kiểm tra log worker có dòng `...`". Kết quả mong đợi phải là thứ quan sát được cụ thể (nội dung trả về, mã trạng thái HTTP, giá trị trong DB, số lần gọi LLM...), không phải mô tả chung chung.

Claude Code KHÔNG được tự gán `PASS`/`FAIL` hay `STATUS: FIXED` dựa trên việc tự chạy test code hoặc tự suy luận. Sau khi đưa ra TEST PROCEDURE PROMPT, Claude Code phải báo `STATUS: AWAITING FUNCTIONAL VERIFICATION` (xem mục 34) và dừng lại, chờ kết quả kiểm thử thực tế được gửi lại.

Nếu kiểm thử thực tế phát hiện lỗi, người dùng/Claude Cowork sẽ gửi lại mô tả lỗi thực tế đã quan sát được (không phải lỗi suy đoán) để Claude Code tiếp tục điều tra và sửa theo đúng quy trình root-cause ở các mục trên.

Chỉ khi nhận được xác nhận kết quả kiểm thử thực tế là PASS cho đủ 5 case, Claude Code mới được cập nhật `STATUS: FIXED` trong báo cáo.

---

# 17. ƯU TIÊN SỬA TẠI ĐÚNG TẦNG KIẾN TRÚC

Nếu lỗi xuất phát từ:

```text
Input validation
```

→ sửa validation.

Nếu lỗi xuất phát từ:

```text
Business logic
```

→ sửa business logic.

Nếu lỗi xuất phát từ:

```text
Database layer
```

→ sửa database/data access layer.

Nếu lỗi xuất phát từ:

```text
API contract
```

→ xử lý tại API boundary.

Nếu lỗi xuất phát từ:

```text
Configuration
```

→ sửa configuration.

Không được sửa ở một tầng hoàn toàn khác chỉ vì tầng đó dễ sửa hơn.

Ví dụ:

```text
Database trả dữ liệu sai
        ↓
KHÔNG được chỉ thêm JavaScript để sửa dữ liệu hiển thị
```

Phải tìm nguyên nhân tại database/data-access/business layer trước.

---

# 18. QUY TẮC KHI CẦN THƯ VIỆN MỚI

Nếu giải pháp tốt nhất thực sự cần thư viện mới:

**DỪNG IMPLEMENTATION tại điểm cần dependency.**

Không được tự ý cài.

Phải báo:

```text
Tôi đã xác định root cause là: ...

Giải pháp hiện tại không đủ vì: ...

Giải pháp đề xuất cần package:

Package: ...
Version: ...
Purpose: ...

Package hiện tại trong project không thể đáp ứng vì: ...

Tôi chưa cài package này.

Cần bạn xác nhận trước khi tiếp tục.
```

Sau khi tôi xác nhận mới được:

```text
install
→ update dependency file
→ implement
→ test
```

---

# 19. KHÔNG ĐƯỢC GIẢ ĐỊNH DEPENDENCY ĐÃ TỒN TẠI

Không được viết code dựa trên một package mà chưa kiểm tra project có package đó hay không.

Trước khi import:

```python
import some_package
```

phải kiểm tra dependency/project configuration.

Không được tự ý thêm package chỉ vì import bị lỗi.

Nếu package chưa tồn tại:

```text
Package missing
→ kiểm tra package có thực sự cần thiết không
→ nếu cần → báo user
```

---

# 20. KHÔNG ĐƯỢC TỰ Ý THAY ĐỔI KIẾN TRÚC

Không được tự ý:

- Đổi framework.
- Đổi database.
- Đổi thư viện chính.
- Thay đổi kiến trúc hệ thống.
- Rewrite module lớn.
- Thay đổi API contract.
- Thay đổi schema.
- Thay đổi authentication/authorization architecture.

chỉ để giải quyết một lỗi đơn lẻ.

Nếu lỗi cho thấy kiến trúc hiện tại thực sự là nguyên nhân, phải báo cáo:

```text
ARCHITECTURE ISSUE

Root cause:
...

Current architecture causing:
...

Minimal solution:
...

Architectural solution:
...

Impact:
...

User confirmation required: YES
```

Không thực hiện thay đổi kiến trúc lớn khi chưa được xác nhận.

---

# 21. KHÔNG ĐƯỢC TỰ Ý XÓA HOẶC RESET DỮ LIỆU

Khi debug hoặc sửa lỗi, không được tự ý:

- Xóa database.
- Drop table.
- Xóa migration.
- Reset database.
- Xóa dữ liệu production.
- Xóa file người dùng.
- Xóa cache quan trọng.
- Xóa Docker volume.
- Xóa persistent storage.

Nếu cần thao tác phá hủy dữ liệu để kiểm tra:

**PHẢI báo trước và yêu cầu xác nhận.**

---

# 22. PHẢI BẢO VỆ DỮ LIỆU VÀ SECRET

Không được:

- In API key vào log.
- In password vào terminal output nếu không cần.
- Commit secret.
- Hard-code credential.
- Ghi token vào source code.
- Ghi database password vào test.
- Đưa secret vào error message.

Khi debug configuration, có thể hiển thị:

```text
API_KEY = ********
PASSWORD = ********
TOKEN = ********
```

thay vì giá trị thật.

---

# 23. KHÔNG ĐƯỢC TỰ Ý THAY ĐỔI FILE KHÔNG LIÊN QUAN

Khi sửa bug:

- Chỉ thay đổi những file cần thiết.
- Không format lại toàn bộ project nếu không cần.
- Không refactor module không liên quan.
- Không đổi naming hàng loạt.
- Không sửa style/code convention không liên quan.
- Không xóa code chưa được xác minh là dead code.

Mục tiêu là giữ phạm vi thay đổi nhỏ nhưng **không hy sinh tính đúng đắn**.

---

# 24. KHÔNG ĐƯỢC KẾT LUẬN KHI CHƯA CÓ BẰNG CHỨNG

Nếu chưa chắc chắn, không được đoán.

Phải:

1. Điều tra thêm.
2. Kiểm tra source.
3. Kiểm tra log.
4. Kiểm tra configuration.
5. Kiểm tra dependency.
6. Chạy test/reproduction.
7. Nếu cần, kiểm tra documentation chính thức.

Nếu vẫn không thể xác định:

```text
STATUS: BLOCKED

What is known:
...

What is unknown:
...

Evidence needed:
...

Why I cannot safely modify the code:
...
```

Không được tạo một bản sửa dựa trên giả định không được kiểm chứng.

---

# 25. QUY TRÌNH BẮT BUỘC CHO MỖI BUG

Mỗi bug phải tuân theo flow:

```text
BUG
 ↓
REPRODUCE
 ↓
INVESTIGATE
 ↓
IDENTIFY ROOT CAUSE
 ↓
CHECK EXISTING ARCHITECTURE
 ↓
CHECK IMPACT / RELATED CASES
 ↓
DESIGN GENERAL SOLUTION
 ↓
CHECK EXISTING DEPENDENCIES
 ↓
CHECK WHETHER NEW DEPENDENCY IS REQUIRED
 ↓
IF NEW DEPENDENCY → ASK USER
 ↓
IMPLEMENT
 ↓
WRITE TEST PROCEDURE PROMPT (mục 16 — KHÔNG tự viết/chạy test code thay thế bước này)
 ↓
REPORT STATUS: AWAITING FUNCTIONAL VERIFICATION
 ↓
CHỜ KẾT QUẢ KIỂM THỬ THỰC TẾ (từ user / Claude Cowork)
 ↓
NẾU PHÁT HIỆN LỖI THỰC TẾ → QUAY LẠI INVESTIGATE
 ↓
NẾU PASS ĐỦ 5 CASE → REPORT RESULT (STATUS: FIXED)
```

Không được bỏ qua bước Root Cause chỉ vì lỗi có vẻ đơn giản.

Không được bỏ qua bước WRITE TEST PROCEDURE PROMPT bằng cách tự viết và tự chạy test code để thay thế bước kiểm thử chức năng thực tế.

---

# 26. QUY TRÌNH BẮT BUỘC TRƯỚC KHI CODE

Trước khi chỉnh sửa code, Claude Code phải:

### Bước 1 — Đọc context

Kiểm tra:

- README.
- CLAUDE.md.
- pyproject.toml.
- requirements files.
- Docker configuration.
- Configuration files.
- Relevant source code.
- Existing tests.

### Bước 2 — Reproduce

Nếu có thể, tái hiện lỗi.

### Bước 3 — Trace

Theo dõi flow:

```text
Input
 ↓
Validation
 ↓
Business Logic
 ↓
Service
 ↓
Repository / Database
 ↓
External API
 ↓
Output
```

Chỉ áp dụng những tầng thực tế có trong project.

### Bước 4 — Root cause

Xác định chính xác nguyên nhân.

### Bước 5 — Solution design

Thiết kế giải pháp tổng quát.

### Bước 6 — Dependency check

Kiểm tra dependency hiện có trước khi đề xuất dependency mới.

### Bước 7 — Implement

Chỉ sau khi đã hiểu vấn đề.

### Bước 8 — Viết TEST PROCEDURE PROMPT

Không tự viết/chạy test code để tự xác nhận kết quả. Viết quy trình kiểm thử chức năng theo đúng format ở mục 16 (mục tiêu, điều kiện tiên quyết, các bước thao tác thực tế, kết quả mong đợi cho từng case) và bàn giao để kiểm thử thực tế trước khi báo `FIXED`.

---

# 27. QUY TẮC TEST

Có hai loại kiểm thử trong project, KHÔNG được nhầm lẫn hoặc thay thế cho nhau.

## 27.1 Unit test / automated test hiện có trong repo

Nếu project đã có test framework (`pytest`, `unittest`), Claude Code có thể tiếp tục chạy bộ test hiện có để phát hiện lỗi cú pháp, crash, import lỗi, hoặc regression thuần code khi cần thiết. Không được tự ý thêm test framework mới. Không được tự ý xóa hoặc sửa test hiện có chỉ để làm test PASS giả tạo.

Kết quả PASS của bộ test tự động này CHỈ là tín hiệu "code không crash ở phạm vi đã cover". Nó KHÔNG được dùng làm bằng chứng để báo `STATUS: FIXED` cho một bug/tính năng liên quan đến hành vi nghiệp vụ hoặc trải nghiệm người dùng thực tế.

## 27.2 Kiểm thử chức năng (functional verification) — bắt buộc, thay cho việc Claude Code tự test bằng code

Claude Code KHÔNG được tự viết test code mới (pytest/unittest/script) rồi tự chạy để chứng minh một bug đã sửa đúng hoặc một tính năng hoạt động đúng như mong đợi.

Thay vào đó, Claude Code PHẢI viết TEST PROCEDURE PROMPT theo đúng format ở mục 16, để người dùng/Claude Cowork thực hiện kiểm thử thực tế (request/response thật, giao diện thật, dữ liệu thật trong DB, log thật) và phân tích kết quả thực tế.

Claude Code không được tự kết luận `PASS`/`FAIL` hay `FIXED` thay cho bước kiểm thử chức năng thực tế này. Nếu chưa nhận được kết quả kiểm thử thực tế, trạng thái phải là `AWAITING FUNCTIONAL VERIFICATION`, không phải `FIXED`.

---

# 28. QUY TẮC LOGGING VÀ DEBUGGING

Khi debug:

- Ưu tiên log có cấu trúc.
- Không spam log.
- Không log secret.
- Không xóa logging hiện tại chỉ vì log có nhiều thông tin.
- Không giảm mức logging chỉ để terminal sạch.
- Không tắt error reporting để che lỗi.

Sau khi debug xong:

- Xóa debug code không cần thiết.
- Không để lại `print()` tạm thời nếu project sử dụng logging.
- Không để lại breakpoint/debugger.
- Không để lại test bypass.

---

# 29. QUY TẮC VỚI DATABASE

Không được tự ý:

- Drop database.
- Drop table.
- Delete dữ liệu.
- Reset migration.
- Rewrite schema.

Nếu bug liên quan database:

1. Kiểm tra schema.
2. Kiểm tra migration.
3. Kiểm tra model.
4. Kiểm tra query.
5. Kiểm tra transaction.
6. Kiểm tra dữ liệu thực tế nếu được phép.

Nếu cần migration/schema change có khả năng ảnh hưởng dữ liệu:

**PHẢI báo user trước.**

---

# 30. QUY TẮC VỚI API VÀ EXTERNAL SERVICE

Không được tự ý thay đổi API contract để làm lỗi biến mất.

Phải xác định:

- Request.
- Response.
- Status code.
- Authentication.
- Headers.
- Timeout.
- Retry.
- Error response.
- Data schema.

Nếu API bên ngoài thay đổi:

- Xác minh bằng evidence.
- Kiểm tra documentation nếu có.
- Không tự suy đoán response format.
- Không tạo dữ liệu giả để bypass API failure.

---

# 31. QUY TẮC VỚI DOCKER

Không được tự ý:

- Đổi image.
- Đổi Python version trong image.
- Đổi port.
- Đổi volume.
- Xóa volume.
- Đổi network.
- Đổi service.
- Thêm service mới.

chỉ để làm lỗi hiện tại biến mất.

Nếu Docker configuration là root cause:

```text
DOCKER ISSUE

Current configuration:
...

Root cause:
...

Required change:
...

Impact:
...

User confirmation required: YES
```

---

# 32. QUY TẮC KHI CÓ NHIỀU GIẢI PHÁP

Nếu có nhiều cách sửa:

Không được tự ý chọn cách nhanh nhất.

Phải so sánh tối thiểu:

```text
Solution A:
- Root cause coverage
- Complexity
- Maintainability
- Risk
- Dependency impact

Solution B:
- Root cause coverage
- Complexity
- Maintainability
- Risk
- Dependency impact
```

Ưu tiên giải pháp:

1. Đúng root cause.
2. Tổng quát.
3. Ít rủi ro.
4. Dễ bảo trì.
5. Tận dụng architecture/dependency hiện có.
6. Thay đổi nhỏ hợp lý.

---

# 33. QUY TẮC KHI KHÔNG THỂ SỬA AN TOÀN

Nếu không thể sửa lỗi mà không:

- Thêm dependency.
- Thay đổi architecture.
- Thay đổi database.
- Thay đổi API contract.
- Thay đổi Python version.
- Thay đổi infrastructure.
- Thay đổi behavior quan trọng.

thì **KHÔNG ĐƯỢC tự ý thực hiện**.

Phải báo:

```text
BLOCKED — USER DECISION REQUIRED

Root cause:
...

Why current architecture cannot safely solve it:
...

Required change:
...

Impact:
...

Risk:
...

I have NOT applied the risky change.
Please confirm before proceeding.
```

---

# 34. FORMAT BÁO CÁO SAU KHI SỬA

Sau mỗi bug đã sửa, phải báo cáo:

```text
BUG:
[Mô tả lỗi]

ROOT CAUSE:
[Nguyên nhân gốc]

SOLUTION:
[Giải pháp đã áp dụng]

WHY THIS SOLUTION:
[Tại sao đây là giải pháp tổng quát và không chỉ là workaround]

FILES CHANGED:
[Danh sách file]

IMPACT:
[Các thành phần bị ảnh hưởng]

DEPENDENCIES:
- None
hoặc
- [Tên dependency] — đã được user xác nhận

TEST PROCEDURE PROMPT:
[Đính kèm hoặc trỏ tới TEST PROCEDURE PROMPT đầy đủ theo format mục 16 — BẮT BUỘC phải có trước khi báo FIXED]

FUNCTIONAL VERIFICATION RESULT:
- Original bug: PASS / FAIL / CHƯA KIỂM THỬ
- Similar cases: PASS / FAIL / CHƯA KIỂM THỬ
- Normal case: PASS / FAIL / CHƯA KIỂM THỬ
- Edge cases: PASS / FAIL / CHƯA KIỂM THỬ
- Regression: PASS / FAIL / CHƯA KIỂM THỬ
(Kết quả này do người dùng/Claude Cowork thực hiện kiểm thử thực tế và cung cấp lại. Claude Code KHÔNG được tự điền PASS khi chưa nhận được kết quả kiểm thử thực tế.)

STATUS:
FIXED / AWAITING FUNCTIONAL VERIFICATION / NOT VERIFIED / BLOCKED
```

`STATUS: FIXED` chỉ được ghi khi toàn bộ `FUNCTIONAL VERIFICATION RESULT` đã là `PASS` và kết quả đó do người dùng/Claude Cowork cung cấp sau khi kiểm thử thực tế — không phải do Claude Code tự suy luận hoặc tự chạy code kiểm tra.

---

# 35. TIÊU CHUẨN HOÀN THÀNH BUG FIX

Một bug chỉ được coi là **FIXED** khi đáp ứng:

```text
[✓] Root cause đã được xác định
[✓] Đã sửa root cause
[✓] Không chỉ dùng workaround
[✓] Không tạo dependency mới trái phép
[✓] Đã viết TEST PROCEDURE PROMPT theo mục 16
[✓] Original case đã được xác minh PASS qua kiểm thử chức năng thực tế (không phải tự test bằng code)
[✓] Similar cases đã được xác minh PASS qua kiểm thử chức năng thực tế
[✓] Normal cases đã được xác minh PASS qua kiểm thử chức năng thực tế
[✓] Regression đã được xác minh PASS qua kiểm thử chức năng thực tế
[✓] Không phá vỡ architecture hiện tại
[✓] Không sửa trực tiếp third-party package
[✓] Không che giấu exception/error
[✓] Không có debug code tạm thời
[✓] Không có thay đổi không liên quan
```

Nếu chưa nhận được kết quả kiểm thử chức năng thực tế:

```text
STATUS: AWAITING FUNCTIONAL VERIFICATION
```

Nếu đã kiểm thử thực tế nhưng có case FAIL, hoặc thiếu một điều kiện quan trọng khác:

```text
STATUS: NOT VERIFIED
```

Không được báo `FIXED` chỉ vì bộ test code tự viết không còn báo lỗi, hoặc vì Claude Code tự thử qua code và thấy ứng dụng "có vẻ" không còn báo lỗi.

---

# 36. QUY TẮC CUỐI CÙNG

Claude Code phải luôn tuân thủ:

> **Đừng sửa triệu chứng khi chưa hiểu nguyên nhân.**

> **Đừng dùng workaround khi có thể sửa root cause.**

> **Đừng thêm dependency khi dependency hiện tại đủ khả năng giải quyết.**

> **Nếu cần thư viện/công nghệ chưa có trong project, phải báo tôi và chờ xác nhận.**

> **Đừng tự ý thay đổi environment, architecture, database hoặc infrastructure.**

> **Đừng sửa package bên thứ ba trực tiếp.**

> **Đừng che giấu lỗi bằng exception handling, hard-code hoặc configuration bypass.**

> **Một bug chỉ được coi là fixed sau khi đã kiểm tra original case, similar cases và regression.**

> **Đừng tự viết và tự chạy test code (pytest/unittest/script) để tự kết luận một bug đã sửa đúng hoặc một tính năng hoạt động đúng — phải viết TEST PROCEDURE PROMPT (mục 16) và chờ kết quả kiểm thử chức năng thực tế từ người dùng/Claude Cowork trước khi báo FIXED.**

> **Nếu không đủ bằng chứng để sửa an toàn, phải điều tra thêm hoặc báo BLOCKED — không được đoán.**
