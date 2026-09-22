"""Cấu hình hiệu lực theo tier + mặc định khớp cột DB."""
import unittest
from types import SimpleNamespace

from app.models import BotSettings
from core.context_engine import settings as cfg
from core.context_engine.settings import DEFAULTS, EngineSettings


def row(**overrides):
    """Bản ghi bot_settings giả: mọi trường engine = giá trị lưu; mặc định = DEFAULTS."""
    base = dict(DEFAULTS, config_tier="expert", language="vi", instructions="", temperature=0.7, max_tokens=500)
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


class Tiers(unittest.TestCase):
    def test_basic_ignores_stored_advanced_and_expert_values(self):
        s = EngineSettings.from_model(row(config_tier="basic", recent_message_limit=25, rag_top_k=15, max_candidate_count=9,
                                         rag_distance_threshold=0.5, low_confidence_reply_mode="decline"))
        self.assertEqual(s.recent_message_limit, 10, "basic: recent_message_limit cố định = 10")
        self.assertEqual(s.rag_top_k, DEFAULTS["rag_top_k"])
        self.assertEqual(s.rag_distance_threshold, DEFAULTS["rag_distance_threshold"])
        self.assertEqual(s.low_confidence_reply_mode, "ask_clarify")

    def test_basic_honours_the_three_switches(self):
        s = EngineSettings.from_model(row(config_tier="basic", rag_enabled=False, clarification_enabled=False,
                                         structured_memory_enabled=False, summary_enabled=False))
        self.assertFalse(s.rag_enabled)
        self.assertFalse(s.clarification_enabled)
        self.assertFalse(s.structured_memory_enabled)
        self.assertFalse(s.summary_enabled)

    def test_advanced_honours_its_fields_but_not_expert_only_ones(self):
        s = EngineSettings.from_model(row(config_tier="advanced", recent_message_limit=20, rag_top_k=12, rag_distance_threshold=1.2,
                                         max_clarification_turns=4, max_candidate_count=9, memory_min_confidence=0.2))
        self.assertEqual((s.recent_message_limit, s.rag_top_k, s.rag_distance_threshold, s.max_clarification_turns), (20, 12, 1.2, 4))
        self.assertEqual(s.max_candidate_count, DEFAULTS["max_candidate_count"])
        self.assertEqual(s.memory_min_confidence, DEFAULTS["memory_min_confidence"])

    def test_expert_honours_everything(self):
        s = EngineSettings.from_model(row(config_tier="expert", max_candidate_count=9, low_confidence_reply_mode="decline",
                                         low_confidence_decline_message="  Xin lỗi.  "))
        self.assertEqual(s.max_candidate_count, 9)
        self.assertEqual(s.low_confidence_reply_mode, "decline")
        self.assertEqual(s.low_confidence_decline_message, "Xin lỗi.")

    def test_unknown_tier_is_basic(self):
        self.assertEqual(EngineSettings.from_model(row(config_tier="root")).config_tier, "basic")

    def test_downgrading_expert_to_basic_drops_hidden_values(self):
        stored = dict(rag_distance_threshold=0.3, max_candidate_count=1)
        self.assertEqual(EngineSettings.from_model(row(config_tier="expert", **stored)).max_candidate_count, 1)
        self.assertEqual(EngineSettings.from_model(row(config_tier="basic", **stored)).max_candidate_count, DEFAULTS["max_candidate_count"])


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
