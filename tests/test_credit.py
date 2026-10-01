"""Phase D — Cost Engine + Credit Engine: giá vốn từ usage thật, giữ chỗ/quyết toán Credit theo từng lượt chạy agent, sổ cái chỉ-thêm, cấp Credit
dùng thử, trần chi phí/lượt, trang /profile. Runner GIẢ (không worker/DeepSeek). Phần thuần không cần DB; còn lại cần DB *_test
(xem tests/README.md). Đây là kiểm tra hồi quy ở mức mã — KHÔNG thay cho kiểm thử chức năng thực tế."""
import random
import unittest
from datetime import datetime
from decimal import Decimal
from unittest import mock

from app.auth import service as auth_service
from app.credits import service as credits
from app.models import AgentExecution, CreditAccount, CreditTransaction, ExecutionCost, TeamMember
from config import Config
from core.context_engine import cost_estimate as ce
from core.context_engine import execution_cost as xc
from core.context_engine.agent import runtime as rt
from core.context_engine.settings import EngineSettings
from tests.test_agent_flow import AgentFlowCase
from tests.test_agent_runtime import FakeAgentRunner

D = Decimal
TRIAL = Config.TRIAL_CREDIT_VND
# 3 lượt suy luận của FakeAgentRunner: mỗi lượt 300 cache-hit + 200 cache-miss + 40 đầu ra
CALLS = [{"kind": "main", "prompt_tokens": 500, "completion_tokens": 40, "cache_hit_tokens": 300, "cache_miss_tokens": 200}] * 3
OFF_PEAK_MOMENT = datetime(2026, 9, 26, 3, 0)   # thứ Bảy
PEAK_MOMENT = datetime(2026, 9, 25, 3, 30)      # thứ Sáu 10:30 giờ Việt Nam


class PureCost(unittest.TestCase):
    def test_peak_hours_follow_vietnam_time_on_weekdays(self):
        cases = [
            (datetime(2026, 9, 25, 0, 59), False),  # T6 07:59
            (datetime(2026, 9, 25, 1, 0), True),    # T6 08:00
            (datetime(2026, 9, 25, 3, 59), True),   # T6 10:59
            (datetime(2026, 9, 25, 4, 0), False),   # T6 11:00
            (datetime(2026, 9, 25, 6, 0), True),    # T6 13:00
            (datetime(2026, 9, 25, 9, 59), True),   # T6 16:59
            (datetime(2026, 9, 25, 10, 0), False),  # T6 17:00
            (datetime(2026, 9, 26, 3, 0), False),   # T7 10:00
            (datetime(2026, 9, 27, 3, 0), False),   # CN 10:00
        ]
        for moment, expected in cases:
            self.assertEqual(xc.is_peak_hours(moment), expected, moment)

    def test_llm_cost_sums_hit_miss_and_output_separately(self):
        result = xc.llm_cost(CALLS, OFF_PEAK_MOMENT)
        self.assertEqual((result.input_tokens, result.cache_hit_tokens, result.cache_miss_tokens, result.output_tokens), (1500, 900, 600, 120))
        self.assertEqual(result.cost_vnd, D("4.3151"))  # (900·0,003 + 600·0,15 + 120·0,60)/1e6 USD × 26.200
        self.assertTrue(result.reported)
        self.assertGreater(xc.llm_cost(CALLS, PEAK_MOMENT).cost_vnd, result.cost_vnd, "giờ cao điểm đắt hơn")

    def test_unreported_usage_is_flagged_not_invented(self):
        result = xc.llm_cost([{"prompt_tokens": None, "completion_tokens": None}], OFF_PEAK_MOMENT)
        self.assertEqual((result.cost_vnd, result.reported), (D("0.0000"), False))
        partial = xc.llm_cost([{"prompt_tokens": 100, "completion_tokens": 10, "cache_hit_tokens": None, "cache_miss_tokens": None}], OFF_PEAK_MOMENT)
        self.assertEqual((partial.cache_hit_tokens, partial.cache_miss_tokens), (0, 100), "chỉ có tổng prompt -> tính cả là cache-miss")

    def test_price_is_cost_times_markup_plus_tool_and_infra(self):
        llm = xc.llm_cost(CALLS, OFF_PEAK_MOMENT)
        plain = xc.price_execution(llm, markup=D("1.0"))
        self.assertEqual((plain.total_cost_vnd, plain.billed_vnd), (D("4.3151"), D("4.3151")))
        priced = xc.price_execution(llm, markup=D("2.5"), infra_cost=D("1"))
        self.assertEqual((priced.total_cost_vnd, priced.billed_vnd), (D("5.3151"), xc.money(D("5.3151") * D("2.5"))))

    def test_reserve_estimate_is_an_upper_bound_and_grows_with_iterations_and_markup(self):
        settings = EngineSettings.defaults()
        kw = dict(chunk_size=450, intents=[], max_question_tokens=ce.question_tokens_for_chars(1000))
        one = xc.estimate_reserve_vnd(settings, max_iterations=1, markup=D("1"), **kw)
        four = xc.estimate_reserve_vnd(settings, max_iterations=4, markup=D("1"), **kw)
        doubled = xc.estimate_reserve_vnd(settings, max_iterations=4, markup=D("2"), **kw)
        self.assertLess(one, four)
        self.assertEqual(doubled, xc.money(four * 2))
        self.assertGreater(four, xc.llm_cost(CALLS, PEAK_MOMENT).cost_vnd * 10, "giữ chỗ phải cao hơn nhiều so với 1 lượt điển hình")


class CreditCase(AgentFlowCase):
    def setUp(self):
        super().setUp()
        self.runner = self.use_agent(FakeAgentRunner(("finish_answer", {"answer": "Gói Pro 500.000đ/tháng", "intent_confidence": 0.95}), steps=3))

    def account(self, team=None):
        self.db.session.expire_all()
        return CreditAccount.query.filter_by(team_id=(team or self.team).id).one()

    def ledger(self, team=None):
        self.db.session.expire_all()
        return CreditTransaction.query.filter_by(team_id=(team or self.team).id).order_by(CreditTransaction.id).all()

    def set_balance(self, value):
        """Đưa số dư về `value` bằng giao dịch adjustment (giữ sổ cái đúng bất biến)."""
        account = credits.ensure_account(self.team.id)
        self.db.session.commit()
        delta = D(value) - D(account.balance_vnd)
        if delta:
            credits._add_transaction(account, "adjustment", delta, note="test")
            self.db.session.commit()

    def assert_ledger_matches_balance(self, team=None):
        rows = self.ledger(team)
        self.assertEqual(sum((r.amount_vnd for r in rows), D(0)), self.account(team).balance_vnd)
        running = D(0)
        for r in rows:
            running += r.amount_vnd
            self.assertEqual(r.balance_after_vnd, running, f"balance_after sai ở giao dịch {r.id}")
            self.assertGreaterEqual(running, 0, "số dư không bao giờ âm")


class TrialGrant(CreditCase):
    def test_register_grants_the_trial_credit_exactly_once(self):
        user = auth_service.register("Người Mới", "moi@example.com", "matkhau-123", "Team Mới")
        team_id = TeamMember.query.filter_by(user_id=user.id).one().team_id
        rows = CreditTransaction.query.filter_by(team_id=team_id).all()
        self.assertEqual([(r.type, r.amount_vnd, r.balance_after_vnd, r.execution_id) for r in rows], [("trial_grant", TRIAL, TRIAL, None)])
        self.assertEqual(CreditAccount.query.filter_by(team_id=team_id).one().balance_vnd, TRIAL)
        credits.ensure_account(team_id)  # gọi lại: không cấp thêm
        self.db.session.commit()
        self.assertEqual(CreditTransaction.query.filter_by(team_id=team_id).count(), 1)

    def test_oauth_signup_and_new_team_also_get_the_trial_and_an_existing_user_gets_nothing_more(self):
        from app.team import service as team_service

        user = auth_service.find_or_create_user("oauth@example.com", "Oauth User")
        again = auth_service.find_or_create_user("oauth@example.com", "Oauth User")
        self.assertEqual(user.id, again.id)
        team, error = team_service.create_team(user, "Nhóm thứ hai")
        self.assertIsNone(error)
        self.assertEqual(CreditTransaction.query.filter_by(type="trial_grant").count(), 2, "mỗi team mới đúng 1 lần, đăng nhập lại không cấp thêm")
        self.assertEqual(CreditAccount.query.count(), 2)
        self.assertEqual(self.account(team).balance_vnd, TRIAL)

    def test_a_team_created_before_phase_d_gets_the_trial_once_on_first_use(self):
        self.assertEqual(CreditAccount.query.count(), 0)
        self.ask()
        self.assertEqual(len([r for r in self.ledger() if r.type == "trial_grant"]), 1)
        self.ask()
        self.assertEqual(len([r for r in self.ledger() if r.type == "trial_grant"]), 1)


class ReserveAndSettle(CreditCase):
    def test_enough_balance_reserve_then_release_then_charge_the_real_cost(self):
        conversation, message = self.ask()
        self.assertEqual(message.decision_trace["decision"], "answer")
        rows = self.ledger()
        self.assertEqual([r.type for r in rows], ["trial_grant", "reserve", "release", "execution_charge"])
        reserve, release, charge = rows[1], rows[2], rows[3]
        self.assertEqual(release.amount_vnd, -reserve.amount_vnd, "hoàn đủ phần giữ chỗ")
        self.assertEqual(charge.amount_vnd, D("-4.3151"))
        (execution,) = self.executions()
        self.assertEqual((release.execution_id, charge.execution_id), (execution.id, execution.id))
        self.assertEqual(self.account().balance_vnd, TRIAL - D("4.3151"))
        self.assert_ledger_matches_balance()

    def test_execution_costs_row_matches_the_real_usage_and_is_linked_1_to_1(self):
        self.ask()
        (execution,) = self.executions()
        cost = ExecutionCost.query.filter_by(execution_id=execution.id).one()
        self.assertEqual((cost.team_id, cost.bot_id), (self.team.id, self.bot.id))
        self.assertEqual((cost.llm_input_tokens, cost.llm_cache_hit_tokens, cost.llm_cache_miss_tokens, cost.llm_output_tokens), (1500, 900, 600, 120))
        self.assertEqual((cost.llm_cost_vnd, cost.tool_cost_vnd, cost.infra_cost_vnd, cost.total_cost_vnd), (D("4.3151"), D(0), D(0), D("4.3151")))
        self.assertEqual((cost.markup_multiplier, cost.billed_vnd, cost.charged_vnd, cost.uncollected_vnd), (D(1), D("4.3151"), D("4.3151"), D(0)))
        self.assertTrue(cost.usage_reported)

    def test_markup_multiplier_scales_the_credit_charged_but_not_the_cost(self):
        self.set_settings(max_cost_per_execution_vnd=D("5000"))  # trần tính theo giá BÁN: hệ số 2 làm ước tính giữ chỗ gấp đôi
        with mock.patch.object(Config, "PLATFORM_MARKUP_MULTIPLIER", D("2.0")):
            self.ask()
        (execution,) = self.executions()
        cost = ExecutionCost.query.filter_by(execution_id=execution.id).one()
        self.assertEqual((cost.total_cost_vnd, cost.billed_vnd), (D("4.3151"), D("8.6302")))
        self.assertEqual(self.account().balance_vnd, TRIAL - D("8.6302"))

    def test_every_turn_is_charged_and_the_ledger_stays_balanced(self):
        conversation = self.conversation()
        for _ in range(3):
            self.ask(conversation=conversation)
        self.assertEqual(ExecutionCost.query.count(), 3)
        self.assertEqual(self.account().balance_vnd, TRIAL - D("4.3151") * 3)
        self.assert_ledger_matches_balance()

    def test_reserve_needs_enough_balance_and_writes_nothing_when_short(self):
        credits.ensure_account(self.team.id)
        self.db.session.commit()
        self.assertIsNone(credits.reserve(self.team.id, TRIAL + 1))
        self.assertEqual([r.type for r in self.ledger()], ["trial_grant"])
        reservation = credits.reserve(self.team.id, TRIAL)  # đúng bằng số dư: được
        self.assertEqual(reservation.amount_vnd, TRIAL)
        self.assertEqual(self.account().balance_vnd, 0)
        self.assertIsNone(credits.reserve(self.team.id, D("0.0001")))

    def test_execution_dearer_than_the_reserve_is_charged_the_difference_too(self):
        credits.ensure_account(self.team.id)
        self.db.session.commit()
        reservation = credits.reserve(self.team.id, D("10"))
        (execution,) = [self.service._record_agent_execution(self.bot, None, {"execution_id": "dear", "status": "completed", "started_at": 1.0})]
        llm = xc.llm_cost(CALLS, OFF_PEAK_MOMENT)
        price = xc.ExecutionPrice(D("50"), D(0), D(0), D("50"), D(1), D("50"))
        credits.settle(reservation, execution_id=execution.id, bot_id=self.bot.id, llm=llm, price=price)
        self.db.session.commit()
        rows = self.ledger()
        self.assertEqual([(r.type, r.amount_vnd) for r in rows[1:]], [("reserve", D("-10")), ("release", D("10")), ("execution_charge", D("-50"))])
        self.assertEqual(self.account().balance_vnd, TRIAL - 50)
        self.assert_ledger_matches_balance()


class NeverNegative(CreditCase):
    def test_cost_beyond_the_balance_charges_only_the_balance_logs_the_shortfall_and_blocks_the_next_turn(self):
        self.set_balance(D("30"))
        reservation = credits.reserve(self.team.id, D("10"))
        execution = self.service._record_agent_execution(self.bot, None, {"execution_id": "huge", "status": "completed", "started_at": 1.0})
        price = xc.ExecutionPrice(D("500"), D(0), D(0), D("500"), D(1), D("500"))
        with self.assertLogs("app.credits.service", level="WARNING") as logs:
            cost = credits.settle(reservation, execution_id=execution.id, bot_id=self.bot.id, llm=xc.llm_cost(CALLS, OFF_PEAK_MOMENT), price=price)
        self.db.session.commit()
        self.assertEqual((cost.billed_vnd, cost.charged_vnd, cost.uncollected_vnd), (D("500"), D("30"), D("470")))
        self.assertIn("thiếu 470", logs.output[0])
        self.assertEqual(self.account().balance_vnd, 0)
        self.assert_ledger_matches_balance()

        conversation, blocked = self.ask("Câu tiếp theo?")
        self.assertEqual(blocked.content, Config.DEFAULT_OUT_OF_CREDIT_MESSAGE)
        self.assertEqual(blocked.decision_trace["decision"], "decline")
        self.assertEqual(blocked.decision_trace["reasons"], [credits.BLOCK_INSUFFICIENT])
        self.assertEqual(self.runner.count, 0, "agent không được chạy khi hết Credit")
        self.assertEqual(AgentExecution.query.count(), 1, "chỉ có lượt 'huge' đã xảy ra; lượt bị chặn không tạo Execution")
        self.assertEqual(self.account().balance_vnd, 0)

    def test_out_of_credit_uses_the_bot_owners_message_when_set(self):
        self.set_balance(D("0"))
        self.set_settings(out_of_credit_message="  Hết lượt rồi nhé, liên hệ 1900.  ")
        _, message = self.ask()
        self.assertEqual(message.content, "Hết lượt rồi nhé, liên hệ 1900.")
        self.assertEqual(ExecutionCost.query.count(), 0)

    def test_blocked_turn_costs_nothing_and_leaves_no_reserve_or_half_written_state(self):
        from app.models import ConversationState

        self.set_balance(D("0"))
        self.ask()
        self.assertEqual([r.type for r in self.ledger()], ["trial_grant", "adjustment"])
        self.assertEqual(ConversationState.query.count(), 0)

    def test_random_operations_keep_the_ledger_equal_to_the_balance_and_never_negative(self):
        rng = random.Random(20260925)
        credits.ensure_account(self.team.id)
        self.db.session.commit()
        holds = []
        for step in range(60):
            action = rng.choice(["reserve", "reserve", "settle", "release", "topup"])
            if action == "reserve":
                held = credits.reserve(self.team.id, D(rng.randint(1, 4000)) / 4)
                if held:
                    holds.append(held)
            elif action == "settle" and holds:
                held = holds.pop(rng.randrange(len(holds)))
                execution = self.service._record_agent_execution(self.bot, None, {"execution_id": f"r{step}", "status": "completed", "started_at": 1.0})
                billed = D(rng.randint(0, 6000)) / 4
                credits.settle(held, execution_id=execution.id, bot_id=self.bot.id, llm=xc.llm_cost([], OFF_PEAK_MOMENT),
                               price=xc.ExecutionPrice(billed, D(0), D(0), billed, D(1), billed))
                self.db.session.commit()
            elif action == "release" and holds:
                credits.release_unused(holds.pop(rng.randrange(len(holds))))
                self.db.session.commit()
            elif action == "topup":
                account = credits.ensure_account(self.team.id)
                credits._add_transaction(account, "topup", D(rng.randint(1, 2000)))
                self.db.session.commit()
            self.assert_ledger_matches_balance()
        self.assertGreater(len(self.ledger()), 20)


class MaxCostPerExecution(CreditCase):
    def test_estimate_above_the_bots_limit_blocks_before_reserving_anything(self):
        self.set_settings(max_cost_per_execution_vnd=D("1"))
        _, message = self.ask()
        self.assertEqual(message.content, Config.DEFAULT_MAX_COST_MESSAGE)
        self.assertEqual(message.decision_trace["decision"], "decline")
        self.assertEqual(message.decision_trace["reasons"], [credits.BLOCK_MAX_COST])
        self.assertEqual(message.decision_trace["credit_gate"]["limit_vnd"], 1.0)
        self.assertEqual(CreditTransaction.query.filter_by(type="reserve").count(), 0, "chặn TRƯỚC khi giữ chỗ: không có dòng reserve")
        self.assertEqual((self.runner.count, AgentExecution.query.count(), ExecutionCost.query.count()), (0, 0, 0))
        self.assertEqual(credits.get_balance(self.team.id), TRIAL, "Credit không bị đụng tới")

    def test_a_limit_above_the_estimate_lets_the_turn_run(self):
        self.set_settings(max_cost_per_execution_vnd=D("5000"))
        _, message = self.ask()
        self.assertEqual(message.decision_trace["decision"], "answer")

    def test_the_limit_is_per_bot(self):
        other = self.service.create_bot(self.team.id, "Bot B")
        self.set_settings(max_cost_per_execution_vnd=D("1"))
        conversation = self.conversation(other)
        customer = self.add_message(conversation, "customer", "Giá?")
        message = self.service.reply_to_customer(other, conversation, customer)
        self.assertEqual(message.decision_trace["decision"], "answer", "bot khác không bị ảnh hưởng")

    def test_default_limit_covers_the_default_reserve(self):
        estimate = xc.estimate_reserve_vnd(EngineSettings.defaults(max_tokens=3000), chunk_size=450, intents=[],
                                           max_question_tokens=ce.question_tokens_for_chars(1000), max_iterations=Config.AGENT_MAX_ITERATIONS, markup=D(1))
        default = self.service.get_or_create_settings(self.bot).max_cost_per_execution_vnd
        self.assertGreater(default, estimate, "trần mặc định không được chặn oan bot dùng cấu hình tối đa")


class FailuresStillSettle(CreditCase):
    def test_a_failed_run_charges_the_usage_already_spent_and_refunds_the_rest(self):
        calls = CALLS[:2]
        info = {"execution_id": "job-fail", "status": "timeout", "iterations_used": 2, "total_llm_calls": 2, "tool_calls_used": 1, "stop_reason": "timeout",
                "error": "quá 25s", "started_at": 1.0, "finished_at": 26.0, "usage_calls": calls}

        class Boom:
            def run(self, **kw):
                raise rt.AgentRunError("AI Agent timeout", info)

        self.use_agent(Boom())
        conversation = self.conversation()
        customer = self.add_message(conversation, "customer", "Giá gói Pro?")
        with self.assertRaises(rt.AgentRunError):
            self.service.reply_to_customer(self.bot, conversation, customer)
        self.db.session.rollback()
        (execution,) = self.executions()
        cost = ExecutionCost.query.filter_by(execution_id=execution.id).one()
        self.assertEqual(cost.llm_cost_vnd, xc.llm_cost(calls, OFF_PEAK_MOMENT).cost_vnd)
        self.assertEqual([r.type for r in self.ledger()], ["trial_grant", "reserve", "release", "execution_charge"])
        self.assertEqual(self.account().balance_vnd, TRIAL - cost.charged_vnd)
        self.assert_ledger_matches_balance()

    def test_a_run_that_never_started_refunds_the_whole_reserve_and_writes_no_execution(self):
        class Down:
            def run(self, **kw):
                raise rt.AgentUnavailableError("chưa có worker")

        self.use_agent(Down())
        conversation = self.conversation()
        customer = self.add_message(conversation, "customer", "hi")
        with self.assertRaises(rt.AgentUnavailableError):
            self.service.reply_to_customer(self.bot, conversation, customer)
        self.db.session.rollback()
        self.assertEqual([r.type for r in self.ledger()], ["trial_grant", "reserve", "release"])
        self.assertEqual(self.account().balance_vnd, TRIAL, "không mất Credit vì lượt không chạy")
        self.assertEqual((AgentExecution.query.count(), ExecutionCost.query.count()), (0, 0))

    def test_a_write_error_after_the_agent_finished_does_not_leave_the_reserve_stuck(self):
        with mock.patch("app.dashboard.service.ctx_state.save_state_update", side_effect=RuntimeError("ghi lỗi")):
            with self.assertRaises(RuntimeError):
                self.ask()
        self.db.session.rollback()
        self.assertEqual(self.account().balance_vnd, TRIAL)
        self.assertEqual([r.type for r in self.ledger()], ["trial_grant", "reserve", "release"])
        self.assertEqual(ExecutionCost.query.count(), 0)

    def test_agent_mode_off_never_touches_credit(self):
        from core.context_engine.structured import LLMReply
        from tests.helpers import llm_json, usage

        self.use_agent(None)
        self.llm.replies.append(LLMReply(llm_json(), usage()))
        _, message = self.ask()
        self.assertEqual(message.content, "Gói Pro giá 500.000đ.")
        self.assertEqual((CreditAccount.query.count(), CreditTransaction.query.count(), ExecutionCost.query.count()), (0, 0, 0))


class LedgerIsAppendOnly(CreditCase):
    def test_updating_or_deleting_a_transaction_is_refused(self):
        self.ask()
        row = self.ledger()[0]
        row.amount_vnd = D("999999")
        with self.assertRaises(RuntimeError):
            self.db.session.commit()
        self.db.session.rollback()
        row = self.ledger()[0]
        self.db.session.delete(row)
        with self.assertRaises(RuntimeError):
            self.db.session.commit()
        self.db.session.rollback()
        self.assertEqual(self.ledger()[0].type, "trial_grant")


class ProfilePage(CreditCase):
    def setUp(self):
        super().setUp()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.team.user.id)
            session["team_id"] = self.team.id
            session["csrf_token"] = "tok"

    def test_requires_login(self):
        response = self.app.test_client().get("/profile")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_shows_balance_and_usage_history_without_reserve_release_tokens_or_cost_price(self):
        self.ask()
        self.ask()
        html = self.client.get("/profile").get_data(as_text=True)
        self.assertIn("AI Credit", html)
        self.assertIn("9.991,37đ", html, "10.000 − 2 × 4,3151 = 9.991,3698 → hiển thị làm tròn 2 chữ số")
        self.assertIn("-4,32đ", html)
        self.assertIn("Sử dụng trợ lý AI", html)
        self.assertIn("Credit dùng thử", html)
        self.assertEqual(html.count("Sử dụng trợ lý AI"), 2)
        for hidden in ("Giữ chỗ", "giữ chỗ", "reserve", "release", "giá vốn", "prompt_tokens", "cache", ">1500<", ">900<", ">120<"):
            self.assertNotIn(hidden, html, hidden)

    def test_only_shows_the_current_teams_credit(self):
        other = self.make_team("Team B")
        credits.ensure_account(other.id)
        credits._add_transaction(credits.ensure_account(other.id), "topup", D("77777"))
        self.db.session.commit()
        html = self.client.get("/profile").get_data(as_text=True)
        self.assertNotIn("77.777", html)
        self.assertIn("10.000đ", html)

    def test_sidebar_profile_link_points_to_the_page(self):
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn('href="/profile"', html)

    def test_number_format_filter(self):
        from app.profile.routes import format_vnd

        self.assertEqual(format_vnd(D("10000")), "10.000đ")
        self.assertEqual(format_vnd(D("9991.3698")), "9.991,37đ")
        self.assertEqual(format_vnd(D("0")), "0đ")
        self.assertEqual(format_vnd(D("-4.3151"), True), "-4,32đ")
        self.assertEqual(format_vnd(D("10000"), True), "+10.000đ")
        self.assertEqual(format_vnd(D("1234567.5")), "1.234.567,5đ")


if __name__ == "__main__":
    unittest.main()
