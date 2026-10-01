"""Lỗi đọc tệp mà khách hiểu và xử lý được — message là tiếng Việt, hiện thẳng cho khách. Chi tiết kỹ thuật chỉ nằm trong log.
Tách riêng module này (thay vì định nghĩa trong markitdown_reader.py/ocr.py) để cả hai cùng dùng chung 1 kiểu lỗi mà không vòng import lẫn nhau."""


class ReadError(Exception):
    pass
