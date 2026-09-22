"""Context & Response Decision Engine — thay luồng RAG đơn giản (search top-5 -> nối chuỗi -> gọi LLM).

Bố cục (mỗi module 1 phase): settings (cấu hình + tier) · state (Phase 1) · builder (Phase 2) · rag_engine.retrieve (Phase 3,
ở core/rag_engine.py) · decision (Phase 4) · history_retrieval (Phase 5) · cost (Phase 6) · engine (bộ điều phối 1 lượt).
Việc nền (rolling summary, embed lịch sử) ở jobs.py, chạy bởi workers/context_jobs.py.
"""
