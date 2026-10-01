"""Máy chủ MCP (stdio) mà runtime dsh khởi chạy để cung cấp công cụ cho agent. CHỈ dùng thư viện chuẩn (không import gì của dự án):
chạy trong tiến trình con riêng, không được nạp Flask/Chroma/model embedding.

Công cụ (model thấy dưới tên mcp__kb__<tên>): search_knowledge_base, summarize_conversation + 3 công cụ KẾT THÚC finish_answer / ask_clarification / decline
+ (Phase M) các công cụ HÀNH ĐỘNG trên website khách do biến môi trường KB_ACTIONS mô tả (chỉ hành động đã được chủ bot duyệt); gọi 1 công cụ hành động = POST
/internal/actions/dispatch, Flask đẩy lệnh xuống widget và chờ widget báo kết quả THẬT — MCP chỉ chuyển kết quả đó cho model, không tự bịa "đã xong".
Công cụ kết thúc không làm gì ngoài xác nhận — quyết định được worker đọc từ chính lệnh gọi (protocol.summarize_events).

Danh tính KHÔNG do model truyền (chống prompt injection đổi sang bot khác):
- bot_id: biến môi trường KB_BOT_ID của tiến trình này (do worker đặt).
- run_id + token: tệp JSON KB_RUN_FILE do worker ghi TRƯỚC mỗi lượt (tiến trình dsh chạy tuần tự từng lượt nên không lẫn).
- hội thoại cần tóm tắt: model KHÔNG truyền gì cho summarize_conversation; Flask tự biết hội thoại của lượt (ngữ cảnh do Flask đặt theo run_id).
"""
import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_FALLBACK = "2025-06-18"
SEARCH_TIMEOUT_SECONDS = 20
SUMMARIZE_TIMEOUT_SECONDS = 30  # gồm 1 lệnh gọi LLM tóm tắt (thường 2-6s)
ACTION_TIMEOUT_MARGIN_SECONDS = 5  # cộng vào thời gian chờ widget (run file: action_wait_seconds) để Flask kịp trả lời "hết hạn chờ"
MAX_QUERY_CHARS = 500

TEXTS = {
    "vi": {
        "search_desc": "Tìm thông tin về doanh nghiệp, sản phẩm, dịch vụ, giá, chính sách trong tài liệu của bot. Dùng khi thông tin tra cứu "
                       "ban đầu chưa đủ hoặc chưa liên quan; hãy viết lại câu tìm ngắn gọn, đúng từ khóa (không cần lặp lại câu cũ).",
        "query_desc": "Câu/từ khóa cần tra cứu",
        "summarize_desc": "Tóm tắt phần đầu hội thoại đã dài và trả về bản tóm tắt để bạn dùng ngay trong lượt này. CHỈ gọi khi tin nhắn của khách có "
                          "nhắc rằng hội thoại đã dài; không gọi trong các trường hợp khác. Tối đa 1 lần mỗi lượt, không cần tham số.",
        "finish_desc": "KẾT THÚC lượt bằng câu trả lời cho khách. Chỉ dùng thông tin có trong tài liệu tra cứu; gọi đúng 1 lần.",
        "clarify_desc": "KẾT THÚC lượt bằng đúng 1 câu hỏi làm rõ khi chưa đủ thông tin để trả lời chính xác.",
        "decline_desc": "KẾT THÚC lượt khi không có thông tin để trả lời (hệ thống sẽ gửi lời từ chối do chủ doanh nghiệp cấu hình).",
        "answer": "Nội dung trả lời gửi cho khách", "question": "Câu hỏi làm rõ gửi cho khách", "reason": "Lý do ngắn gọn (nội bộ)",
        "intent": "Nhãn ý định hiện tại của khách (chữ thường, gạch dưới)", "confidence": "Độ chắc chắn 0.0-1.0",
        "slots": "Thông tin khách đã cung cấp (chỉ giá trị khách thực sự nói)", "self": "Độ chắc chắn 0.0-1.0 rằng thông tin đủ để trả lời",
        "recorded": "Đã ghi nhận. Không cần viết thêm gì, chỉ trả lời 'ok'.",
        "limit": "Đã hết số lần tra cứu cho lượt này. Hãy kết luận ngay bằng finish_answer, ask_clarification hoặc decline.",
        "no_context": "(Không tìm thấy thông tin liên quan trong tài liệu.)",
        "unavailable": "Không tra cứu được lúc này (lỗi hệ thống). Đừng đoán: hãy dùng decline.",
        "bad_query": "Thiếu câu cần tra cứu.",
        "summarize_not_needed": "Lượt này không cần tóm tắt hội thoại. Hãy tiếp tục xử lý câu hỏi của khách.",
        "summarize_nothing": "Không có phần hội thoại cũ nào cần tóm tắt thêm. Hãy tiếp tục xử lý câu hỏi của khách.",
        "summarize_busy": "Hội thoại đang được tóm tắt ở nền. Hãy tiếp tục xử lý câu hỏi của khách bằng thông tin hiện có.",
        "summarize_failed": "Chưa tóm tắt được lúc này. Hãy tiếp tục xử lý câu hỏi của khách bằng thông tin hiện có (không đoán phần hội thoại cũ).",
        "summary_label": "Tóm tắt phần đầu hội thoại:",
        "confirm_note": "(Khách sẽ được hỏi xác nhận trong khung chat trước khi thực hiện.)",
        "action_param": "Giá trị do khách cung cấp",
        "action_done": "Hoàn tất: thao tác đã được thực hiện trên trang của khách.",
        "action_data": "Nội dung đọc được từ trang:",
        "action_failed": "Không thực hiện được (lý do: {reason}). Đừng nói với khách là đã thành công.",
        "action_timeout": "Chưa nhận được xác nhận từ trình duyệt của khách nên KHÔNG biết thao tác đã xong hay chưa. Đừng khẳng định đã thực hiện; hãy nói bạn đã gửi yêu cầu và nhờ khách kiểm tra.",
        "action_awaiting": "Đã hiện hộp xác nhận cho khách trong khung chat; thao tác CHỈ chạy khi khách bấm Đồng ý. Hãy nhờ khách xác nhận và đừng nói là đã hoàn tất.",
        "action_unavailable": "Hành động này không dùng được lúc này ({reason}). Hãy giải thích với khách và hướng dẫn họ tự làm.",
        "action_limit": "Đã dùng hết số lần thực hiện hành động của lượt này. Hãy kết luận ngay bằng finish_answer.",
        "action_invalid": "Thiếu thông tin cần thiết ({reason}). Hãy hỏi khách bằng ask_clarification.",
    },
    "en": {
        "search_desc": "Search the bot's documents for information about the business, products, services, prices and policies. Use it when the "
                       "initial reference information is missing or irrelevant; rewrite the query as short keywords (do not repeat the old one).",
        "query_desc": "Query or keywords to look up",
        "summarize_desc": "Summarize the early part of a long conversation and return the summary for immediate use in this turn. ONLY call it when the "
                          "customer message says the conversation is long; do not call it otherwise. At most once per turn, no arguments.",
        "finish_desc": "END the turn with the reply to the customer. Use only facts from the retrieved documents; call exactly once.",
        "clarify_desc": "END the turn with exactly one clarifying question when there is not enough information to answer accurately.",
        "decline_desc": "END the turn when there is no information to answer (the system sends the owner-configured refusal).",
        "answer": "Reply text sent to the customer", "question": "Clarifying question sent to the customer", "reason": "Short internal reason",
        "intent": "Customer's current intent label (lowercase, underscores)", "confidence": "Certainty 0.0-1.0",
        "slots": "Information the customer has provided (only what they actually said)", "self": "Certainty 0.0-1.0 that the information is enough",
        "recorded": "Recorded. Write nothing more, just reply 'ok'.",
        "limit": "The search budget for this turn is used up. Conclude now with finish_answer, ask_clarification or decline.",
        "no_context": "(No relevant information was found in the documents.)",
        "unavailable": "Search is unavailable right now (system error). Do not guess: use decline.",
        "bad_query": "Missing query.",
        "summarize_not_needed": "This turn does not need a summary. Carry on with the customer's question.",
        "summarize_nothing": "There is no older conversation left to summarize. Carry on with the customer's question.",
        "summarize_busy": "The conversation is being summarized in the background. Carry on using the information you already have.",
        "summarize_failed": "The summary is unavailable right now. Carry on using the information you already have (do not guess the older conversation).",
        "summary_label": "Summary of the early conversation:",
        "confirm_note": "(The customer will be asked to confirm in the chat window before it runs.)",
        "action_param": "Value provided by the customer",
        "action_done": "Done: the action was performed on the customer's page.",
        "action_data": "Content read from the page:",
        "action_failed": "It could not be performed (reason: {reason}). Do not tell the customer it succeeded.",
        "action_timeout": "No confirmation came back from the customer's browser, so it is UNKNOWN whether the action finished. Do not claim it was done; say you sent the request and ask the customer to check.",
        "action_awaiting": "A confirmation box was shown to the customer in the chat; the action runs ONLY if they press Confirm. Ask them to confirm and do not say it is complete.",
        "action_unavailable": "This action is not available right now ({reason}). Explain to the customer and guide them to do it themselves.",
        "action_limit": "The action budget for this turn is used up. Conclude now with finish_answer.",
        "action_invalid": "Required information is missing ({reason}). Ask the customer with ask_clarification.",
    },
}


def texts(language: str) -> dict:
    return TEXTS.get(language, TEXTS["vi"])


def action_tool_definitions(language: str, actions: list[dict]) -> list[dict]:
    """Công cụ hành động website (Phase M): mô tả lấy từ module_actions.description; tham số (value_from_slot) đều là chuỗi do khách cung cấp."""
    t = texts(language)
    tools = []
    for action in actions:
        props = {p["name"]: {"type": "string", "description": t["action_param"]} for p in action.get("params", []) if isinstance(p, dict) and p.get("name")}
        description = action.get("description", "") + (" " + t["confirm_note"] if action.get("confirm") else "")
        tools.append({"name": action["name"], "description": description, "inputSchema": {"type": "object", "properties": props}})
    return tools


def tool_definitions(language: str, actions: list[dict] | None = None) -> list[dict]:
    t = texts(language)
    common = {
        "intent": {"type": "string", "description": t["intent"]},
        "intent_confidence": {"type": "number", "description": t["confidence"]},
        "slots": {"type": "object", "description": t["slots"]},
    }
    return [
        {"name": "search_knowledge_base", "description": t["search_desc"],
         "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": t["query_desc"]}}, "required": ["query"]}},
        {"name": "summarize_conversation", "description": t["summarize_desc"], "inputSchema": {"type": "object", "properties": {}}},
        {"name": "finish_answer", "description": t["finish_desc"],
         "inputSchema": {"type": "object", "required": ["answer"],
                         "properties": {"answer": {"type": "string", "description": t["answer"]}, **common,
                                        "self_assessed_confidence": {"type": "number", "description": t["self"]}}}},
        {"name": "ask_clarification", "description": t["clarify_desc"],
         "inputSchema": {"type": "object", "required": ["question"],
                         "properties": {"question": {"type": "string", "description": t["question"]}, **common}}},
        {"name": "decline", "description": t["decline_desc"],
         "inputSchema": {"type": "object", "properties": {"reason": {"type": "string", "description": t["reason"]}}}},
        *action_tool_definitions(language, actions or []),
    ]


class Server:
    def __init__(self, environ=None, opener=None):
        env = os.environ if environ is None else environ
        self.bot_id = env.get("KB_BOT_ID", "")
        self.base_url = (env.get("KB_INTERNAL_URL") or "").rstrip("/")
        self.run_file = env.get("KB_RUN_FILE", "")
        self.language = env.get("KB_LANGUAGE", "vi")
        self.opener = opener or urllib.request.urlopen
        self._counted_run = None
        self._search_calls = 0
        self._action_run = None
        self._action_calls = 0
        self.actions = self._load_actions(env.get("KB_ACTIONS", ""))

    @staticmethod
    def _load_actions(raw: str) -> dict:
        """KB_ACTIONS: JSON danh sách hành động do worker đặt lúc khởi động tiến trình (rỗng/hỏng -> không có công cụ hành động)."""
        try:
            items = json.loads(raw) if raw else []
        except ValueError:
            return {}
        if not isinstance(items, list):
            return {}
        return {a["name"]: a for a in items if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"]}

    # -- ngữ cảnh lượt hiện tại (do worker ghi) --
    def read_run(self) -> dict:
        try:
            with open(self.run_file, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _search(self, query: str) -> tuple[str, bool]:
        t = texts(self.language)
        if not isinstance(query, str) or not query.strip():
            return t["bad_query"], True  # kiểm tra TRƯỚC khi đếm: câu tìm rỗng không tốn lượt tra cứu
        run = self.read_run()
        run_id = run.get("run_id")
        if run_id != self._counted_run:  # lượt mới: đếm lại
            self._counted_run, self._search_calls = run_id, 0
        self._search_calls += 1
        if self._search_calls > int(run.get("max_search_calls", 3)):
            return t["limit"], False
        payload = json.dumps({"bot_id": int(self.bot_id), "run_id": run_id, "token": run.get("token"), "query": query.strip()[:MAX_QUERY_CHARS]}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/internal/rag/search", data=payload, method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            with self.opener(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return t["unavailable"], True
        text = data.get("text") if isinstance(data, dict) else None
        return (text if isinstance(text, str) and text.strip() else t["no_context"]), False

    def _summarize(self) -> tuple[str, bool]:
        t = texts(self.language)
        run = self.read_run()
        if not run.get("allow_summarize"):
            return t["summarize_not_needed"], False  # từ chối sớm, không tốn 1 lượt gọi HTTP (Flask vẫn kiểm tra lại độc lập)
        payload = json.dumps({"bot_id": int(self.bot_id), "run_id": run.get("run_id"), "token": run.get("token")}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/internal/conversation/summarize", data=payload, method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            with self.opener(request, timeout=SUMMARIZE_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return t["summarize_failed"], True
        status = data.get("status") if isinstance(data, dict) else None
        summary = data.get("summary") if isinstance(data, dict) else None
        if status == "summarized" and isinstance(summary, str) and summary.strip():
            return f"{t['summary_label']}\n{summary.strip()}", False
        if status == "nothing_to_summarize":
            return t["summarize_nothing"] + (f"\n\n{t['summary_label']}\n{summary.strip()}" if isinstance(summary, str) and summary.strip() else ""), False
        if status == "busy":
            return t["summarize_busy"], False
        if status in ("not_allowed", "already_used"):
            return t["summarize_not_needed"], False
        return t["summarize_failed"], True

    def _action(self, name: str, arguments: dict) -> tuple[str, bool]:
        """Gọi công cụ hành động: Flask kiểm tra lại mọi điều kiện (đã duyệt, đúng website, giới hạn lượt), đẩy lệnh xuống widget rồi chờ kết quả thật."""
        t = texts(self.language)
        run = self.read_run()
        if not run.get("allow_actions"):
            return t["action_unavailable"].format(reason="chat thử / không có phiên khách"), True
        run_id = run.get("run_id")
        if run_id != self._action_run:
            self._action_run, self._action_calls = run_id, 0
        self._action_calls += 1
        if self._action_calls > int(run.get("max_action_calls", 2)):
            return t["action_limit"], False
        declared = {q.get("name") for q in self.actions[name].get("params", []) if isinstance(q, dict)}  # chỉ tham số công cụ có khai báo (Flask lọc lại lần nữa)
        params = {k: v for k, v in (arguments or {}).items() if k in declared and isinstance(v, (str, int, float)) and not isinstance(v, bool)}
        payload = json.dumps({"bot_id": int(self.bot_id), "run_id": run_id, "token": run.get("token"), "tool": name, "params": params}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/internal/actions/dispatch", data=payload, method="POST", headers={"Content-Type": "application/json"}
        )
        timeout = float(run.get("action_wait_seconds", 8)) + ACTION_TIMEOUT_MARGIN_SECONDS
        try:
            with self.opener(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return t["action_timeout"], True
        status = data.get("status") if isinstance(data, dict) else None
        reason = str(data.get("reason") or "") if isinstance(data, dict) else ""
        if status == "done":
            info = data.get("data")
            return t["action_done"] + (f"\n{t['action_data']} {info}" if isinstance(info, str) and info.strip() else ""), False
        if status == "failed":
            return t["action_failed"].format(reason=reason or "error"), True
        if status == "awaiting_confirmation":
            return t["action_awaiting"], False
        if status == "limit":
            return t["action_limit"], False
        if status == "invalid":
            return t["action_invalid"].format(reason=reason), True
        if status == "unavailable":
            return t["action_unavailable"].format(reason=reason or "not available"), True
        return t["action_timeout"], True  # 'timeout' hoặc trạng thái lạ: không biết kết quả -> không được khẳng định đã xong

    def call_tool(self, name: str, arguments: dict) -> dict:
        t = texts(self.language)
        if name == "search_knowledge_base":
            text, is_error = self._search((arguments or {}).get("query"))
            return {"content": [{"type": "text", "text": text}], "isError": is_error}
        if name == "summarize_conversation":
            text, is_error = self._summarize()
            return {"content": [{"type": "text", "text": text}], "isError": is_error}
        if name in ("finish_answer", "ask_clarification", "decline"):
            return {"content": [{"type": "text", "text": t["recorded"]}], "isError": False}
        if name in self.actions:
            text, is_error = self._action(name, arguments)
            return {"content": [{"type": "text", "text": text}], "isError": is_error}
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}

    # -- JSON-RPC --
    def handle(self, message: dict):
        """Trả dict phản hồi, hoặc None với notification."""
        method, msg_id, params = message.get("method"), message.get("id"), message.get("params") or {}
        if msg_id is None:
            return None
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL_FALLBACK, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "kb", "version": "1.0.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_definitions(self.language, list(self.actions.values()))}
        elif method == "tools/call":
            arguments = params.get("arguments")
            result = self.call_tool(params.get("name"), arguments if isinstance(arguments, dict) else {})
        else:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _utf8(stream):
    """Giao thức MCP là JSON UTF-8. Trên Windows stdin/stdout của tiến trình con (pipe) mặc định theo code page (cp1252) -> tiếng Việt
    hỏng hoặc UnicodeEncodeError làm chết tiến trình ngay khi trả mô tả công cụ. Đặt UTF-8 tường minh, không phụ thuộc biến môi trường."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    return stream


def serve(stdin=None, stdout=None, server: Server | None = None) -> None:
    stdin, stdout = stdin or _utf8(sys.stdin), stdout or _utf8(sys.stdout)
    server = server or Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        response = server.handle(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
