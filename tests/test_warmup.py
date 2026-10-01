"""Khởi động ứng dụng: phần khởi tạo LLM phải được dựng sẵn ở nền, không để lượt trả lời đầu tiên của khách gánh
(đã đo ~3 giây: import langchain_deepseek/openai + SSL context). Không cần DB, không gọi mạng."""
import unittest
from unittest import mock

from core import llm_client
from workers import process_documents


class LlmWarmUp(unittest.TestCase):
    def setUp(self):
        llm_client.get_llm.cache_clear()
        self.addCleanup(llm_client.get_llm.cache_clear)

    def test_warm_up_builds_the_client_without_a_network_call(self):
        with mock.patch.object(llm_client, "ChatDeepSeek") as chat, mock.patch.object(llm_client, "_http_client") as http:
            llm_client.warm_up()
        chat.assert_called_once()
        http.assert_called()
        chat.return_value.invoke.assert_not_called()  # chỉ dựng client, không gửi request (không tốn token)

    def test_first_real_call_reuses_the_warmed_client_for_same_params(self):
        with mock.patch.object(llm_client, "ChatDeepSeek") as chat, mock.patch.object(llm_client, "_http_client"):
            llm_client.warm_up()
            llm_client.get_llm()  # cùng bộ tham số mặc định: dùng lại, không dựng lại
        chat.assert_called_once()

    def test_other_param_sets_only_build_a_new_wrapper(self):
        with mock.patch.object(llm_client, "ChatDeepSeek") as chat, mock.patch.object(llm_client, "_http_client"):
            llm_client.warm_up()
            llm_client.get_llm(0.3, 700)
        self.assertEqual(chat.call_count, 2)


class EmbeddedStartup(unittest.TestCase):
    def test_start_embedded_warms_both_embedding_model_and_llm(self):
        app = mock.Mock()
        app.config = {"EMBEDDED_WORKER": False}
        with mock.patch.object(process_documents.socketio, "start_background_task") as start:
            process_documents.start_embedded(app)
        scheduled = [call.args[0] for call in start.call_args_list]
        self.assertIn(process_documents.rag_engine.warm_up, scheduled)
        self.assertIn(process_documents._warm_up_llm, scheduled)
        self.assertNotIn(process_documents.run_forever, scheduled)  # EMBEDDED_WORKER=false: không chạy worker huấn luyện

    def test_warm_up_llm_task_delegates_to_llm_client(self):
        with mock.patch.object(llm_client, "warm_up") as warm:
            process_documents._warm_up_llm()
        warm.assert_called_once_with()

    def test_embedded_worker_still_started_when_enabled(self):
        app = mock.Mock()
        app.config = {"EMBEDDED_WORKER": True}
        with mock.patch.object(process_documents.socketio, "start_background_task") as start:
            process_documents.start_embedded(app)
        self.assertIn(process_documents.run_forever, [call.args[0] for call in start.call_args_list])


if __name__ == "__main__":
    unittest.main()
