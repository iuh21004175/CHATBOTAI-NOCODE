# DeepSeek Flash – Pricing, Context Caching và tính chi phí

> Cập nhật: **21/09/2026**
> Model: **DeepSeek-V4.1-Flash** (`deepseek-flash`)
> Nhà cung cấp: **DeepSeek**
> Tỷ giá tham chiếu sử dụng trong tài liệu: **1 USD ≈ 26.200 VND**

---

## 1. Tổng quan

TECHNONX CHATBOTAI sử dụng LLM để:

* Phân tích câu hỏi khách hàng.
* Xác định Intent.
* Trích xuất Slot.
* Truy vấn RAG.
* Quyết định `ANSWER`, `CLARIFY` hoặc `RETRIEVE_MORE`.
* Sinh câu trả lời.
* Cập nhật Conversation Summary.
* Cập nhật Structured Memory.

Để kiểm soát chi phí, hệ thống cần quản lý:

1. Input Tokens.
2. Input Cache Hit Tokens.
3. Input Cache Miss Tokens.
4. Output Tokens.
5. Context Window.
6. RAG Token Budget.
7. Conversation Memory.
8. Prompt Cache.

---

# 2. Thông tin DeepSeek Flash

| Thông số         |             Giá trị |
| ---------------- | ------------------: |
| Model API        |    `deepseek-flash` |
| Model thực tế    | DeepSeek-V4.1-Flash |
| Context Length   |           1M tokens |
| Max Output       |         384K tokens |
| Input Cache Hit  |                  Có |
| Input Cache Miss |                  Có |
| Output           |                  Có |
| Context Caching  |             Tự động |

DeepSeek Flash có context window rất lớn, tuy nhiên **không nên gửi toàn bộ lịch sử hội thoại vào mỗi request**.

Context lớn không đồng nghĩa với việc nên sử dụng context lớn.

Đối với TECHNONX CHATBOTAI, cần sử dụng:

```text
Recent Messages
+
Conversation Summary
+
Structured Memory
+
Intent
+
Slots
+
RAG Context
+
Current User Message
```

thay vì gửi toàn bộ lịch sử.

---

# 3. Bảng giá DeepSeek Flash

## 3.1. Giá USD

DeepSeek tính phí theo **1 triệu tokens (1M tokens)**.

| Loại Token         |    Off-Peak |        Peak |
| ------------------ | ----------: | ----------: |
| Input – Cache Hit  | $0.003 / 1M | $0.006 / 1M |
| Input – Cache Miss |  $0.15 / 1M |  $0.30 / 1M |
| Output             |  $0.60 / 1M |  $1.20 / 1M |

### Peak Time

Theo giờ UTC:

```text
01:00 – 04:00 UTC
06:00 – 10:00 UTC
```

Quy đổi sang giờ Việt Nam (UTC+7):

```text
08:00 – 11:00
13:00 – 17:00
```

từ thứ Hai đến thứ Sáu.

Ngoài các khoảng thời gian trên là **Off-Peak**.

---

# 4. Quy đổi sang tiền Việt Nam

Tài liệu sử dụng tỷ giá:

```text
1 USD = 26.200 VND
```

## 4.1. Input Cache Hit

### Off-Peak

```text
$0.003 / 1M
× 26.200
= 78,6 VND / 1M tokens
```

Tương đương:

```text
0,0786 VND / 1K tokens
```

### Peak

```text
$0.006 / 1M
× 26.200
= 157,2 VND / 1M tokens
```

Tương đương:

```text
0,1572 VND / 1K tokens
```

---

## 4.2. Input Cache Miss

### Off-Peak

```text
$0.15 / 1M
× 26.200
= 3.930 VND / 1M tokens
```

Tương đương:

```text
3,93 VND / 1K tokens
```

### Peak

```text
$0.30 / 1M
× 26.200
= 7.860 VND / 1M tokens
```

Tương đương:

```text
7,86 VND / 1K tokens
```

---

## 4.3. Output

### Off-Peak

```text
$0.60 / 1M
× 26.200
= 15.720 VND / 1M tokens
```

Tương đương:

```text
15,72 VND / 1K tokens
```

### Peak

```text
$1.20 / 1M
× 26.200
= 31.440 VND / 1M tokens
```

Tương đương:

```text
31,44 VND / 1K tokens
```

---

# 5. Bảng giá DeepSeek Flash quy đổi VND

| Loại             | Off-Peak / 1M |  Peak / 1M | Off-Peak / 1K |  Peak / 1K |
| ---------------- | ------------: | ---------: | ------------: | ---------: |
| Input Cache Hit  |      78,6 VND |  157,2 VND |    0,0786 VND | 0,1572 VND |
| Input Cache Miss |     3.930 VND |  7.860 VND |      3,93 VND |   7,86 VND |
| Output           |    15.720 VND | 31.440 VND |     15,72 VND |  31,44 VND |

> **Lưu ý:** Đây là quy đổi theo tỷ giá tham chiếu 26.200 VND/USD. Số tiền thực tế khi thanh toán có thể thay đổi theo tỷ giá và phương thức thanh toán.

---

# 6. Input Token gồm những gì?

Input token là toàn bộ nội dung gửi vào model.

Ví dụ:

```text
System Prompt
+
Business Instruction
+
Assistant Configuration
+
Tool Definitions
+
Structured Memory
+
Intent
+
Slots
+
Conversation Summary
+
Recent Messages
+
RAG Context
+
Current User Message
```

Ví dụ:

```text
System Prompt:        500 tokens
Business Instruction: 300 tokens
Memory:               100 tokens
Summary:              300 tokens
Recent Messages:      800 tokens
RAG:                 1.500 tokens
User Message:         100 tokens
---------------------------------
Total:              3.600 tokens
```

---

# 7. Output Token là gì?

Output token là số token model sinh ra.

Ví dụ:

```text
Input:
3.000 tokens

Output:
300 tokens
```

Chi phí được tính riêng:

```text
Input cost
+
Output cost
```

Không nên lấy:

```text
total_tokens
```

rồi nhân trực tiếp với một mức giá duy nhất.

---

# 8. Cache Hit và Cache Miss

DeepSeek có cơ chế Context Caching.

## Cache Hit

Một phần input có prefix giống request trước đó và được cache lại.

Ví dụ:

```text
Request 1:

SYSTEM PROMPT
BUSINESS RULES
CONFIGURATION
...
USER MESSAGE
```

Request 2:

```text
SYSTEM PROMPT
BUSINESS RULES
CONFIGURATION
...
USER MESSAGE MỚI
```

Phần prefix giống nhau có khả năng được cache hit.

---

## Cache Miss

Phần input chưa có trong cache hoặc không khớp với cache sẽ được tính theo giá Cache Miss.

Ví dụ:

```text
Static Prefix
       ↓
Cache Hit

Dynamic Context
       ↓
Cache Miss

Current User Message
       ↓
Cache Miss
```

---

# 9. DeepSeek Context Caching không phải Memory

Hai khái niệm này khác nhau.

## Context Caching

Mục đích:

```text
Giảm chi phí xử lý input
```

## Conversation Memory

Mục đích:

```text
Giúp AI hiểu trạng thái hội thoại
```

Ví dụ:

```text
Khách:
Tôi muốn mua laptop.

AI:
Anh/chị cần laptop cho mục đích gì?

Khách:
Lập trình.

```

Memory có thể lưu:

```json
{
  "intent": "buy_laptop",
  "purpose": "programming"
}
```

Cache không có nhiệm vụ lưu thông tin khách hàng theo nghĩa nghiệp vụ.

---

# 10. Kiến trúc Prompt tối ưu Cache

Nên sắp xếp prompt theo thứ tự:

```text
STATIC PREFIX
│
├── System Prompt
├── Business Instructions
├── Assistant Configuration
├── Response Rules
└── Tool Definitions
│
▼
DYNAMIC CONTEXT
│
├── Structured Memory
├── Current Intent
├── Slots
├── Conversation Summary
├── Recent Messages
└── RAG Context
│
▼
CURRENT USER MESSAGE
```

Mục tiêu:

```text
Static Prefix
      ↓
Ổn định
      ↓
Tăng khả năng Cache Hit
      ↓
Giảm Input Cost
```

---

# 11. Không nên đưa dữ liệu động lên đầu Prompt

Không nên:

```text
CURRENT USER MESSAGE
+
RANDOM MEMORY
+
RANDOM RAG
+
SYSTEM PROMPT
+
BUSINESS RULE
```

vì phần đầu prompt thay đổi thường xuyên.

Nên:

```text
SYSTEM PROMPT
BUSINESS RULE
CONFIGURATION
TOOLS

----------------

MEMORY
INTENT
SLOTS
SUMMARY
RECENT MESSAGES
RAG

----------------

CURRENT USER MESSAGE
```

---

# 12. Kiến trúc Context của TECHNONX CHATBOTAI

```text
                         USER MESSAGE
                              │
                              ▼
                    ┌───────────────────┐
                    │ Conversation State│
                    └─────────┬─────────┘
                              │
          ┌───────────────────┼────────────────────┐
          │                   │                    │
          ▼                   ▼                    ▼
       Intent               Slots              Memory
          │                   │                    │
          └───────────────────┼────────────────────┘
                              │
                              ▼
                    Conversation Summary
                              │
                              ▼
                    Recent Messages
                              │
                              ▼
                         RAG Search
                              │
                              ▼
                          Reranker
                              │
                              ▼
                    Candidate Filtering
                              │
                              ▼
                    Token Budget Manager
                              │
                              ▼
                     Answerability Engine
                              │
             ┌────────────────┼─────────────────┐
             │                │                 │
             ▼                ▼                 ▼
          ANSWER           CLARIFY         RETRIEVE_MORE
             │                │                 │
             └────────────────┼─────────────────┘
                              │
                              ▼
                            LLM
                              │
                              ▼
                     Response to User
                              │
                              ▼
                    Update Conversation State
```

---

# 13. Recent Messages

Không nên gửi toàn bộ lịch sử.

Ví dụ cấu hình:

```yaml
recent_message_limit: 10
recent_token_limit: 2000
```

Hệ thống lấy tối đa:

```text
10 messages
```

nhưng vẫn phải kiểm tra:

```text
Token Budget
```

Ví dụ:

```text
10 messages
↓
2.800 tokens
↓
Limit = 2.000
↓
Cắt bớt message cũ
```

---

# 14. Conversation Summary

Khi hội thoại dài:

```text
Message 1
Message 2
Message 3
...
Message 100
```

không nên gửi toàn bộ.

Thay vào đó:

```text
Old Messages
      ↓
Summarizer
      ↓
Conversation Summary
```

Ví dụ:

```json
{
  "summary": "Khách hàng đang tìm laptop phục vụ lập trình. 
  Ưu tiên hiệu năng CPU, RAM tối thiểu 16GB và ngân sách khoảng 25 triệu."
}
```

---

# 15. Structured Memory

Structured Memory dùng để lưu thông tin quan trọng.

Ví dụ:

```json
{
  "customer_preferences": {
    "ram": "16GB",
    "purpose": "programming",
    "budget": "25000000"
  },
  "constraints": {
    "brand": null
  }
}
```

Không nên lưu mọi câu nói của khách vào Memory.

Chỉ lưu:

```text
Facts
Preferences
Constraints
Entities
Long-term information
```

---

# 16. Intent Tracking

Ví dụ:

```json
{
  "intent": "product_search",
  "confidence": 0.91
}
```

Một số Intent:

```text
product_search
product_comparison
price_inquiry
technical_support
order_status
contact_sales
refund_request
general_question
```

---

# 17. Slot Filling

Ví dụ khách muốn mua laptop.

Các slot:

```text
purpose
budget
ram
cpu
gpu
brand
screen_size
```

Khách nói:

```text
Tôi cần laptop lập trình.
```

Hệ thống:

```json
{
  "purpose": "programming",
  "budget": null,
  "ram": null,
  "cpu": null,
  "gpu": null
}
```

Khi còn thiếu thông tin quan trọng:

```text
CLARIFY
```

Ví dụ:

```text
Bạn dự kiến ngân sách khoảng bao nhiêu?
```

---

# 18. RAG không nên trả toàn bộ kết quả cho LLM

Ví dụ:

```text
RAG Search
↓
50 documents
```

Không nên:

```text
50 documents
↓
LLM
```

Nên:

```text
50 documents
      ↓
Score Filtering
      ↓
Deduplication
      ↓
Reranking
      ↓
Top N
      ↓
Token Budget
      ↓
LLM
```

---

# 19. RAG Configuration

Cấu hình khởi điểm:

```yaml
rag:
  top_k: 8
  rerank_top_n: 5
  score_threshold: 0.70
  max_candidates: 5
  token_budget: 3000
```

Đây là **giá trị khởi điểm để benchmark**, không phải ngưỡng cố định cho mọi doanh nghiệp.

---

# 20. Khi nào RAG nên yêu cầu Clarification?

Không nên:

```text
RAG có nhiều kết quả
↓
CLARIFY ngay
```

Mà nên:

```text
RAG Search
    ↓
Rerank
    ↓
Deduplicate
    ↓
Score Filtering
    ↓
Token Budget
    ↓
Kiểm tra mức độ rõ ràng
```

Nếu vẫn còn:

```text
Nhiều ứng viên gần như tương đương
+
Không xác định được lựa chọn
+
Thiếu Slot quan trọng
```

thì:

```text
CLARIFY
```

---

# 21. Retrieval Signals

Hệ thống có thể sử dụng:

```text
intent_confidence
slot_completion
retrieval_confidence
top_score
second_score
score_gap
candidate_count
evidence_conflict
query_ambiguity
context_pressure
```

Ví dụ:

```json
{
  "intent_confidence": 0.72,
  "slot_completion": 0.61,
  "top_score": 0.91,
  "second_score": 0.90,
  "score_gap": 0.01,
  "candidate_count": 14
}
```

Trong trường hợp:

```text
top_score = 0.91
second_score = 0.90
```

hai kết quả gần như ngang nhau.

Hệ thống có thể xem đây là dấu hiệu câu hỏi chưa đủ cụ thể.

---

# 22. Context Pressure

Có thể tính:

```text
context_pressure =
current_context_tokens / max_context_tokens
```

Ví dụ:

```text
current_context = 8.000 tokens
max_context = 10.000 tokens

context_pressure = 0.8
```

Tức:

```text
80%
```

Có thể đặt:

```yaml
context_pressure:
  warning: 0.80
  hard_limit: 0.90
```

---

# 23. Answerability Engine

Answerability Engine quyết định:

```text
ANSWER
CLARIFY
RETRIEVE_MORE
```

Ví dụ:

```json
{
  "decision": "clarify",
  "intent_confidence": 0.72,
  "slot_completion": 0.61,
  "top_retrieval_score": 0.91,
  "candidate_count": 14,
  "context_pressure": 0.82,
  "reason": [
    "too_many_relevant_candidates",
    "missing_preference"
  ]
}
```

Điều này rất hữu ích để Admin hiểu:

```text
Tại sao chatbot lại hỏi lại khách?
```

---

# 24. Nguyên tắc Clarification

Không nên hỏi:

```text
Bạn có thể cung cấp thêm thông tin về nhu cầu,
ngân sách, thương hiệu, mục đích sử dụng,
tính năng và các yêu cầu khác không?
```

Nên hỏi **một câu hỏi quan trọng nhất**.

Ví dụ:

```text
Bạn dự kiến ngân sách khoảng bao nhiêu?
```

Sau khi khách trả lời:

```text
Update Slot
↓
RAG Search lại
↓
Decision lại
```

---

# 25. Giới hạn Clarification

Không nên hỏi vô hạn.

Cấu hình:

```yaml
clarification:
  max_turns: 2
  questions_per_turn: 1
```

Nếu đã hỏi:

```text
2 lần
```

thì hệ thống nên cố gắng đưa ra phương án phù hợp nhất dựa trên thông tin hiện có, thay vì tiếp tục hỏi liên tục.

---

# 26. Token Budget

Nên phân bổ token:

```text
Context Window
│
├── Static Prompt
├── Memory
├── Summary
├── Recent Messages
├── RAG
└── User Message
```

Ví dụ:

```yaml
token_budget:
  system: 1000
  memory: 500
  summary: 500
  recent_messages: 2000
  rag: 3000
  user_message: 500
```

Tổng:

```text
7.500 tokens
```

---

# 27. Output Token Budget

Output cũng cần giới hạn.

Ví dụ:

```yaml
generation:
  max_output_tokens: 500
```

Không nên mặc định để model sinh quá dài.

Đặc biệt:

```text
Output Token
```

có giá cao hơn rất nhiều so với:

```text
Input Cache Hit
```

Vì vậy việc kiểm soát độ dài câu trả lời có tác động trực tiếp đến chi phí.

---

# 28. Ví dụ tính chi phí

## Trường hợp 1

Request:

```text
Input = 810 tokens
Output = 50 tokens
```

Giả sử toàn bộ Input là Cache Miss.

### Off-Peak

Input:

```text
810 / 1.000.000 × 3.930
≈ 3,18 VND
```

Output:

```text
50 / 1.000.000 × 15.720
≈ 0,79 VND
```

Tổng:

```text
≈ 3,97 VND
```

### Peak

Input:

```text
810 / 1.000.000 × 7.860
≈ 6,37 VND
```

Output:

```text
50 / 1.000.000 × 31.440
≈ 1,57 VND
```

Tổng:

```text
≈ 7,94 VND
```

---

# 29. Ví dụ 2 – Cache Hit

Giả sử:

```text
Input Cache Hit  = 1.500 tokens
Input Cache Miss = 500 tokens
Output           = 300 tokens
```

## Off-Peak

Cache Hit:

```text
1.500 / 1.000.000 × 78,6
≈ 0,118 VND
```

Cache Miss:

```text
500 / 1.000.000 × 3.930
≈ 1,965 VND
```

Output:

```text
300 / 1.000.000 × 15.720
≈ 4,716 VND
```

Tổng:

```text
≈ 6,80 VND
```

## Peak

Cache Hit:

```text
≈ 0,236 VND
```

Cache Miss:

```text
≈ 3,93 VND
```

Output:

```text
≈ 9,432 VND
```

Tổng:

```text
≈ 13,60 VND
```

---

# 30. Ví dụ 3 – 5.000 Input + 500 Output

Giả sử:

```text
Input = 5.000 tokens
Output = 500 tokens
```

và toàn bộ Input là Cache Miss.

## Off-Peak

```text
Input:
5.000 × 3.93 / 1.000
= 19,65 VND
```

```text
Output:
500 × 15,72 / 1.000
= 7,86 VND
```

Tổng:

```text
27,51 VND
```

## Peak

```text
Input:
5.000 × 7,86 / 1.000
= 39,30 VND
```

```text
Output:
500 × 31,44 / 1.000
= 15,72 VND
```

Tổng:

```text
55,02 VND
```

---

# 31. Ví dụ 4 – Context có Cache Hit cao

Giả sử:

```text
Cache Hit  = 4.000 tokens
Cache Miss = 1.000 tokens
Output     = 500 tokens
```

## Off-Peak

Cache Hit:

```text
4.000 × 78,6 / 1.000.000
= 0,3144 VND
```

Cache Miss:

```text
1.000 × 3.930 / 1.000.000
= 3,93 VND
```

Output:

```text
500 × 15.720 / 1.000.000
= 7,86 VND
```

Tổng:

```text
≈ 12,10 VND
```

---

# 32. Công thức tổng quát

## Input Cache Hit

```text
Cost =
cache_hit_tokens / 1.000.000
× cache_hit_price
```

## Input Cache Miss

```text
Cost =
cache_miss_tokens / 1.000.000
× cache_miss_price
```

## Output

```text
Cost =
output_tokens / 1.000.000
× output_price
```

## Tổng

```text
Total Cost =
Cache Hit Cost
+
Cache Miss Cost
+
Output Cost
```

---

# 33. Công thức trong Python

```python
def calculate_cost(
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    output_tokens: int,
    cache_hit_price: float,
    cache_miss_price: float,
    output_price: float,
    usd_to_vnd: float = 26200,
):
    """
    Prices are USD per 1M tokens.
    """

    hit_cost_usd = (
        cache_hit_tokens / 1_000_000
    ) * cache_hit_price

    miss_cost_usd = (
        cache_miss_tokens / 1_000_000
    ) * cache_miss_price

    output_cost_usd = (
        output_tokens / 1_000_000
    ) * output_price

    total_usd = (
        hit_cost_usd
        + miss_cost_usd
        + output_cost_usd
    )

    total_vnd = total_usd * usd_to_vnd

    return {
        "cache_hit_cost_usd": hit_cost_usd,
        "cache_miss_cost_usd": miss_cost_usd,
        "output_cost_usd": output_cost_usd,
        "total_usd": total_usd,
        "total_vnd": total_vnd,
    }
```

---

# 34. Theo dõi Usage trong TECHNONX

Mỗi request nên lưu usage.

Ví dụ:

```json
{
  "model": "deepseek-flash",
  "prompt_tokens": 5000,
  "prompt_cache_hit_tokens": 4000,
  "prompt_cache_miss_tokens": 1000,
  "completion_tokens": 500,
  "total_tokens": 5500
}
```

---

# 35. Database Usage Tracking

Có thể tạo bảng:

```text
llm_usage
```

Các trường:

```text
id
tenant_id
assistant_id
conversation_id
message_id

provider
model

prompt_tokens
prompt_cache_hit_tokens
prompt_cache_miss_tokens
completion_tokens
total_tokens

input_cost
output_cost
total_cost

currency

created_at
```

---

# 36. Theo dõi Cost theo Tenant

TECHNONX là SaaS nên cần tính chi phí theo:

```text
Tenant
    ↓
Assistant
    ↓
Conversation
    ↓
Message
    ↓
LLM Request
```

Ví dụ:

```text
Tenant A
├── Assistant 1
│   ├── Conversation 1
│   ├── Conversation 2
│   └── Conversation 3
│
└── Assistant 2
    ├── Conversation 4
    └── Conversation 5
```

Có thể tính:

```text
Daily Cost
Monthly Cost
Cost / Assistant
Cost / Conversation
Cost / Customer
Cost / Message
```

---

# 37. Không nên gọi LLM cho mọi quyết định

Các quyết định sau nên xử lý bằng code/rule:

```text
Token Counting
Message Limit
Context Budget
RAG Candidate Limit
Score Threshold
Duplicate Removal
Summary Trigger
Cache Metrics
Usage Tracking
Tenant Quota
Cost Calculation
```

LLM nên dùng cho:

```text
Intent Understanding
Slot Extraction
Semantic Ambiguity
Memory Extraction
Response Generation
Clarification Question
```

Mục tiêu:

```text
Không biến mỗi bước xử lý thành một LLM request.
```

---

# 38. Pipeline đề xuất

```text
User Message
      │
      ▼
Normalize Input
      │
      ▼
Conversation State
      │
      ├── Intent
      ├── Slots
      ├── Memory
      └── Summary
      │
      ▼
Query Analysis
      │
      ▼
RAG Retrieval
      │
      ▼
Reranking
      │
      ▼
Deduplication
      │
      ▼
Candidate Filtering
      │
      ▼
Token Budget
      │
      ▼
Answerability Engine
      │
      ├───────────────┐
      │               │
      ▼               ▼
   CLARIFY          ANSWER
      │               │
      │               ▼
      │              LLM
      │               │
      └───────────────┘
              │
              ▼
      Update State
              │
              ▼
       Save Usage/Cost
```

---

# 39. Các component nên có

## Context Layer

```text
ConversationState
IntentTracker
SlotManager
StructuredMemory
ConversationSummary
```

## Context Builder

```text
RecentMessageSelector
SummarySelector
MemorySelector
ContextBudgetManager
TokenCounter
```

## RAG Controller

```text
RetrievalScorer
Reranker
CandidateLimiter
RAGTokenBudget
DuplicateFilter
```

## Decision Engine

```text
AmbiguityDetector
AnswerabilityScorer
ContextPressureAnalyzer
ClarificationController
```

## Cost Control

```text
LLMUsageTracker
PromptCacheMetrics
TenantQuota
TokenBudget
CostEstimator
```

---

# 40. DeepSeek + LangChain

DeepSeek cung cấp API tương thích với OpenAI API.

Ví dụ:

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="deepseek-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
)
```

Context Caching do DeepSeek xử lý.

Không cần:

```python
cache=True
```

hoặc tự tạo:

```python
cache_key
```

cho Context Caching của DeepSeek.

Điều quan trọng là thiết kế prompt sao cho phần prefix ổn định.

---

# 41. Theo dõi Cache Hit

Sau khi gọi API, cần lấy usage từ response.

Mục tiêu lưu:

```text
prompt_tokens
prompt_cache_hit_tokens
prompt_cache_miss_tokens
completion_tokens
total_tokens
```

Sau đó tính:

```text
Cache Hit Rate
```

theo công thức:

```text
Cache Hit Rate =
prompt_cache_hit_tokens
/
prompt_tokens
× 100
```

Ví dụ:

```text
prompt_tokens = 10.000
cache_hit = 8.000
```

thì:

```text
Cache Hit Rate
= 8.000 / 10.000 × 100
= 80%
```

---

# 42. Dashboard cho Admin

TECHNONX có thể hiển thị:

```text
Total Tokens
Input Tokens
Output Tokens
Cache Hit Tokens
Cache Miss Tokens
Cache Hit Rate
Estimated Cost
```

Ví dụ:

```text
┌───────────────────────────────┐
│ AI Usage – September         │
├───────────────────────────────┤
│ Total Requests      125,420   │
│ Input Tokens        82.4M     │
│ Output Tokens       12.7M     │
│ Cache Hit           61.2M     │
│ Cache Miss          21.2M     │
│ Cache Hit Rate      74.3%     │
│ Estimated Cost      ... VND   │
└───────────────────────────────┘
```

---

# 43. Chiến lược tối ưu chi phí

## 1. Giữ Static Prefix ổn định

```text
System
Business Rules
Assistant Configuration
Tools
```

đặt ở đầu prompt.

---

## 2. Giới hạn Recent Messages

Ví dụ:

```yaml
limit: 10
token_limit: 2000
```

---

## 3. Dùng Summary

Không gửi toàn bộ lịch sử.

---

## 4. Dùng Structured Memory

Lưu thông tin quan trọng dưới dạng cấu trúc.

---

## 5. Giới hạn RAG

Ví dụ:

```yaml
top_k: 8
rerank_top_n: 5
token_budget: 3000
```

---

## 6. Giới hạn Output

Ví dụ:

```yaml
max_output_tokens: 500
```

---

## 7. Clarification trước khi trả lời quá rộng

Nếu câu hỏi quá mơ hồ:

```text
CLARIFY
```

thay vì đưa vào LLM một lượng RAG rất lớn.

---

# 44. Điểm quan trọng về chi phí

Không nên chỉ quan tâm:

```text
Input Token
```

Cần quan tâm cả:

```text
Input Cache Hit
Input Cache Miss
Output Token
```

Trong nhiều trường hợp:

```text
Output Token
```

có chi phí cao hơn rất nhiều so với:

```text
Cache Hit Token
```

Do đó:

```text
Answerability Engine
+
Output Limit
+
RAG Token Budget
+
Context Compression
```

không chỉ giúp AI trả lời chính xác hơn mà còn giúp kiểm soát chi phí.

---

# 45. Chiến lược tối ưu tổng thể

```text
                 USER
                  │
                  ▼
          Intent + Slot
                  │
                  ▼
         Retrieve Relevant Data
                  │
                  ▼
             Reranking
                  │
                  ▼
        Remove Duplicates
                  │
                  ▼
        Apply Token Budget
                  │
                  ▼
        Check Answerability
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
     CLARIFY              ANSWER
        │                   │
        ▼                   ▼
 Narrow Requirement         LLM
        │                   │
        └─────────┬─────────┘
                  ▼
          Update Memory
                  │
                  ▼
          Save Usage Data
                  │
                  ▼
          Calculate Cost
```

---

# 46. Cấu hình đề xuất ban đầu cho TECHNONX

```yaml
conversation:
  recent_message_limit: 10
  recent_token_limit: 2000

summary:
  trigger_tokens: 4000
  max_tokens: 500

memory:
  confidence_threshold: 0.70

intent:
  confidence_threshold: 0.70

rag:
  top_k: 8
  rerank_top_n: 5
  score_threshold: 0.70
  max_candidates: 5
  token_budget: 3000

clarification:
  max_turns: 2
  questions_per_turn: 1

context:
  pressure_warning: 0.80
  pressure_hard_limit: 0.90

generation:
  max_output_tokens: 500

cost:
  currency: VND
  usd_to_vnd: 26200
```

> Các ngưỡng trên là **cấu hình khởi điểm để benchmark và điều chỉnh theo dữ liệu thực tế**, không phải thông số bắt buộc của DeepSeek.

---

# 47. Decision Trace

Mỗi quyết định nên có trace.

Ví dụ:

```json
{
  "decision": "clarify",

  "intent_confidence": 0.72,

  "slot_completion": 0.61,

  "retrieval": {
    "top_score": 0.91,
    "second_score": 0.90,
    "score_gap": 0.01,
    "candidate_count": 14
  },

  "context_pressure": 0.82,

  "reason": [
    "too_many_relevant_candidates",
    "missing_preference"
  ]
}
```

Điều này giúp Admin/debug team biết:

```text
Chatbot hỏi lại vì lý do gì?
```

thay vì chỉ thấy:

```text
AI tự nhiên hỏi lại khách.
```

---

# 48. Mục tiêu cuối cùng

Kiến trúc TECHNONX CHATBOTAI nên hướng đến:

```text
Không gửi quá nhiều context
        +
Không hỏi lại khách hàng không cần thiết
        +
Không truy xuất quá nhiều tài liệu
        +
Không sinh câu trả lời quá dài
        +
Tận dụng Context Cache
        +
Theo dõi chính xác Token Usage
        +
Theo dõi Cost theo Tenant
```

Kết quả:

```text
Context nhỏ hơn
        ↓
RAG chính xác hơn
        ↓
LLM xử lý ít hơn
        ↓
Output ngắn hơn
        ↓
Chi phí thấp hơn
        ↓
Khả năng mở rộng SaaS tốt hơn
```

---

# 49. Nguồn chính thức

* DeepSeek Pricing: [DeepSeek API Pricing](https://api-docs.deepseek.com/quick_start/pricing/?utm_source=chatgpt.com)
* DeepSeek Context Caching: [DeepSeek Context Caching](https://api-docs.deepseek.com/guides/kv_cache/?utm_source=chatgpt.com)
* DeepSeek Chat Completion API: [DeepSeek Chat Completion API](https://api-docs.deepseek.com/api/create-chat-completion/?utm_source=chatgpt.com)
* DeepSeek-V4.1-Flash release: [DeepSeek V4.1-Flash Release](https://api-docs.deepseek.com/news/news260910/?utm_source=chatgpt.com)

---

# 50. Tóm tắt nhanh

### Giá DeepSeek Flash

| Token      |            Off-Peak |                Peak |
| ---------- | ------------------: | ------------------: |
| Cache Hit  |   **78,6 VND / 1M** |  **157,2 VND / 1M** |
| Cache Miss |  **3.930 VND / 1M** |  **7.860 VND / 1M** |
| Output     | **15.720 VND / 1M** | **31.440 VND / 1M** |

### Công thức

```text
Total Cost
=
Cache Hit Cost
+
Cache Miss Cost
+
Output Cost
```

### Kiến trúc tối ưu

```text
Static Prompt
    ↓
Memory
    ↓
Intent + Slots
    ↓
Summary
    ↓
Recent Messages
    ↓
RAG
    ↓
Reranking
    ↓
Token Budget
    ↓
Answerability Engine
    ↓
ANSWER / CLARIFY / RETRIEVE_MORE
    ↓
LLM
    ↓
Usage + Cost Tracking
```

### Nguyên tắc cốt lõi

```text
Không phải context càng lớn thì AI càng tốt.

Context cần:
- đúng
- đủ
- có cấu trúc
- nằm trong budget
- ưu tiên thông tin liên quan
- tận dụng cache
```
