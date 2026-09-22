"""Cấu hình hiệu lực: công tắc cố định (FIXED_TOGGLES) + mọi thông số số luôn honour giá trị lưu + mặc định khớp cột DB."""
import unittest
from types import SimpleNamespace

from app.models import BotSettings
from core.context_engine import settings as cfg
from core.context_engine.settings import DEFAULTS, FIXED_TOGGLES, EngineSettings


def row(**overrides):
    """Bản ghi bot_settings giả: mọi trường engine = giá trị lưu; mặc định = DEFAULTS."""
    base = dict(DEFAULTS, language="vi", instructions="", temperature=0.7, max_tokens=500)
    base.update(overrides)
    return SimpleNamespace(**base)


class DefaultsMatchDatabase(unittest.TestCase):
    def test_every_default_matches_model_default_and_server_default(self):
        columns = BotSettings.__table__.c
        for name, expected in DEFAULTS.items():
            column = columns[name]
            if expected is None:
                self.assertTrue(column.nullable, name)
                continue
            self.assertEqual(column.default.arg, expected, f"{name}: default phía Python")
            server = str(column.server_default.arg)
            if isinstance(expected, bool):
                self.assertEqual(server, "1" if expected else "0", f"{name}: server_default")
            elif isinstance(expected, str):
                self.assertEqual(server, expected, f"{name}: server_default")
            else:
                self.assertEqual(float(server), float(expected), f"{name}: server_default")

    def test_ranges_contain_their_defaults(self):
        for name, (kind, low, high) in cfg.RANGES.items():
            self.assertTrue(low <= DEFAULTS[name] <= high, name)


class FixedToggles(unittest.TestCase):
    """Các công tắc bật/tắt tính năng không còn đọc từ DB — luôn dùng FIXED_TOGGLES bất kể giá trị đang lưu."""

    def test_stored_true_values_are_overridden_by_fixed_toggles(self):
        s = EngineSettings.from_model(row(**{name: True for name in FIXED_TOGGLES}))
        for name, expected in FIXED_TOGGLES.items():
            self.assertEqual(getattr(s, name), expected, name)

    def test_stored_false_values_are_overridden_by_fixed_toggles(self):
        s = EngineSettings.from_model(row(**{name: False for name in FIXED_TOGGLES}))
        for name, expected in FIXED_TOGGLES.items():
            self.assertEqual(getattr(s, name), expected, name)

    def test_structured_memory_is_fixed_off(self):
        self.assertFalse(FIXED_TOGGLES["structured_memory_enabled"], "trùng chức năng với tóm tắt hội thoại")

    def test_engine_settings_has_no_config_tier(self):
        self.assertFalse(hasattr(EngineSettings.defaults(), "config_tier"), "khái niệm mức cấu hình đã bị bỏ")


class NoTierGating(unittest.TestCase):
    """Mọi thông số số (RANGES) luôn honour giá trị đang lưu — không còn khái niệm mức cấu hình giới hạn trường nào."""

    def test_every_numeric_field_honours_stored_value(self):
        stored = dict(
            recent_message_limit=20, rag_top_k=12, rag_distance_threshold=1.2,
            max_candidate_count=9, memory_min_confidence=0.2, recent_token_limit=3000, summary_max_tokens=800,
            memory_max_items=15, intent_confidence_threshold=0.5, slot_completion_threshold=0.5, rag_rerank_top_n=6,
            rag_max_context_tokens=2500, context_pressure_warning=0.5, context_pressure_hard_limit=0.6,
            max_context_tokens=9000,
        )
        s = EngineSettings.from_model(row(**stored))
        for name, expected in stored.items():
            self.assertEqual(getattr(s, name), expected, name)

    def test_low_confidence_reply_mode_and_messages_always_honoured(self):
        s = EngineSettings.from_model(row(low_confidence_reply_mode="decline", low_confidence_decline_message="  Xin lỗi.  "))
        self.assertEqual(s.low_confidence_reply_mode, "decline")
        self.assertEqual(s.low_confidence_decline_message, "Xin lỗi.")


class Robustness(unittest.TestCase):
    """Dữ liệu cũ/sai trong DB không được làm hỏng engine."""

    def test_out_of_range_values_are_clamped(self):
        s = EngineSettings.from_model(row(rag_top_k=999, memory_min_confidence=-3, rag_distance_threshold=50, max_context_tokens=1))
        self.assertEqual(s.rag_top_k, 20)
        self.assertEqual(s.memory_min_confidence, 0.0)
        self.assertEqual(s.rag_distance_threshold, 1.90)
        self.assertEqual(s.max_context_tokens, 2000)

    def test_garbage_types_fall_back_to_defaults(self):
        s = EngineSettings.from_model(row(rag_top_k="abc", memory_min_confidence=float("nan"), low_confidence_reply_mode="explode"))
        self.assertEqual(s.rag_top_k, DEFAULTS["rag_top_k"])
        self.assertEqual(s.memory_min_confidence, DEFAULTS["memory_min_confidence"])
        self.assertEqual(s.low_confidence_reply_mode, "ask_clarify")

    def test_rerank_never_exceeds_top_k(self):
        s = EngineSettings.from_model(row(rag_top_k=3, rag_rerank_top_n=9))
        self.assertEqual(s.rag_rerank_top_n, 3)

    def test_inverted_pressure_thresholds_reset(self):
        s = EngineSettings.from_model(row(context_pressure_warning=0.9, context_pressure_hard_limit=0.7))
        self.assertLess(s.context_pressure_warning, s.context_pressure_hard_limit)

    def test_null_columns_use_defaults_and_zero_temperature_is_kept(self):
        s = EngineSettings.from_model(row(rag_top_k=None, temperature=0.0, max_tokens=None, language="fr"))
        self.assertEqual(s.rag_top_k, DEFAULTS["rag_top_k"])
        self.assertEqual(s.temperature, 0.0)
        self.assertEqual(s.max_tokens, 500)
        self.assertEqual(s.language, "vi")


if __name__ == "__main__":
    unittest.main()
