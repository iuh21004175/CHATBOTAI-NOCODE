"""Cấu hình hiệu lực: công tắc cố định (FIXED_TOGGLES) + tham số nội bộ RAG/Context Engine luôn dùng mặc định (ENGINE_INTERNAL) +
chế độ Cơ bản/Nâng cao (config_tier) + mặc định khớp cột DB."""
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


class EngineInternalIsFixed(unittest.TestCase):
    """Tham số nội bộ RAG/Context Engine không phải thứ chủ bot chỉnh: engine luôn dùng mặc định, bất kể giá trị lưu và chế độ."""

    STORED = dict(
        rag_top_k=12, rag_rerank_top_n=6, rag_distance_threshold=1.2, rag_max_context_tokens=2500, max_candidate_count=9,
        intent_confidence_threshold=0.5, slot_completion_threshold=0.5,
        context_pressure_warning=0.5, context_pressure_hard_limit=0.6, max_context_tokens=9000,
    )

    def test_stored_values_are_ignored_in_both_tiers(self):
        self.assertEqual(set(self.STORED), set(cfg.ENGINE_INTERNAL), "test phải phủ đúng danh sách ENGINE_INTERNAL")
        for tier in ("basic", "advanced", "expert", None):
            s = EngineSettings.from_model(row(config_tier=tier, **self.STORED))
            for name in self.STORED:
                self.assertEqual(getattr(s, name), DEFAULTS[name], (tier, name))

    def test_other_fields_are_still_read_from_the_row(self):
        s = EngineSettings.from_model(row(memory_max_items=15, memory_min_confidence=0.2))
        self.assertEqual((s.memory_max_items, s.memory_min_confidence), (15, 0.2))


class ConfigTier(unittest.TestCase):
    """Chế độ Cơ bản: chỉ mức nhớ (recent_*); Nâng cao: thêm summary_*. Trường không thuộc chế độ hiện tại dùng mặc định."""

    STORED = dict(recent_message_limit=20, recent_token_limit=3000, summary_trigger_tokens=9000, summary_max_tokens=800)

    def test_advanced_honours_all_four_memory_fields(self):
        for tier in ("advanced", "expert"):
            s = EngineSettings.from_model(row(config_tier=tier, **self.STORED))
            for name, expected in self.STORED.items():
                self.assertEqual(getattr(s, name), expected, (tier, name))

    def test_basic_and_missing_tier_use_default_summary_but_keep_the_saved_memory_level(self):
        for tier in ("basic", None, "la"):
            s = EngineSettings.from_model(row(config_tier=tier, **self.STORED))
            self.assertEqual((s.recent_message_limit, s.recent_token_limit), (20, 3000), tier)
            self.assertEqual((s.summary_trigger_tokens, s.summary_max_tokens),
                             (DEFAULTS["summary_trigger_tokens"], DEFAULTS["summary_max_tokens"]), tier)

    def test_row_without_tier_attribute_is_basic(self):
        stub = row(**self.STORED)
        self.assertFalse(hasattr(stub, "config_tier"))
        self.assertEqual(EngineSettings.from_model(stub).summary_max_tokens, DEFAULTS["summary_max_tokens"])

    def test_normalize_tier(self):
        self.assertEqual([cfg.normalize_tier(v) for v in ("basic", "advanced", "expert", None, "", "x")],
                         ["basic", "advanced", "advanced", "basic", "basic", "basic"])

    def test_memory_levels_are_within_ranges_and_medium_is_the_default(self):
        for key, (messages, tokens) in cfg.MEMORY_LEVELS.items():
            self.assertTrue(cfg.RANGES["recent_message_limit"][1] <= messages <= cfg.RANGES["recent_message_limit"][2], key)
            self.assertTrue(cfg.RANGES["recent_token_limit"][1] <= tokens <= cfg.RANGES["recent_token_limit"][2], key)
        self.assertEqual(cfg.MEMORY_LEVELS["medium"], (DEFAULTS["recent_message_limit"], DEFAULTS["recent_token_limit"]))

    def test_memory_level_of_matches_presets_else_custom(self):
        for key, pair in cfg.MEMORY_LEVELS.items():
            self.assertEqual(cfg.memory_level_of(*pair), key)
        self.assertEqual(cfg.memory_level_of(7, 1500), cfg.CUSTOM_MEMORY_LEVEL)
        self.assertEqual(cfg.memory_level_of(None, None), cfg.CUSTOM_MEMORY_LEVEL)

    def test_every_advanced_field_has_a_range_and_summary_fields_are_advanced_only(self):
        self.assertTrue(set(cfg.ADVANCED_FIELDS) <= set(cfg.RANGES))
        self.assertTrue(set(cfg.ADVANCED_ONLY) <= set(cfg.ADVANCED_FIELDS))
        self.assertFalse(set(cfg.ADVANCED_FIELDS) & set(cfg.ENGINE_INTERNAL))

    def test_low_confidence_reply_mode_and_messages_always_honoured(self):
        s = EngineSettings.from_model(row(low_confidence_reply_mode="decline", low_confidence_decline_message="  Xin lỗi.  "))
        self.assertEqual(s.low_confidence_reply_mode, "decline")
        self.assertEqual(s.low_confidence_decline_message, "Xin lỗi.")


class Robustness(unittest.TestCase):
    """Dữ liệu cũ/sai trong DB không được làm hỏng engine."""

    def test_out_of_range_values_are_clamped(self):
        s = EngineSettings.from_model(row(config_tier="advanced", recent_message_limit=999, recent_token_limit=1,
                                          summary_trigger_tokens=10**9, memory_min_confidence=-3))
        self.assertEqual(s.recent_message_limit, 30)
        self.assertEqual(s.recent_token_limit, 200)
        self.assertEqual(s.summary_trigger_tokens, 20000)
        self.assertEqual(s.memory_min_confidence, 0.0)

    def test_garbage_types_fall_back_to_defaults(self):
        s = EngineSettings.from_model(row(config_tier="advanced", recent_message_limit="abc", memory_min_confidence=float("nan"), low_confidence_reply_mode="explode"))
        self.assertEqual(s.recent_message_limit, DEFAULTS["recent_message_limit"])
        self.assertEqual(s.memory_min_confidence, DEFAULTS["memory_min_confidence"])
        self.assertEqual(s.low_confidence_reply_mode, "ask_clarify")

    def test_null_columns_use_defaults_and_stored_temperature_is_ignored(self):
        s = EngineSettings.from_model(row(recent_message_limit=None, temperature=0.0, max_tokens=None, language="fr"))
        self.assertEqual(s.recent_message_limit, DEFAULTS["recent_message_limit"])
        self.assertEqual(s.temperature, cfg.DEFAULT_TEMPERATURE, "temperature không còn cấu hình theo bot")
        self.assertEqual(s.max_tokens, 500)
        self.assertEqual(s.language, "vi")


if __name__ == "__main__":
    unittest.main()
