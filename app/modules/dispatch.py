"""Giao 1 lệnh hành động website cho widget của khách và (nếu không cần khách xác nhận) chờ kết quả THẬT (M3 + M4). Được gọi từ route nội bộ
/internal/actions/dispatch mà công cụ MCP của agent gọi (token ký theo (bot_id, run_id) đã kiểm ở route).

Backend không có DOM: ghi pending_widget_actions, đẩy lệnh qua Socket.IO (namespace /widget) vào room của khách, widget thực thi rồi báo kết quả qua HTTP POST
(app/widget/routes.py:action_result) -> Redis -> hàm này nhận và trả cho agent. Agent chỉ được biết "thành công" khi widget BÁO thành công.

Mọi điều kiện đều kiểm lại Ở ĐÂY (không tin phía MCP/agent): hành động phải đã duyệt và (nếu thanh toán) công tắc bot đang bật; hội thoại phải là của widget;
khách phải đang mở widget trên ĐÚNG website đã khai báo (module_urls.source_url — domain NGƯỜI DÙNG ĐÃ XÁC NHẬN, không phải detected_domain tự đoán);
giới hạn số lần gọi mỗi lượt.
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass

from flask import current_app

from app.models import Bot, Conversation, PendingWidgetAction
from app.modules import service
from app.widget import channel
from core.context_engine.agent import protocol
from core.website_actions import spec as spec_mod
from extensions import db, redis_client

MAX_PARAM_CHARS = 300
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

STATUS_DONE, STATUS_FAILED = "done", "failed"
STATUS_TIMEOUT, STATUS_AWAITING = "timeout", "awaiting_confirmation"
STATUS_UNAVAILABLE, STATUS_LIMIT, STATUS_INVALID = "unavailable", "limit", "invalid"


@dataclass
class DispatchResult:
    status: str
    reason: str | None = None
    data: str | None = None

    def as_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason, "data": self.data}


def clean_params(action_spec: dict, params) -> dict:
    """Chỉ giữ tham số CÓ khai báo trong selector_spec (value_from_slot), ép chuỗi, bỏ ký tự điều khiển, cắt độ dài. Tham số lạ do model bịa bị bỏ."""
    allowed = spec_mod.params_of(action_spec)
    out = {}
    if isinstance(params, dict):
        for name in allowed:
            value = params.get(name)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip():
                out[name] = _CONTROL_CHARS.sub("", str(value)).strip()[:MAX_PARAM_CHARS]
    return out


def _run_context(bot_id: int, run_id: str) -> dict | None:
    raw = redis_client.get(protocol.Keys(current_app.config["AGENT_REDIS_PREFIX"]).run_context(run_id))
    try:
        context = json.loads(raw) if raw else None
    except ValueError:
        return None
    return context if isinstance(context, dict) and context.get("bot_id") == bot_id else None


def dispatch(bot: Bot, *, run_id: str, tool_name: str, params, wait_seconds: float | None = None) -> DispatchResult:
    wait_seconds = current_app.config["AGENT_ACTION_WAIT_SECONDS"] if wait_seconds is None else wait_seconds
    context = _run_context(bot.id, run_id)
    conversation_id = context.get("conversation_id") if context else None
    conversation = (
        Conversation.query.filter_by(id=conversation_id, bot_id=bot.id).first()
        if isinstance(conversation_id, int) and not isinstance(conversation_id, bool) else None
    )
    if conversation is None or conversation.channel != "web_widget" or not conversation.visitor_id:
        return DispatchResult(STATUS_UNAVAILABLE, "no_widget_session")

    action = service.resolve_tool(bot.id, tool_name)   # đã duyệt + (thanh toán => công tắc bật) + đúng bot
    if action is None:
        return DispatchResult(STATUS_UNAVAILABLE, "action_not_available")

    counter = protocol.Keys(current_app.config["AGENT_REDIS_PREFIX"]).actions(run_id)
    if redis_client.incr(counter) > current_app.config["AGENT_MAX_ACTION_CALLS"]:
        redis_client.expire(counter, 300)
        return DispatchResult(STATUS_LIMIT)
    redis_client.expire(counter, 300)

    module_url = service.url_of(action)
    hosts = channel.connected_hosts(bot.public_id, conversation.visitor_id)
    if not hosts:
        return DispatchResult(STATUS_UNAVAILABLE, "customer_widget_not_connected")
    if not any(spec_mod.same_site(host, module_url.source_url) for host in hosts):
        return DispatchResult(STATUS_UNAVAILABLE, "widget_not_on_the_configured_website")  # bot nhúng ở domain khác domain đã khai báo: không thực thi, chỉ trả lời như thường

    clean = clean_params(action.selector_spec, params)
    if action.action_type == "fill_form" and not clean:
        return DispatchResult(STATUS_INVALID, "missing: " + ", ".join(spec_mod.params_of(action.selector_spec)))

    confirm = service.effective_risk_of(action) == "payment"   # M4: thanh toán luôn cần khách bấm Đồng ý ngay trong khung chat, độc lập với cấu hình
    pending = PendingWidgetAction(
        bot_id=bot.id, conversation_id=conversation.id, action_id=action.id, action_name=tool_name[:80], token=secrets.token_urlsafe(24),
        params=clean, requires_confirm=confirm, status="pending",
    )
    db.session.add(pending)
    db.session.commit()

    channel.emit_action(bot.public_id, conversation.visitor_id, {
        "id": pending.id, "token": pending.token, "type": action.action_type, "name": tool_name, "label": action.description,
        "spec": action.selector_spec, "params": clean, "confirm": confirm, "expect_host": module_url.source_url,
    })
    if confirm:
        return DispatchResult(STATUS_AWAITING)  # không chặn lượt chat chờ khách; kết quả (nếu khách đồng ý) vẫn được ghi vào pending_widget_actions
    result = channel.wait_result(pending.id, wait_seconds)
    if result is None:
        return DispatchResult(STATUS_TIMEOUT)
    return DispatchResult(result.get("status") or STATUS_FAILED, result.get("reason"), result.get("data"))
