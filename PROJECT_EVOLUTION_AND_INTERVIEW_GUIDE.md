# AgentReview 项目演进记录与面试讲解指南

> 最后更新：2026-09-26
> 记录范围：从 `cc5c769 docs: add phase one implementation interview guide` 之后，到进入 Agent 化阶段前的设计基线。

## 1. 这份文档的用途

`PHASE1_IMPLEMENTATION_AND_INTERVIEW_GUIDE.md` 记录了 Phase 0～1 第一版工程实现，是一个历史快照。当时自动化测试已经完成，但真实 GitHub App、真实模型和故障恢复还没有完成验收。

本文件从那个时间点继续记录项目演进，目标有三个：

1. 记录每一批修改解决了什么问题，而不只是罗列文件。
2. 保存可以支撑简历和面试说法的测试证据。
3. 明确区分已实现、已实测和仍在规划中的能力，避免面试时过度描述。

今后每完成一批功能，都应同步更新本文件的以下部分：

- “演进时间线”：记录问题、方案、验证和结果。
- “当前能力快照”：更新系统现在真正具备的能力。
- “测试证据”：更新测试数量、真实联调结果和限制。
- “面试讲解”：把新增能力转化为可解释的工程决策。
- “尚未完成”：删除已完成项，补充新的边界。

本文只记录安全的工程信息，不记录 `.env` 内容、API Key、Webhook Secret、GitHub App 私钥或其他凭据。

---

## 2. 上一次文档更新时的基线

上一次指南对应提交：

```text
cc5c769 docs: add phase one implementation interview guide
```

当时已经完成：

- FastAPI Webhook 接入与签名校验；
- GitHub App 鉴权和 GitHub API Provider；
- PR Diff 获取与文件过滤；
- Mock/OpenAI Provider 抽象；
- 结构化 Review Report 持久化；
- GitHub Summary Review 发布；
- Celery Worker、重试策略和阶段级恢复；
- MySQL、Redis、API、Worker 的 Docker Compose 环境；
- 144 项自动化测试。

当时尚未完成：

- 真实 GitHub App 端到端联调；
- 真实模型 API 联调；
- Webhook Redelivery 实测；
- Timeout、429、5xx 和永久错误的可控故障验证；
- 固定评测集及质量指标；
- Token usage 采集。

因此，后面的工作重点不再是“把主链路写出来”，而是把主链路从代码层面的正确推进到真实环境可验证、故障可恢复、质量可评估。

---

## 3. 演进时间线

### 3.1 Docker 基础设施验收

#### 问题

自动化测试通过不代表容器环境中的 API、数据库、Redis 和 Worker 能协同工作，需要验证实际部署拓扑和数据库迁移状态。

#### 完成内容

- 启动 MySQL 8.4、Redis、FastAPI API 和 Celery Worker；
- 验证所有服务健康状态；
- 验证 API readiness 同时检查数据库和 Redis；
- 验证 Alembic 已位于 `20260907_0005 (head)`；
- 验证 Celery Worker 能通过 Redis 正常响应 `inspect ping`。

#### 实测结果

```text
api:    healthy
mysql:  healthy
redis:  healthy
worker: online, pong
database migration: 20260907_0005 (head)
```

#### 面试价值

这一步证明项目不是只能在 pytest 中运行的代码集合，而是具备完整本地运行拓扑的后端服务。

---

### 3.2 真实 GitHub App 端到端联调

#### 问题

Mock GitHub Provider 无法证明签名、Installation Token、真实 PR Diff 和 Review 发布能够共同工作。

#### 完成内容

- 创建专用 GitHub App 和测试仓库；
- 使用最小权限：`Contents: Read-only`、`Pull requests: Read & write`；
- Webhook 只订阅 `Pull request`；
- 使用 Smee 将 GitHub Webhook 转发到本地 API；
- 配置 Webhook Secret 和 GitHub App private key；
- 在真实测试仓库创建 PR；
- 验证 PR 新增 commit 后，`synchronize` 事件能够触发新一轮评审。

#### 实测链路

```text
GitHub pull_request event
  → Smee
  → FastAPI Webhook
  → MySQL Review Task
  → Redis / Celery Worker
  → GitHub Installation Token
  → PR metadata and file diff
  → AI Review Pipeline
  → GitHub PR Summary Review
```

#### 实测结果

- Webhook 正常返回 `202 Accepted`；
- Worker 成功获取 Installation Token；
- Worker 成功读取真实 PR 和文件 Diff；
- Review 成功发布到对应 GitHub PR；
- 同一 PR 新增 commit 后第二次评审也成功完成。

#### 面试价值

可以如实说明“完成了真实 GitHub App 端到端联调”，而不再只是“写了 GitHub API 客户端”。

---

### 3.3 DeepSeek Provider 与真实模型联调

对应提交：

```text
d9614a0 feat(llm): add DeepSeek responses and usage metadata
```

#### 问题

第一版只有 Mock 和 OpenAI Provider。项目希望使用成本更低的 DeepSeek 做真实联调，同时保持未来接入 OpenAI、Qwen 等模型的能力。

#### 设计

配置改为 Provider-neutral：

```text
LLM_PROVIDER
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
```

将 OpenAI Provider 中通用的 Responses API 能力抽取为共享实现：

```text
ResponsesAPIProvider
  ├── OpenAIResponsesProvider
  └── DeepSeekResponsesProvider
```

共享部分负责：

- HTTP 请求；
- Bearer Token；
- JSON Schema；
- Pydantic 输出解析；
- Timeout/Transport 错误转换；
- 401、403、429、408、409、5xx 分类；
- `Retry-After` 解析；
- Secret 脱敏。

Provider 子类只保留供应商差异：

- OpenAI：严格 JSON Schema，显式 `store=false`；
- DeepSeek：使用兼容的 Responses API，但不发送当前不支持的 OpenAI 专属选项；
- 两者都可使用自定义 Base URL。

#### 真实联调案例

测试 PR 中故意加入：

```python
def calculate_average(values):
    return sum(values) / len(values)
```

当 `values=[]` 时会发生除零错误。

#### 实测结果

- DeepSeek Responses 请求返回 HTTP 200；
- 模型识别出空列表除零问题；
- 输出 `MEDIUM`、`correctness` finding；
- 给出修复建议；
- Pipeline `outcome=completed`；
- 结果成功发布到 GitHub PR。

#### 面试价值

重点不是“调用了 DeepSeek”，而是：

> 我把模型厂商差异限制在 Provider Adapter 内，业务服务只依赖统一协议；通过自定义 Base URL、模型名和通用凭据配置，在不修改 Pipeline 的情况下完成了 DeepSeek 真实联调。

---

### 3.4 Webhook Redelivery 与业务幂等实测

#### 问题

GitHub Webhook 采用至少一次投递，同一个 Delivery 可能被重新发送。如果系统只依赖“查询后插入”，并发或重投递仍可能造成重复任务和重复付费调用。

#### 实测方法

对同一个 GitHub `pull_request.synchronize` Delivery 执行 Redeliver，并同时观察：

- API 响应；
- Celery 任务日志；
- DeepSeek 调用；
- GitHub Review 数量；
- MySQL Delivery、Task、Report、Publication 记录和唯一索引。

#### 实测结果

- API 再次正常返回 `202`；
- 没有创建新的 `reviews.run` 任务；
- 没有再次调用 DeepSeek；
- GitHub PR 没有新增重复 Review；
- 同一个 Delivery 只有一条记录；
- 同一个 `repository + PR number + head SHA` 只有一条 Review Task；
- 对应 Report 和 Publication 都只有一条；
- MySQL 唯一索引真实存在；
- 全库重复组合数为 0。

#### 准确边界

系统保证的是业务结果幂等，不声称 LLM 调用 Exactly Once。如果请求已经到达模型服务，但客户端在接收响应前超时，理论上仍可能产生重复调用和费用。

#### 面试表达

> 我采用应用层快速复用加数据库唯一约束兜底，分别按 GitHub Delivery 和 PR Head SHA 做两级幂等。实测 Webhook Redelivery 时，API 仍返回 202，但不会重复投递 Worker、调用模型或发布 Review。

---

### 3.5 可控故障注入与重试恢复验收

对应提交：

```text
53e262e test(reliability): add retry and recovery fault injection
```

#### 问题

只写重试代码不能证明重试策略正确。直接用真实 DeepSeek 制造 429、Timeout 或 5xx 又不可控、可能产生费用。

#### 方案

增加测试专用 Responses API Fault Stub 和 Compose Override：

```text
compose.fault.yml
scripts/llm_fault_stub.py
scripts/inspect_retry_state.py
```

Fault Stub 支持：

- 第一次 Timeout，随后恢复；
- HTTP 429 和可配置 `Retry-After`；
- HTTP 500/503；
- HTTP 401/403；
- HTTP 200 但结构化输出非法；
- 可配置失败次数和只读请求计数。

正式 Provider 逻辑没有为测试增加分支。Worker 只在显式加载 `compose.fault.yml` 时把 Base URL 指向本地 Stub。

#### 已完成实测

| 场景 | 分类 | 尝试次数 | 结果 |
|---|---|---:|---|
| Timeout 一次 | `llm_transient_error` | 2 | 1 秒后恢复 |
| HTTP 429 一次 | `llm_rate_limit_error` | 2 | `Retry-After=2` 覆盖默认退避 |
| HTTP 503 一次 | `llm_transient_error` | 2 | 1 秒后恢复 |
| HTTP 500 两次 | `llm_transient_error` | 3 | 验证 1 秒、2 秒指数退避 |
| HTTP 401 | `llm_authentication_error` | 1 | 不重试，不生成 Report/Publication |
| Invalid structured output | `llm_output_error` | 1 | 不重试，不发布 |

所有恢复成功的场景最终都满足：

- 仅一条 Report；
- 仅一条 Publication；
- 仅发布一份 GitHub Review。

#### Report 持久化后的阶段级恢复

另外通过单元测试注入 GitHub Publication 临时失败：

```text
第一次执行：调用 LLM → Report 已提交 → Publication 失败
第二次执行：读取已有 Report → 跳过 Diff 和 LLM → 重试 Publication
```

验证结果：

- Task `attempt_count` 从 1 增加到 2；
- LLM 调用次数保持 1；
- Diff 获取次数保持 1；
- Report 始终只有一条；
- 同一条 Publication 从 `FAILED` 恢复为 `PUBLISHED`。

#### 面试价值

这是目前最有区分度的后端亮点之一：不仅实现了重试，还通过真实 Worker、HTTP Stub、日志和数据库状态共同验证了错误分类、退避和断点恢复。

---

### 3.6 最小评测种子集与离线 Evaluator

对应独立提交：

```text
3bec0a9 feat(eval): add offline review quality seed framework
```

#### 问题

一次真实 PR 成功只能证明链路可用，不能衡量 Prompt、模型或未来多 Agent 改造对质量的影响。必须先建立固定回归集。

#### 评测集

建立 10 个 seed cases：

| 类型 | 案例 |
|---|---|
| correctness | 空列表除零 |
| correctness | off-by-one 跳过最后元素 |
| security | SQL 注入 |
| security | 目录穿越 |
| performance | N+1 查询 |
| performance | 循环内线性查找导致 O(n²) |
| maintainability | 三处复制相同业务规则 |
| reliability | 文件资源未可靠关闭 |
| negative | 正确处理空列表 |
| negative | 参数化查询并校验动态排序字段 |

每个正样本保存：

- expected finding；
- category；
- severity；
- file path；
- expected line range；
- 用于匹配的同义词概念组。

当前生产 Finding 没有 `start_line/end_line`，所以范围只作为未来 ground truth 保存，不计算范围命中率，也不从模型文本中猜行号。

#### Matcher

第一版匹配规则：

1. 必须是同一个文件；
2. 合并 actual finding 的标题、解释和建议；
3. 执行 Unicode 规范化、大小写折叠和标点清理；
4. 计算人工同义词概念组覆盖率；
5. 计算 expected description 与 actual 文本的 token overlap；
6. 按综合得分执行一对一贪心匹配。

匹配阶段不使用 expected category 和 severity。匹配完成后才分别计算类别和严重程度准确率，避免指标被答案泄漏污染。

#### Metrics

当前支持：

- Recall；
- False Positive 数量；
- 每案例平均 False Positive；
- Negative sample False Positive；
- Category Match；
- Severity Match；
- Schema Validity；
- Case Pass Rate；
- Latency mean/P50/P95；
- Token usage 和 cost 占位。

Case 只有在以下条件全部满足时才通过：

- 没有漏报；
- 没有误报；
- 匹配 Finding 的 category 正确；
- 匹配 Finding 的 severity 正确。

#### Mock Self-test 的定位

默认命令：

```powershell
python -m evals.runner
```

默认 Runner：

- 只使用 Mock；
- 不读取真实 Provider 配置；
- 不访问网络、数据库或 GitHub；
- 不产生模型费用；
- 输出 `is_model_baseline=false`。

Mock 结果为 100% 只说明 Evaluator、Matcher 和 Metrics 自身工作正常，绝不代表模型质量。

真实模型必须显式指定：

```powershell
python -m evals.runner --provider deepseek --live
```

截至当前，该 live 路径仍明确拒绝执行，因为 baseline 保存和安全保护还没有完成。

---

### 3.7 Provider Usage Metadata

对应提交：

```text
d9614a0 feat(llm): add DeepSeek responses and usage metadata
```

#### 问题

共享 Responses Provider 原来只返回 `LLMReviewOutput`。虽然 OpenAI 和 DeepSeek 的完成响应都包含顶层 `usage`，但现有 Pydantic Envelope 使用 `extra="ignore"`，这些数据会被丢弃，评测层无法记录真实 token。

#### 设计

Provider 协议扩展为：

```text
LLMReviewResult
  ├── output: LLMReviewOutput
  └── usage: LLMTokenUsage | None
        ├── input_tokens
        ├── output_tokens
        └── total_tokens
```

精确解析 Responses API 顶层字段：

```json
{
  "usage": {
    "input_tokens": 120,
    "output_tokens": 35,
    "total_tokens": 155
  }
}
```

处理规则：

- usage 整体缺失或为 `null`：返回 `None`，评审继续完成；
- usage 存在：三个 token 字段必须是非负整数；
- Mock Provider 始终返回 `usage=None`，不按字符数伪造 token；
- latency 继续由 Eval Runner 外层单调时钟测量；
- Provider 不计算 cost，不硬编码模型价格；
- token 和 cost 不进入现有数据库领域模型，本批没有 migration。

#### 保持生产 Pipeline 兼容

`AIReviewService` 增加：

```text
review_with_metadata(context) → AIReviewResult(report, usage)
```

原有入口继续保留：

```text
review(context) → ReviewReport
```

生产 Pipeline 仍调用 `review()`，因此任务编排、Report 持久化和 GitHub Publication 无需改变。Eval Runner 使用 `review_with_metadata()` 获取 usage。

#### 当前验证结果

```text
Eval tests: 17 passed
All tests: 182 passed
Ruff: passed
mypy app evals: passed (81 source files)
git diff --check: passed
```

第二批 usage metadata 改造已经随共享 Responses Provider 一并提交，但尚未执行 DeepSeek live eval。

---

### 3.8 受保护的 DeepSeek Live Eval 与 Baseline 写入

#### 问题

Eval Runner 已经能做 Mock self-test，但真实评测不能依赖 `.env` 中的 Provider 值自动触发，否则普通开发命令可能意外产生费用。同时，部分试跑、脏工作区和覆盖旧结果都会降低 baseline 的可复现性。

#### 安全设计

只有同时提供以下参数才会创建真实 DeepSeek Provider：

```powershell
python -m evals.runner --provider deepseek --live
```

保护规则包括：

- 默认命令仍然只运行 Mock；
- `--provider deepseek` 缺少 `--live` 时立即拒绝；
- CLI 安全校验通过后才加载应用 Settings；
- API Key 只能来自现有环境配置，不能作为 CLI 参数传入；
- Eval 只构造 `ReviewContext` 并调用 `AIReviewService`，不连接数据库和 GitHub；
- 案例按顺序执行，`--max-cases 1` 可用于低成本试跑；
- Provider 在运行结束后关闭 HTTP Client；
- 每个案例的异常转换为安全错误类型，不保存 Provider 错误消息。

#### Baseline 保护

只有完整的 DeepSeek live run 才能使用：

```powershell
python -m evals.runner --provider deepseek --live --save-baseline
```

保存规则：

- Mock 结果不能保存为模型 baseline；
- 使用 `--max-cases` 的部分运行不能保存；
- 默认要求 Git 工作区干净，并且在产生模型费用前检查；
- `--allow-dirty` 是显式例外，结果仍记录 `git_dirty=true`；
- 输出使用新建模式，拒绝覆盖已有 baseline；
- 默认文件名由 Model、Prompt Version 和 Dataset Version 组成；
- 可以用 `--baseline-output` 指定另一个尚不存在的路径。

Baseline 记录：

- Provider、Model、Prompt Version；
- Dataset Version、案例总数、实际执行数和 Dataset SHA-256；
- Git commit、dirty 状态和 UTC 执行时间；
- 每个案例的结构化 Finding、Matcher 结果、错误类型、latency 和 token usage；
- 聚合 Recall、False Positive、Category/Severity Match、Schema Validity 和 Case Pass Rate。

Baseline 不记录：

- Seed case 的源代码 Patch；
- 完整 Prompt；
- `.env` 或任何环境变量；
- API Key、Webhook Secret 或私钥；
- Provider 原始错误消息；
- 硬编码模型价格。

#### 自动化验证结果

```text
Eval tests: 27 passed
All tests: 192 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (82 source files)
git diff --check: passed
Mock CLI smoke test: passed; usage/cost remained null
```

这一阶段先用 Mock/Fake Provider 完成安全机制验证；真实 DeepSeek 结果单独记录在下一节，避免把自动化验证与真实模型证据混为一谈。

---

### 3.9 首次 DeepSeek 10-case Baseline 实测

#### 执行顺序

先执行单案例低成本试跑：

```powershell
python -m evals.runner --provider deepseek --live --max-cases 1
```

试跑成功识别空列表除零问题，结构化输出、usage 解析和安全开关均正常。随后在干净工作区执行完整评测：

```powershell
python -m evals.runner --provider deepseek --live --save-baseline
```

Baseline 原始结果保存为：

```text
evals/baselines/deepseek-flash__review-v1__seed-v1.json
```

#### 可复现元数据

```text
Provider: deepseek
Model: deepseek-flash
Prompt version: review-v1
Dataset version: seed-v1
Dataset SHA-256: dcb3cd4af05dd8670b6da06c81dd51d719cf948264dc51c4da7cc66edf956e2d
Git commit: 4274964e5b6fe07ed45b3cf359acb5b2b8cc9a33
Git dirty: false
Executed cases: 10/10
```

#### 真实模型结果

```text
Successful cases: 10/10
Transport errors: 0
Provider errors: 0
Schema errors: 0
Schema validity: 1.0

Expected findings: 8
Matched findings: 8
Recall: 1.0
Category match: 1.0
Severity match: 0.25
False positives: 10
False positives per case: 1.0
Negative-sample false positives: 4
Case pass rate: 0.1

Latency mean: 4187.20 ms
Latency P50: 3324.53 ms
Latency P95: 8722.74 ms
Input tokens: 6623
Output tokens: 7707
Total tokens: 14330
Cost: unavailable
```

#### 如何解释结果

- DeepSeek 找到了全部 8 个预期缺陷，且 Finding category 全部正确；
- 只有 2 个匹配 Finding 的 severity 与期望一致，模型整体倾向于把问题评得更严重；
- 额外 Finding 主要来自泛化的“缺少测试”建议，以及对 negative sample 契约的额外质疑；
- 两个 negative samples 共出现 4 个额外 Finding，说明当前 Prompt 和样例上下文还不能稳定抑制噪声；
- 这份 baseline 的价值不是证明模型已经足够好，而是为 Prompt v2、Matcher 和后续 Agent 改造提供固定对照组。

#### 准确边界

- 这是 10 个手工种子案例上的结果，不能外推为生产环境整体质量；
- 当前没有计算价格，因此不能声称完成成本优化；
- 当前 Case Pass 是严格指标：预期 Finding 全部命中、没有额外 Finding，且 category/severity 均正确；
- 目前没有行号输出，所以没有计算范围命中率；
- 后续如修改数据集标签或内容，必须升级 Dataset Version，不能直接覆盖这份结果。

---

### 3.10 离线 Baseline Comparator

#### 问题

保存 baseline 只能得到单次结果。如果 Prompt v2 产生新的 JSON 后仍靠人工逐项查看，不仅容易漏掉回归，也无法清楚回答“质量提升在哪里、代价是什么”。

#### 设计选择

新增纯离线比较命令：

```powershell
python -m evals.compare `
  --baseline evals/baselines/deepseek-flash__review-v1__seed-v1.json `
  --candidate evals/baselines/deepseek-flash__review-v2__seed-v1.json
```

比较前强制检查：

- 两侧都必须是完整模型 baseline；
- Evaluation Schema Version 必须相同；
- Dataset Version 和 Dataset SHA-256 必须相同；
- Case ID 集合必须完全一致；
- Git、执行时间等可复现元数据必须完整。

Comparator 在不改写旧 baseline 的前提下派生：

- Precision；
- F1；
- Negative Sample Case Pass Rate；
- Recall、误报、类别、严重程度、Case Pass、Schema、延迟和 Token 的差值及方向。

逐案例报告：

- 新增 Finding；
- 消失 Finding；
- Category 或 Severity 变化；
- Case Pass、Matched、False Positive 和 False Negative 前后值。

Finding 对齐使用“同文件 + 标题/解释的标准化文本相似度 + 一对一贪心匹配”。Category 和 Severity 不参与对齐，只在对齐后比较，避免标签污染。CamelCase 会先拆词，以识别 `ZeroDivisionError` 与 `zero division` 这类改写。

#### 安全与边界

- 命令只读取本地 JSON，不加载 Settings；
- 不访问网络、数据库、GitHub 或模型；
- 当前 `improved/regressed` 只表达指标偏好方向，不是发布门禁；
- 文本对齐是确定性启发式算法，不等价于完整语义判断，低相似度案例仍需人工复核；
- 延迟和 Token 会展示变化，但单次小样本波动不能直接证明性能回归。

#### 自动化验证结果

```text
Eval tests: 32 passed
All tests: 197 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (84 source files)
CLI self-comparison smoke test: passed
review-v1 derived precision: 0.4444444444
review-v1 derived F1: 0.6153846154
review-v1 negative-sample case pass rate: 0.0
```

本批没有修改 Prompt、Dataset 或历史 baseline，也没有调用真实 DeepSeek。

---

### 3.11 Prompt v2：证据约束、降噪与 Severity 校准

#### Baseline 暴露的问题

首次 `review-v1 + seed-v1` 真实 baseline 的 Recall 和 Category Match 都是 `1.0`，但产生 10 个额外 Finding、两个 negative samples 上产生 4 个误报，Severity Match 只有 `0.25`。逐案例检查发现两个主要原因：

- 原 Prompt 明确要求优先关注 `missing tests`，模型几乎为每段新增代码额外生成一条“缺少测试”；
- 原 Prompt 没有 Severity 定义，模型把除零、off-by-one、N+1 等有界问题普遍提高为 `high`，把 SQL 注入和目录穿越提高为 `critical`。

此外，模型还会因为 Diff 缺少仓库上下文而推测函数未定义、数据库 Driver 返回值不兼容，或把 PR 已明确声明的 `None` 返回契约当成缺陷。

#### Prompt v2 设计

Prompt Version 从 `review-v1` 升级为 `review-v2`，但不修改输出 Schema、Provider、Pipeline 或 Dataset。

新增 Evidence Policy：

- 只报告能够由当前 PR 数据直接支持的问题；
- 允许返回空 Finding；
- 尊重 PR 标题和描述中明确声明的行为契约；
- Diff 中看不到定义不代表函数、变量、Import 或类型不存在；
- 不推测未提供的 Library、Database Driver、Framework、Runtime 或输入类型行为；
- 低于 `0.80` confidence 的问题应省略。

新增 Finding Policy：

- 一个根因只生成一个 Finding；
- 不得仅因没有测试就创建独立 Finding；
- 对真实代码缺陷的回归测试建议应放在该 Finding 的 suggestion 中；
- Testing Finding 仅用于变更后的测试代码本身错误，或 PR 明确声称的测试/覆盖承诺与 Diff 冲突。

新增 Severity Rubric：

- `critical`：现实可利用、系统级失陷、重大敏感数据泄露等灾难性影响，且缺少有效前置限制；
- `high`：现实条件下的严重安全漏洞、数据损坏/丢失、鉴权失败或大范围生产故障；
- `medium`：可复现功能失败、有界的可靠性/资源问题或显著性能问题；
- `low`：不立即造成生产错误的局部客观维护性或测试问题；
- 严重程度必须依据已证明的实际影响，不能依据 Category 或假设的最坏情况。

#### 兼容性

- 生产 `AIReviewService` 和 Pipeline 仍使用相同接口；
- 结构化输出 Schema 没有变化；
- 历史 `review-v1` baseline 保持不变；
- 新 baseline 默认文件名将包含 `review-v2`，不会覆盖旧结果；
- `seed-v1` 内容和 SHA-256 不变，因此可以用 Comparator 做同数据集对比。

#### 自动化验证结果

```text
Prompt/AI/Eval targeted tests: 44 passed
All tests: 198 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (84 source files)
git diff --check: passed
```

截至本节更新时，只完成了 Prompt v2 代码和自动化验证，尚未调用真实 DeepSeek，也尚未生成 `review-v2` baseline。Prompt 是否真正改善误报和 Severity，必须由下一次相同 `seed-v1` 的真实运行证明。

---

### 3.12 Prompt v2 真实 Baseline 与回归对比

#### 验证方法

Prompt v2 提交后，先执行一个真实 DeepSeek 案例，确认空列表除零仍能命中、Severity 为 `medium`，并且不再额外生成“缺少测试”。随后临时收起个人文件，在干净工作区运行完整评测：

```powershell
python -m evals.runner --provider deepseek --live --save-baseline
```

最后使用纯离线 Comparator 比较两个相同数据集的结果：

```powershell
python -m evals.compare `
  --baseline evals/baselines/deepseek-flash__review-v1__seed-v1.json `
  --candidate evals/baselines/deepseek-flash__review-v2__seed-v1.json
```

这种顺序把三个问题分开：单案例先验证链路和基本行为，完整 baseline 提供真实模型输出，Comparator 再负责可重复的差异计算。比较阶段不访问网络，也不会再次产生模型费用。

#### 可复现元数据

```text
Provider: deepseek
Model: deepseek-flash
Prompt version: review-v2
Dataset version: seed-v1
Dataset SHA-256: dcb3cd4af05dd8670b6da06c81dd51d719cf948264dc51c4da7cc66edf956e2d
Git commit: f111d2d902230f7e438f1089e46f68c76a38933b
Git dirty: false
Executed cases: 10/10
Transport errors: 0
Provider errors: 0
Schema errors: 0
```

原始结果保存为：

```text
evals/baselines/deepseek-flash__review-v2__seed-v1.json
```

#### 核心质量指标

| 指标 | review-v1 | review-v2 | 变化 |
|---|---:|---:|---:|
| Recall | 1.0 | 1.0 | 持平 |
| Precision | 0.4444 | 1.0 | +0.5556 |
| F1 | 0.6154 | 1.0 | +0.3846 |
| False Positives | 10 | 0 | -10 |
| False Positives / Case | 1.0 | 0.0 | -1.0 |
| Negative Sample False Positives | 4 | 0 | -4 |
| Negative Sample Case Pass Rate | 0.0 | 1.0 | +1.0 |
| Category Match | 1.0 | 1.0 | 持平 |
| Severity Match | 0.25 | 1.0 | +0.75 |
| Case Pass Rate | 0.1 | 1.0 | +0.9 |
| Schema Validity | 1.0 | 1.0 | 持平 |

结果说明 Prompt v2 在这一轮 `seed-v1` 上保留了全部 8 个预期缺陷和正确 Category，同时去除了全部 10 个额外 Finding，并使 8 个匹配 Finding 的 Severity 全部符合预期。10 个案例均满足严格 Case Pass：预期问题全部命中、没有额外问题、Category 和 Severity 均正确。

#### 逐案例变化

| 案例 | review-v1 的主要问题 | review-v2 结果 |
|---|---|---|
| 空列表除零 | 额外报告缺少测试；Severity `high` | 只保留正确性问题；`medium` |
| Off-by-one | 推测 `process` 未定义；Severity `high` | 删除上下文推测；`medium` |
| SQL 注入 | 额外报告缺少测试；Severity `critical` | 只保留 SQL 注入；`high` |
| 目录穿越 | 额外报告缺少测试；Severity `critical` | 只保留任意文件读取风险；`high` |
| N+1 查询 | 额外报告缺少测试；Severity `high` | 只保留性能问题；`medium` |
| O(n²) 查找 | 原结果已经准确 | 继续保持 `performance/medium` |
| 重复业务规则 | Severity `medium` | 校准为 `maintainability/low` |
| 文件未关闭 | 额外报告缺少测试 | 只保留 `reliability/medium` |
| 正确处理空列表 | 质疑 `None` 契约、臆测 iterator，并报告缺少测试 | 返回空 Findings |
| 安全参数化查询 | 臆测 Cursor 链式调用不兼容 | 返回空 Findings |

一共删除了 6 条泛化 Testing Finding、1 条“函数未定义”推测、2 条违反明确契约/输入边界的推测，以及 1 条数据库 Driver 行为推测。Severity Rubric 同时校准了 6 个 Finding；另外两个正例原本已经匹配，因此最终 Severity Match 从 `0.25` 提升到 `1.0`。

#### Token 与延迟

| 指标 | review-v1 | review-v2 | 变化 |
|---|---:|---:|---:|
| Input Tokens | 6,623 | 10,103 | +3,480（约 +52.5%） |
| Output Tokens | 7,707 | 4,145 | -3,562（约 -46.2%） |
| Total Tokens | 14,330 | 14,248 | -82（约 -0.6%） |
| Mean Latency | 4,187.20 ms | 2,537.43 ms | -1,649.77 ms |
| P50 Latency | 3,324.53 ms | 2,418.62 ms | -905.90 ms |
| P95 Latency | 8,722.74 ms | 3,532.85 ms | -5,189.89 ms |

更详细的 Prompt 增加了输入 Token，但降噪后输出显著缩短，因此总 Token 基本持平。延迟在本轮下降，但每个版本只有一次 10-case 运行，且受供应商负载、网络和生成长度影响，不能把这一结果描述成已证明的性能优化。

#### 为什么这次优化有效

- Testing Policy 直接消除了“新增代码没有测试”这类模板化额外意见，但仍把回归测试建议保留在真实缺陷的 suggestion 中；
- Evidence Policy 阻止模型把 Diff 缺少上下文误认为符号缺失，也阻止对未说明的 Driver 和输入类型做假设；
- Contract Policy 让模型尊重 PR 已明确声明的 `None` 返回语义；
- Severity Rubric 用现实影响而不是假设的最坏情况定级，减少了 `high/critical` 膨胀；
- Dataset 和模型保持不变，因此差异主要来自 Prompt 版本，而不是换题或改标签。

#### 面试怎么讲

可以按以下结构说明：

> 第一版模型召回率是 100%，但 Precision 只有 44.4%，还存在严重级别膨胀。我没有直接删除模型输出，而是先固定 10 个案例、保存带 Git 和 Dataset SHA 的 baseline，再逐项分析误报来源。随后在 Prompt v2 中加入证据边界、契约尊重、Testing 去重和 Severity Rubric，并保持模型和数据集不变重新评测。单次 seed-v1 回归中 Recall 保持 100%，误报从 10 降到 0，Severity Match 从 25% 提升到 100%，总 Token 基本持平。这个结果只代表当前小型种子集，因此我保留了历史 baseline 和完整边界说明，没有把它夸大成生产准确率。

这个案例体现的不只是“会写 Prompt”，而是完整的评测闭环：先建立基线、定位错误模式、做单变量修改、在同数据集回归、保留原始证据并准确说明局限。

#### 准确边界与尚未完成

- 当前只有 10 个手工案例，样本规模小且缺陷模式较清晰；
- 每个 Prompt 版本只有一次完整运行，尚未测量模型随机性和重复运行方差；
- `1.0` 是本次固定种子集结果，不代表真实仓库或生产环境准确率；
- Dataset 参与了 Prompt 问题诊断；现已新增未参与 Prompt v2 调优的 holdout set，但尚未执行真实模型验证；
- 当前没有版本化价格快照，不能把 Token 换算为可复现成本；
- 延迟结果没有控制网络与供应商负载，不能作为性能承诺。

---

### 3.13 冻结的 Holdout 泛化验证集

#### 问题

Prompt v2 在 `seed-v1` 上得到完美单次结果，但 `seed-v1` 已经参与过误报分析和 Prompt 设计。继续只在同一批案例上评测，会把“对开发集优化”误当成“能够泛化到新问题”。在进入多 Agent 阶段前，需要一份 Prompt v2 从未见过的固定验证集。

#### 数据集角色

- `review_seed_v1.json`：开发集，用于定位 Prompt v1 的错误模式并设计 Prompt v2；
- `review_holdout_v1.json`：冻结验证集，在 Prompt v2 提交后创建，用于第一次泛化检查；
- 两者都遵循 `review-eval-v1` Schema，但 Dataset Version、Case ID 和 Patch 内容互不重复；
- Holdout 的模型结果不能反向用于悄悄修改标签或 Prompt。需要继续迭代时，应新增 Dataset Version 或新的验证集。

#### Holdout 组成

`holdout-v1` 共 10 个案例、8 个预期 Finding、2 个 negative samples，并包含两个多文件案例：

| 类别 | 案例 |
|---|---|
| Correctness | 跨文件折扣百分比/小数单位错误；精确余额被错误拒绝 |
| Security | Shell 命令注入；删除发票缺少 owner/admin 鉴权 |
| Performance | async 中使用阻塞 sleep；循环中重复排序和搜索 |
| Reliability | loader 异常导致 Lock 永久未释放 |
| Maintainability | Email canonicalization 规则复制到三个业务流程 |
| Negative | 参数列表形式安全调用 subprocess；跨文件安全解析并校验上传目录边界 |

案例选择原则：

- 不复用 `seed-v1` 的 Case ID 或 Patch；
- 问题必须能从 PR 描述和 Diff 直接证明；
- 继续覆盖 correctness、security、performance、reliability、maintainability；
- Negative samples 专门检查 Prompt v2 的证据边界和安全降噪；
- 多文件案例检查模型能否跨文件理解 API 契约，而不是只看单文件片段。

#### 冻结机制

测试固定以下 SHA-256：

```text
9902805446cea36d711bdb8450f64609633ac6599099dc5810726c78f302567c
```

自动化测试同时检查：

- Dataset Version 为 `holdout-v1`；
- 恰好 10 个案例和 2 个 negative samples；
- 与 `seed-v1` 的 Case ID、Patch 完全隔离；
- 五个目标 Category 均被覆盖；
- 预期 Finding 总数为 8；
- 至少存在一个多文件案例；
- Mock self-test 达到完整匹配，但明确不是模型质量 baseline。

固定 SHA 的目的不是阻止合理修改，而是让任何修改都必须显式升级预期 Hash 并接受代码评审，防止看到模型答案后无意调整题目或标签。

#### 自动化验证结果

```text
Eval tests: 35 passed
All tests: 201 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (84 source files)
git diff --check: passed
Holdout Mock CLI: 10/10 case pass, usage remained null
```

这些是数据结构、隔离规则和评测器自测结果，不是 DeepSeek 质量结果。本批没有调用真实模型；必须在数据集提交并冻结后才能进行第一次 live holdout baseline。

#### 面试怎么讲

> Prompt v2 在开发集上达到满分后，我没有继续把这个结果当作泛化能力，而是先冻结一套新的 holdout。它与开发集的 Case ID 和 Patch 完全隔离，覆盖新的正确性、安全、性能、可靠性和多文件场景，并用 SHA-256 测试防止看到模型输出后调整标签。这样后续多 Agent 或 Prompt 改造可以分别看开发集回归和未参与调优数据上的表现。

#### 准确边界与尚未完成

- Holdout 仍是人工设计的 10 个小案例，不等于真实 PR 分布；
- 文件在仓库中可见，属于工程上的冻结验证集，不是对开发者完全隐藏的盲测集；
- 本节构建阶段只完成了 Mock self-test；真实 DeepSeek holdout 结果和审计记录在下一节；
- 运行一次仍无法估计随机性，后续需要重复运行或更大的真实 PR 样本；
- 在首次 live 结果产生前，不应根据猜测继续修改 Prompt v2。

---

### 3.14 首次 Holdout Baseline 与人工审计

#### 执行原则

`holdout-v1` 在提交 `a19c430` 后才第一次调用真实 DeepSeek。运行前没有继续修改 Prompt、Expected Finding、Matcher 或案例内容；运行时工作区干净。这样结果可以作为 Prompt v2 对新案例的第一次泛化证据，而不是又一次开发集调优结果。

执行命令：

```powershell
python -m evals.runner `
  --dataset evals/cases/review_holdout_v1.json `
  --provider deepseek `
  --live `
  --save-baseline
```

原始结果保存为：

```text
evals/baselines/deepseek-flash__review-v2__holdout-v1.json
```

#### 可复现元数据

```text
Provider: deepseek
Model: deepseek-flash
Prompt version: review-v2
Dataset version: holdout-v1
Dataset SHA-256: 9902805446cea36d711bdb8450f64609633ac6599099dc5810726c78f302567c
Git commit: a19c4300282d650206a37b8637af9b88e55d4bd2
Git dirty: false
Dataset cases: 10
Executed cases: 10
Successful cases: 10
Transport errors: 0
Provider errors: 0
Schema errors: 0
```

#### 原始自动评分

以下数据来自当时冻结的 Dataset、Matcher 和 Metrics，原样保留：

| 指标 | 结果 |
|---|---:|
| Recall | 0.75 |
| Precision | 0.6667 |
| F1 | 0.7059 |
| Expected Findings | 8 |
| Matched Findings | 6 |
| False Positives | 3 |
| Negative Sample False Positives | 1 |
| Negative Sample Case Pass Rate | 0.5 |
| Category Match | 0.8333 |
| Severity Match | 0.5 |
| Case Pass Rate | 0.3 |
| Schema Validity | 1.0 |

原始自动评分看起来明显低于 `seed-v1`，但不能直接得出“模型漏掉了两个问题、产生了三个错误 Finding”的结论。逐案例审计发现其中混合了模型标签偏差、Matcher 漏配和 Fixture 本身错误。

#### 两个 Matcher 漏配

模型实际正确发现了以下两个 Expected Finding：

1. `can_debit` 使用 `balance <= amount`，错误拒绝余额与扣款额相等的合法交易；
2. Signup、Login、Invitation 三个流程重复 `email.strip().lower()` normalization。

但 Matcher v1 要求关键词短语按较严格的形式出现：

- 模型使用 `balance == amount`、`remaining balance would be exactly zero`，没有命中标签中的固定短语顺序；
- 模型使用 `normalization logic duplicated`，没有命中 `duplicated normalization` 的固定词序。

因此每个正确 Finding 都被同时计为一条 False Negative 和一条 False Positive。这里的问题是评测器文本归一化能力不足，不是模型没有发现缺陷。原始 baseline 不回写；未来通过版本化 Matcher 和离线 re-score 修正。

#### 一个 Negative Fixture 缺陷

`holdout-negative-contained-upload-path` 原本期望零 Finding。路径解析代码本身确实正确使用 `resolve()` 和 `relative_to()` 做目录边界校验，但另一个新增文件直接调用：

```python
def read_upload(filename):
    return resolve_upload(filename).read_bytes()
```

该新增文件没有导入同一 PR 另一个新增文件中的 `resolve_upload`，运行时会触发 `NameError`。模型报告这一问题是正确的，所以这条自动 False Positive 实际暴露了 Fixture 缺陷。

不能在看到答案后修改 `holdout-v1` 并覆盖历史证据。后续应创建 `holdout-v2` 修复 Import，同时保留本次 baseline，明确说明第一次盲测也验证了评测数据自身需要审计。

#### 人工审计后的模型结论

- 8 个原定目标问题实际全部被模型识别，人工审计 Recall 为 `1.0`；
- 模型一共输出 9 条 Finding：8 条对应原定缺陷，另 1 条对应 Fixture 真实存在的缺失 Import；
- 两个自动 False Negative 均来自 Matcher 漏配；
- 三个自动 False Positive 中，两个是 Matcher 对同一正确 Finding 的误判，另一个是真实 Fixture 缺陷；
- 如果只修正两个 Matcher 漏配、暂不修正错误的 negative fixture，严格 Case Pass 将从 `0.3` 变为 `0.5`；
- 这不等于所有标签都正确：Category 和 Severity 仍存在真实偏差。

对原定 8 个问题做人工标签审计：

| 项目 | 人工审计结果 |
|---|---:|
| 问题是否识别 | 8/8 |
| Category 正确 | 7/8 |
| Severity 正确 | 5/8 |

真实标签偏差包括：

- async 中 `time.sleep` 被标为 `correctness/medium`，Expected Category 是 `performance`；
- 折扣百分比/小数单位错误被评为 `high`，Expected Severity 是 `medium`；
- Shell 命令注入被评为 `critical`，Expected Severity 是 `high`；
- Loader 异常导致 Lock 未释放被评为 `high`，Expected Severity 是 `medium`。

后三个 Severity 分歧具有一定业务语境差异：负价格可能影响资金、命令执行可能造成进程权限下的全面失陷、永久持锁可能扩大成服务不可用。因此它们既说明模型仍倾向高估，也说明 Severity Ground Truth 需要结合威胁模型和影响范围，而不是把人工标签当作绝对真理。

#### Token 与延迟

```text
Mean latency: 4040.82 ms
P50 latency: 2689.73 ms
P95 latency: 10686.61 ms
Input tokens: 10403
Output tokens: 8016
Total tokens: 18419
Cost: unavailable
```

这些数字只描述本次 Holdout 运行。由于 Dataset 与 `seed-v1` 不同，不能把二者的延迟、Token 或质量指标直接当作 Prompt A/B 对比；P95 也只有 10 个样本，不能外推为生产 SLO。

#### 这次 Holdout 真正证明了什么

正面证据：

- Prompt v2 在 8 个新缺陷上没有出现人工确认的漏报；
- 安全 subprocess negative sample 正确返回空 Findings；
- 模型能够处理跨文件折扣契约并发现百分比/小数单位错误；
- 所有响应都通过结构化 Schema，无 Provider 或 Transport 错误。

暴露的问题：

- Category 仍可能偏移；
- Severity 在高影响描述下仍容易上调；
- 关键词 Matcher 对符号表达和词序改写不够鲁棒；
- 人工构造的评测数据本身可能有缺陷，必须审计 Fixture，而不是盲信 Ground Truth；
- 一次运行仍无法证明随机稳定性。

#### 面试怎么讲

> 我在开发集上把 Prompt v2 调到满分后，专门冻结了一套未参与调优的 Holdout。第一次真实运行的自动 Recall 只有 75%，但我没有直接把它归因于模型，而是逐案例审计。结果发现两个所谓漏报其实是 Matcher 无法识别等价措辞，另一个所谓误报是 negative fixture 自己漏了跨文件 import。人工审计确认模型找到了 8/8 个目标问题，但 Category 只有 7/8、Severity 只有 5/8。这个过程让我把模型质量、评测器质量和数据质量分开处理，也保留原始 baseline，没有为了让分数好看而回写标签。

这个结果比“Holdout 也是满分”更有工程价值，因为它展示了评测系统本身也需要版本化、审计和可追溯修正。

#### 准确边界与下一步

- 人工审计结论尚未形成机器可读的 adjudication artifact；
- Matcher v1 的原始分数必须保留，不能用人工结论覆盖；
- `holdout-v1` 已经暴露给开发流程，后续修正版不能再称为完全未见数据；
- 下一批应实现 Matcher v2 与离线 re-score，并通过新版本报告展示原始分数和重评分数；
- 应创建 `holdout-v2` 修复 Fixture，但保留 `holdout-v1` 和本次结果；
- 在进入多 Agent 后，应增加新的未见案例或真实 PR 样本验证泛化。

---

### 3.15 Matcher v2 与不可变 Baseline 的离线重评分

#### 要解决的问题

首次 Holdout 人工审计确认，模型已经发现 `exact balance` 和 `duplicated normalization` 两个问题，但 Matcher v1 没有把它们与 Expected Finding 对齐：前者包含 `<=`、`balance == amount` 和 `remaining balance is exactly zero` 等符号或改写表达，后者把 `duplicated normalization` 改写成了 `normalization logic duplicated`。如果直接修改原 baseline、Expected Finding 或 Prompt，会把“评测器修正”与“模型质量变化”混在一起，也会破坏第一次运行的历史证据。

#### 设计选择

本批把 Matcher 当作独立、需要版本化的评测组件：

- `text-v1` 完整保留原来的连续短语匹配行为；
- `text-v2` 增加比较运算符规范化，例如把 `<=` 转换成稳定的文本 Token；
- `text-v2` 对一个多词 alternative 支持无序 Token 包含，因此能识别词序变化和中间插词；
- 文件路径仍必须完全一致；
- Finding 仍使用确定性的一对一贪心对齐；
- Category、Severity 和 Expected Line Range 仍不参与匹配，防止答案泄漏；
- 不引入 Embedding 或第二次 LLM 判断，保证结果离线、确定且无额外费用。

历史 baseline 没有 `matcher_version` 字段时按 `text-v1` 加载；新评测显式记录 `text-v2`。因此旧 JSON 可以继续读取，同时以后能够准确说明一份分数由哪个 Matcher 产生。

#### 离线 Rescore

新增 `python -m evals.rescore`，输入冻结 Dataset 和历史 Model Baseline，只复用已经保存的 `actual_findings` 重新计算匹配关系与聚合指标。它不会实例化 Provider，也不会读取应用 Settings、`.env`、数据库或 GitHub。

安全约束包括：

- Dataset version、SHA-256 和 Case IDs 必须与 source baseline 一致；
- 原始 baseline 不被修改；
- rescore 使用独立的 `agent-review-offline-rescore-v1` Schema；
- artifact 保存时拒绝覆盖已有文件；
- artifact 记录 source baseline SHA-256、source/target Matcher version 和重评分时间；
- 原模型的输出、Token Usage、Latency、Model、Prompt、Git Commit 和执行时间保持不变；
- `run_kind` 改为 `offline_rescore`，并明确 `is_model_baseline=false`，避免把重新打分误认为重新调用模型。

执行命令：

```powershell
python -m evals.rescore `
  --baseline evals/baselines/deepseek-flash__review-v2__holdout-v1.json `
  --dataset evals/cases/review_holdout_v1.json `
  --matcher-version text-v2 `
  --save
```

生成的独立 artifact：

```text
evals/rescores/deepseek-flash__review-v2__holdout-v1__text-v2.json
```

#### 原始分数与重评分结果

| 指标 | Matcher v1 原始 Baseline | Matcher v2 离线 Rescore |
|---|---:|---:|
| Recall | 0.75 | 1.0 |
| Precision | 0.6667 | 0.8889 |
| F1 | 0.7059 | 0.9412 |
| Matched Findings | 6 | 8 |
| False Positives | 3 | 1 |
| False Positives / Case | 0.3 | 0.1 |
| Case Pass Rate | 0.3 | 0.5 |
| Category Match | 0.8333 | 0.875 |
| Severity Match | 0.5 | 0.625 |
| Negative Sample Pass Rate | 0.5 | 0.5 |

两个 Matcher 漏配被修正后，8 个 Expected Findings 全部与已有模型输出对齐。剩余的一条 False Positive 没有被“优化掉”，因为它是模型正确发现的 Fixture 缺失 Import；这应由新 Dataset 版本修复，而不能通过放宽 Matcher 或篡改 `holdout-v1` 掩盖。

Category 和 Severity 比例也随匹配分母从 6 增加到 8 而重新计算。它们没有变成满分，真实的分类和严重程度分歧仍然被保留。

#### 自动化验证证据

- Matcher v1 对两个历史案例仍复现原漏配，证明兼容路径没有被悄悄改写；
- Matcher v2 能匹配运算符表达和多词词序变化；
- 原有“同文件、文本证据、一对一、不使用标签”的测试继续通过；
- Rescore 测试验证 source baseline 内存内容不变；
- Dataset SHA-256 不一致会被拒绝；
- artifact 重复保存会被拒绝；
- checked-in rescore 的 source SHA-256 与原 baseline 文件一致；
- CLI 测试仅依赖本地 JSON，在没有 Provider 初始化的路径上完成。

本批最终的自动化测试数量和静态检查结果记录在第 8.1 节。

#### 真实集成证据

本批没有再次调用 DeepSeek，也没有访问 GitHub、数据库或 Redis。重评分使用的是上一批已经保存的真实 DeepSeek 输出，因此本批证据属于“真实模型输出的离线再分析”，不是一次新的真实模型集成运行。Token 和 Latency 与原 baseline 完全相同只能证明元数据被保留，不能算新的性能测量。

#### 面试怎么讲

> Holdout 第一次自动评分只有 75% Recall，但人工审计发现其中两个漏报其实是 Matcher 无法识别等价表达。我没有覆盖原 baseline，也没有为了提高分数去修改 Expected Finding，而是把 Matcher 独立版本化：v1 保留历史语义，v2 增加运算符规范化和多词无序匹配。然后通过完全离线的 Rescore 复用同一批模型输出，Recall 从 0.75 修正到 1.0。重评分 artifact 绑定原 baseline 和 Dataset 的 SHA-256，所以可以证明变化只来自评测器，而不是重新调用模型或改 Prompt。

这体现的不是单纯“把分数调高”，而是区分模型、数据和评测器三个独立误差来源，并保留可审计的证据链。

#### 准确边界与尚未完成

- Matcher v2 仍然是规则化文本匹配，不具备真正的自然语言语义推理能力；
- 无序 Token 包含可能在长文本中产生偶然共现，当前依靠同文件、Term Group Coverage 和一对一匹配降低风险；
- `holdout-v1` 的 missing-import Fixture 缺陷仍然存在且必须保留；
- 下一批应创建 `holdout-v2` 修复 Fixture，而不是覆盖 v1；
- 需要新的未见案例或真实 PR 样本继续检验 Matcher v2 的泛化与误配风险。

---

### 3.16 Holdout v2：修复评测数据但保留第一次失败证据

#### 要解决的问题

`holdout-v1` 的 `holdout-negative-contained-upload-path` 原本用于验证安全的目录边界检查，Expected Findings 为空。但 `src/upload_api.py` 调用了另一个新增文件中的 `resolve_upload`，却没有导入它。DeepSeek 正确报告了运行时 `NameError`，因此原自动评分中的一条 negative false positive 实际是 Fixture 缺陷。

直接修改 `review_holdout_v1.json` 会改变已经运行过真实模型的数据集内容，使它的 SHA-256、原始 baseline 和审计结论失去可追溯性。因此本批保留 v1 不变，新增派生版本 `review_holdout_v2.json`。

#### 设计选择

`holdout-v2` 只做经过审计的最小修复：

```python
from .upload_paths import resolve_upload


def read_upload(filename):
    return resolve_upload(filename).read_bytes()
```

同时更新该 synthetic PR 的 `head_sha`，保证“Patch 变化但 Head SHA 不变”这种不一致不会进入评测数据。其余约束保持不变：

- 10 个 Case ID 和原顺序不变；
- 8 个 Expected Findings 不变；
- 2 个 negative samples 不变；
- `src/upload_paths.py` 的路径 containment 实现不变；
- 另外 9 个案例逐对象完全等于 v1；
- 不修改 Prompt、Matcher、Category、Severity 或 Match Terms。

版本身份：

```text
Dataset version: holdout-v2
Dataset SHA-256: ece7d8102bb1973afa1c290928e4051e9f497f5e99045974ab7e04b30e2b3303
Parent evidence: holdout-v1
```

#### 为什么 v2 不能再叫盲测集

v2 是在阅读 v1 模型结果后产生的数据修正版。它可以作为未来 Prompt、Matcher 和 Agent 架构的回归集，但不能作为 Prompt v2 的第一次未见数据证据。真正的泛化验证仍需要新的未见案例或真实 PR 样本。

同样，不能把 v1 和 v2 的分数直接解释成模型进步：两次输入的 Dataset SHA-256 不同。现有 Baseline Comparator 和 Rescore 都会校验 Dataset 身份，阻止跨数据集混比。

#### 自动化验证证据

- `holdout-v1` 的 SHA-256 冻结断言保持不变；
- `holdout-v2` 增加独立 SHA-256 冻结断言；
- lineage 测试确认只有目标案例发生变化；
- lineage 测试进一步构造期望对象，限制变化为 `head_sha`、Import Patch 和对应行数统计；
- v2 仍包含 10 个案例、8 个 Expected Findings 和 2 个 negatives；
- v2 Mock evaluator self-test 为 10/10 Case Pass、Recall 1.0、False Positives 0；
- Mock Usage 继续为 `null`，没有伪造 Token 或 Cost。

本批最终测试数量和静态检查结果记录在第 8.1 节。

#### 真实集成证据

本批没有调用 DeepSeek，没有生成 v2 Model Baseline，也没有访问 GitHub、数据库或 Redis。Mock 100% 只证明 Dataset、Matcher 和 Metrics 的机械链路一致，不代表模型在修复后的 negative sample 上一定返回零 Finding。

#### 面试怎么讲

> 第一次 Holdout 的一个 negative sample 被模型报错后，我逐文件审计发现不是模型误报，而是评测 Fixture 漏了跨文件 Import。我没有为了让指标变好而覆盖原数据，而是保留 v1、原 baseline 和人工审计，再创建只修复该问题的 v2。测试会逐对象比较两个版本，保证另外九个案例和所有 Expected Findings 都没有被偷偷调整。这让评测数据的修订也具有版本、身份和证据链。

#### 准确边界与下一步

- `holdout-v2` 是 corrected regression set，不是新的 blind holdout；
- 当前只有 Mock self-test，尚无修复后案例的真实模型结果；
- Eval Runner 的显式 `--case-id` 已在下一节实现，可只运行目标案例做低成本验证；
- 部分 live run 只能作为 trial，不能保存成完整 Model Baseline；
- 进入 Agent 化阶段后仍需要新的未见数据验证多 Agent 是否真正泛化。

---

### 3.17 `--case-id`：低成本、可控的精确 Eval 试跑

#### 要解决的问题

Eval Runner 原来只能运行完整 Dataset，或者用 `--max-cases N` 从头截取前 N 个案例。`holdout-v2` 修复的 negative fixture 位于数据集后部，如果只想验证这一条，就必须调用整个 Dataset，或者为了迁就位置而调整案例顺序。这两种做法分别增加真实模型费用或破坏 Dataset 稳定性。

#### 设计选择

Runner 新增显式 `--case-id`：

```powershell
python -m evals.runner `
  --dataset evals/cases/review_holdout_v2.json `
  --provider deepseek `
  --live `
  --case-id holdout-negative-contained-upload-path
```

选择规则集中在同一个 `_select_cases` 边界中，Mock、Live 和 CLI 复用同一套语义：

- Case ID 必须完全匹配 Dataset 中的一个案例；
- 未知 ID 返回清晰错误；
- `--case-id` 与 `--max-cases` 互斥；
- 精确选例属于 partial run，`is_model_baseline=false`；
- partial run 不能与 `--save-baseline` 组合；
- `dataset_case_count` 保留完整数据集大小，`executed_case_count` 记录实际执行数量；
- 默认 Mock、全量 Live 和原有 `--max-cases` 行为保持不变。

未知 Case ID 的校验被放在收集 Git 状态和创建 Provider 之前。这样无效命令不会读取真实 Provider 配置，更不会触发网络调用或付费请求。

#### 自动化验证证据

- Mock Runner 能从 10-case `holdout-v2` 精确选择目标 negative case；
- 精确运行的 `executed_case_count=1`，但 `dataset_case_count=10`；
- 目标 Mock 结果 Case Pass 1.0、False Positives 0；
- 未知 Case ID 会被拒绝；
- 未知 live Case ID 在 Git State Collector 和 Provider Factory 调用前失败；
- `--case-id` 与 `--max-cases` 组合会被拒绝；
- `--case-id` 与 `--save-baseline` 组合会被拒绝；
- Live Runner 的测试替身确认精确选例仍保留 Usage 与可复现元数据。

本批最终测试数量和静态检查结果记录在第 8.1 节。

#### 真实集成证据

2026-09-25 已使用真实 DeepSeek 对修复后的 negative case 完成一次精确 partial trial：

```text
Provider: deepseek
Model: deepseek-flash
Prompt: review-v2
Matcher: text-v2
Dataset: holdout-v2
Dataset SHA-256: ece7d8102bb1973afa1c290928e4051e9f497f5e99045974ab7e04b30e2b3303
Case: holdout-negative-contained-upload-path
Dataset cases: 10
Executed cases: 1
Git commit: 4e0c3f556809cf1ecfa95eeaa7fbaec9613ffe74
Git dirty: true
Outcome: successful_case
Schema valid: true
Expected findings: 0
Actual findings: 0
False positives: 0
Case pass: true
```

DeepSeek 明确认可 `Path.resolve()` 后再用 `relative_to(UPLOAD_ROOT)` 验证目录边界的实现，并指出绝对路径、`..` 和 symlink escape 都会在读取前被解析和拒绝。补齐跨文件 Import 后，模型没有再次报告 v1 中的 `NameError`，也没有对正确的路径 containment 产生安全误报。

本次调用的真实观测数据：

```text
Latency: 7876.82 ms
Input tokens: 1127
Output tokens: 1625
Total tokens: 2752
Cost: unavailable
```

`is_model_baseline=false`，符合 single-case partial trial 的定义。`git_dirty=true` 也被如实保留：执行时 `--case-id` 实现尚未提交，工作区还有其他未提交文件，因此不能把这次结果描述成绑定干净代码快照的正式 baseline。

#### 面试怎么讲

> 为了验证一个修复后的评测案例，我没有重新跑完整模型评测，而是给 Runner 增加精确 Case ID 选择。它和 max-cases 互斥，部分运行不能保存成 baseline，而且未知 ID 会在 Provider 初始化前失败。真实 DeepSeek 单案例试跑最终返回零 Finding，验证修复后的 negative fixture 不再触发缺失 Import，也没有对正确的路径 containment 产生误报。这样既控制了模型费用，也避免通过调整 Dataset 顺序来迁就工具。

#### 准确边界与下一步

- `--case-id` 只控制案例选择，不改变 Prompt、模型参数或 Matcher；
- 单案例结果样本量为 1，不能外推整体 Recall、误报率或延迟分布；
- corrected fixture 已被开发流程看到，真实试跑也不能称为盲测；
- 本次 `7.88s` 延迟和 2752 Tokens 只描述一个请求，不能当成生产 SLO 或平均成本；
- partial trial 结果不写入 `evals/baselines/`，也不用于跨 Dataset 的模型质量对比。

---

### 3.18 进入 Agent 化阶段前的架构评审

#### 为什么先评审再写 Agent

当前系统已经完成真实 GitHub/DeepSeek 主链路、业务幂等、故障恢复和离线评测。如果直接把四个模型调用塞进 Worker，会同时引入重复 Finding、标签冲突、成本放大和重试重复调用，反而破坏现有可靠性。

因此在写多 Agent 代码前，先固化当前单 Agent 的能力、证据、限制和演进原则。详细设计见：

- [`MULTI_AGENT_READINESS_AND_DESIGN.md`](MULTI_AGENT_READINESS_AND_DESIGN.md)

#### 当前准确定位

当前是可靠、可评测、真实联调通过的单 Agent Summary Review 系统。已经实现的核心价值包括：

- GitHub App → FastAPI → Celery → DeepSeek → GitHub Review 真实闭环；
- Delivery、Task、Report 和 Publication 幂等；
- Provider 错误分类、指数退避、`Retry-After` 和阶段级恢复；
- Prompt v2、真实 baseline、Holdout、Matcher v2 和离线 Rescore；
- 能区分模型错误、评测器错误和 Dataset Fixture 缺陷。

尚未实现：

- Planner、专业 Reviewer、Validator 和 Aggregator；
- Agent 级状态、Usage、持久化和断点恢复；
- Agent 并发、预算与降级；
- Tree-sitter、语法分片、行号和 Inline Comment。

#### 阶段命名修正

原始 `PROJECT_PLAN.md` 中：Phase 2 是可靠性与行级评论，Phase 3 是语法上下文，Phase 4 才是多 Agent。实际实施中，评测能力被提前完成，而行级评论和 Tree-sitter 仍未开始。

后续统一称为“Agent 化阶段”或“Multi-Agent Phase”，不再把它简称为 Phase 2，避免面试时阶段定义前后不一致。

#### 为什么考虑多 Agent

当前一个 General Prompt 同时负责 correctness、security、reliability、performance、maintainability、evidence、severity 和去重。多 Agent 的目标是把这些竞争职责拆成可独立设计和评测的专业 Reviewer，再通过确定性 Validator 和 Aggregator 控制证据、标签和重复意见。

预期收益包括专业召回、标签稳定性、可解释性和可扩展性，但这些只是待验证假设。更多 Agent 也会带来更高 Token、更多 429、重复 Finding 和更复杂的恢复语义。

#### 最关键的可靠性风险

当前恢复单位是完整 Report。如果四个 Agent 中前三个成功、第四个超时，而中间结果没有持久化，Celery 重试会重新调用前三个 Agent并重复计费。

因此生产多 Agent 上线前必须设计 Agent Execution 身份、单 Agent 结果持久化、attempt count、Usage、错误码和 Aggregation 幂等。不能只实现几个 Agent 类就声称完成多 Agent 系统。

#### 第一批边界

下一批只建立兼容接缝：

```text
ReviewPipelineService
        → ReviewEngine
            → ReviewOrchestrator
                → GeneralReviewAgent
                    → existing AIReviewService
```

这一批仍只调用一次模型，不修改 Prompt、Provider、Report Schema 或数据库，不增加并发，也不声称质量提升。验收标准是生产行为与现有单 Agent 完全兼容，并为后续专业 Agent 建立可测试边界。

#### 面试怎么讲

> 我没有为了使用 Agent 概念直接堆多个模型调用，而是先审计现有单 Agent 系统的恢复单位和评测基线。多 Agent 最大的工程风险不是类怎么拆，而是第四个 Agent 失败后会不会让前三个成功调用重新付费。因此我先用 ReviewEngine 和单 General Agent 建立兼容接缝，再逐步加入专业 Reviewer、Agent 级持久化和聚合规则，并要求每一步用同一评测体系证明收益。

---

## 4. 当前能力快照

截至 2026-09-26，系统已经具备：

### 4.1 主链路

- 真实 GitHub App Webhook 接入；
- GitHub App Installation Token；
- PR 元数据和 Diff 获取；
- Diff 筛选和大小预算；
- DeepSeek 真实结构化代码评审；
- Report/Finding 持久化；
- GitHub Summary Review 发布；
- 新 commit 自动触发重新评审。

### 4.2 可靠性

- Webhook Delivery 幂等；
- `repository + PR number + head SHA` 任务幂等；
- Report 幂等；
- Publication 幂等和远程对账；
- 可重试/不可重试错误分类；
- 指数退避和随机抖动；
- `Retry-After` 感知；
- Report 持久化后的断点恢复；
- 数据库唯一约束兜底；
- PR Head SHA 变化时阻止发布旧结果。

### 4.3 评测基础

- 10 个开发集案例和 10 个冻结 Holdout 案例；
- 两个数据集各包含 2 个 negative samples；
- 确定性 Matcher；
- Recall、误报、类别、严重程度、Schema、Case Pass 和 latency 指标；
- Mock evaluator self-test；
- Provider token usage 数据通道；
- 显式开启的 DeepSeek Live Eval；
- 带 Git/Dataset/Prompt/Model 元数据的可复现 baseline；
- 校验 Dataset 身份并输出指标和逐案例差异的离线 Comparator；
- 具备证据约束、测试降噪和明确 Severity Rubric 的版本化 Prompt v2；
- 在相同 `seed-v1` 上保存并比较 `review-v1` 与 `review-v2` 的真实 DeepSeek baseline；
- 用 SHA-256、ID/Patch 隔离和覆盖测试冻结的 `holdout-v1` 泛化验证集；
- 保存首次 Holdout 原始 baseline，并区分模型错误、Matcher 漏配和 Fixture 缺陷的人工审计；
- 版本化的 `text-v1` / `text-v2` Matcher，以及绑定原 Baseline SHA-256 的纯离线 Rescore artifact；
- 在不调用模型的条件下重新计算 Finding 对齐、Recall、误报、Category、Severity 和 Case Pass；
- 保留 `holdout-v1` 原始证据，并通过严格 lineage 测试冻结只修复已知 Fixture 的 `holdout-v2`；
- 使用 `--case-id` 做精确、低成本的 Mock 或 Live partial evaluation，并在 Provider 初始化前校验选择。

### 4.4 安全边界

- `.env`、`secrets/`、`*.pem` 被 Git 忽略；
- Docker build context 排除凭据和本地缓存；
- 文档和测试不输出 API Key、Webhook Secret 或私钥；
- 默认 Eval 不加载付费模型；
- 模型输出仍需经过 Pydantic 和业务白名单校验。

---

## 5. 面试如何逐步讲解这个项目

### 5.1 30 秒版本

> 我做了一个事件驱动的 GitHub PR 自动代码评审系统。GitHub Webhook 进入 FastAPI 后快速落库并投递 Celery，Worker 获取 Diff、调用 DeepSeek 生成结构化 Finding，再把结果发布回 PR。项目重点不只是模型调用，我还实现并实测了两级幂等、错误分类、指数退避、Retry-After、Report 断点恢复和 Publication 防重复，并建立了 10 个固定案例的离线评测框架，为后续 Prompt 和多 Agent 改造提供回归基线。

### 5.2 两分钟版本

可以按四层展开：

1. **可运行**：FastAPI、Celery、Redis、MySQL、GitHub App、DeepSeek 组成真实闭环。
2. **可恢复**：Webhook、Task、Report 和 Publication 都有幂等策略；可恢复错误重试，永久错误快速失败。
3. **可验证**：使用 Fault Stub 可控注入 Timeout、429、500、503、401 和非法输出，用日志和数据库共同验收。
4. **可评估**：区分开发集和冻结 Holdout，计算 Recall、Precision、F1、误报、类别、严重程度、Schema 和 Case Pass，避免只凭一次 Demo 或调优过的数据判断模型质量。

### 5.3 五分钟技术深挖顺序

推荐按下面顺序讲，不要一开始就陷入类名和目录：

#### 第一层：为什么要异步

Webhook 需要快速响应，而 GitHub API、模型调用和发布都可能很慢，所以 API 只完成验签、幂等落库和投递，耗时工作交给 Celery。

#### 第二层：为什么要分阶段持久化

外部调用之间可能崩溃。Report 独立提交后，重试可以直接从 Publication 恢复，避免重新获取 Diff 和重复付费调用模型。

#### 第三层：怎么处理至少一次投递

使用 Delivery ID、PR Head SHA、Report 唯一约束、Publication 唯一约束和 GitHub 隐藏标记共同实现业务去重，不把它夸大成分布式 Exactly Once。

#### 第四层：怎么证明重试真的有效

不是等待真实供应商随机失败，而是搭建兼容 Responses API 的 Fault Stub，精确控制第一次失败、失败次数、状态码和 Retry-After，然后观察 Worker 日志、attempt count、Report、Publication 和 GitHub Review。

#### 第五层：怎么避免模型优化靠感觉

建立固定 seed set 和一对一 Matcher。先验证评测器本身，再运行真实模型 baseline。Prompt、多 Agent 或行级评论上线前后都用同一数据集比较。

---

## 6. 可以重点追问的工程亮点

### 6.1 为什么不直接在 Webhook 中调用模型

因为会导致：

- Webhook 超时；
- GitHub 重投递扩大重复调用；
- 模型限流直接影响 API 可用性；
- 进程退出后难以恢复执行阶段。

异步任务和状态持久化把外部事件接收与长耗时任务解耦。

### 6.2 为什么有唯一约束还要应用层幂等

应用层查询可以快速复用已有结果，减少异常；数据库唯一约束处理并发竞争，是最终一致的兜底。两层解决的是不同问题。

### 6.3 为什么 Report 和 Publication 分开

模型输出生成成功和 GitHub 发布成功是两个独立事实。如果合在一个事务概念里，发布失败后可能被迫重新调用模型。分开后可以从已持久化 Report 恢复。

### 6.4 为什么不用真实 DeepSeek 做故障测试

真实故障不可控、不可重复，并可能产生费用。Fault Stub 仍经过正式 HTTP Transport 和错误分类，只替换外部端点，因此能稳定验证重试逻辑，又不污染生产 Provider。

### 6.5 为什么 Matcher 不使用 category/severity

如果匹配时已经使用期望类别和严重程度，之后再统计 category/severity match 会形成答案泄漏，使指标虚高。因此第一步只判断“模型说的是不是同一个问题”，第二步再比较标签。

### 6.6 为什么 usage 不写数据库

当前 usage 首先服务于离线评测，现有业务 Report 不需要承担计费职责。先通过调用结果向 Eval 传递，避免为尚未稳定的成本模型做 migration；以后确认产品需求后再设计独立观测或计费模型。

---

## 7. 当前可用于简历的表述

以下表述与现有实现和实测一致：

> 构建基于 FastAPI、Celery、Redis、MySQL、GitHub App 和 DeepSeek Responses API 的事件驱动 PR 自动评审系统，完成 Webhook → Diff → 结构化 AI Review → GitHub Review 的真实端到端联调。

> 通过 Delivery ID、PR Head SHA、数据库唯一约束、Report 断点恢复及 Publication 远程标记对账实现业务幂等；实测 GitHub Webhook Redelivery 不会重复创建任务、调用模型或发布 Review。

> 搭建兼容 Responses API 的可控故障注入环境，验证 Timeout、429、500/503 的自动恢复、指数退避和 Retry-After 感知，以及 401 和非法结构化输出的快速失败；可恢复场景最终仅保留一份 Report、Publication 和 GitHub Review。

> 建立包含 correctness、security、performance、maintainability、reliability 和 negative samples 的 10 案例评测种子集，实现同文件文本匹配、一对一 Finding 对齐及 Recall、Precision、F1、误报、类别、严重程度、Schema 和 Case Pass 指标；保存绑定 Git、Dataset、Prompt 和 Model 版本的 DeepSeek baseline，并实现离线 baseline 差异报告，为 Prompt 和 Agent 架构迭代提供回归基础。

> 基于固定 `seed-v1` 对 DeepSeek Prompt 做单变量回归：Prompt v2 在一次完整运行中保持 100% Recall，将误报从 10 降至 0、Severity Match 从 25% 提升至 100%，总 Token 基本持平；同时保留版本化 baseline，并明确该结果不外推为生产准确率。

暂时不要写入简历的内容：

- “多 Agent 已实现”；
- “行级评论已实现”；
- 把 10 个种子案例的指标泛化为生产环境整体模型质量；
- “生产级高并发”；
- “LLM Exactly Once”；
- “Token 成本已经优化”；
- 尚未实测的延迟或吞吐数字。

---

## 8. 当前测试证据

### 8.1 自动化测试演进

| 阶段 | 测试数量 | 说明 |
|---|---:|---|
| 上次 Phase 1 文档 | 144 | 主链路代码完成，真实联调未完成 |
| DeepSeek 与故障恢复阶段 | 159 | Provider、Fault Stub 和恢复测试 |
| 第一批 Eval | 175 | Seed、Matcher、Metrics、Mock Runner |
| Usage metadata | 182 | OpenAI、DeepSeek、Mock、AI Service、Pipeline、Eval 回归 |
| 受保护的 Live Eval | 192 | 显式 live 开关、低成本试跑、Baseline 安全写入与可复现元数据 |
| Baseline Comparator | 197 | Dataset 身份校验、派生指标、逐案例 Finding 与标签差异 |
| Prompt v2 | 198 | 证据约束、测试降噪、上下文边界与 Severity Rubric |
| Holdout v1 | 201 | 冻结 SHA、开发集隔离、类别/负例/多文件覆盖与 Mock 自测 |
| Matcher v2 与离线 Rescore | 210 | Matcher 版本兼容、运算符/词序匹配、SHA 绑定、不可变重评分和离线 CLI |
| Holdout v2 Fixture 修复 | 212 | 独立 SHA、最小差异 lineage、原始证据保留和 corrected regression set Mock 自测 |
| Eval 精确 Case 选择 | 217 | `--case-id`、互斥/未知 ID 门禁、partial baseline 限制和 Provider 初始化前失败 |

当前最近一次结果：

```text
Eval tests: 51 passed
All tests: 217 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (86 source files)
git diff --check: passed
```

### 8.2 真实环境证据

- Docker Compose 四个核心服务正常运行；
- MySQL/Redis readiness 正常；
- Alembic 位于最新 migration；
- Celery Worker 在线；
- GitHub App 真实 PR 两次评审成功；
- DeepSeek 真实识别空列表除零问题；
- Webhook Redelivery 业务幂等实测通过；
- 六类故障/恢复场景实测通过；
- 数据库只读检查和唯一索引检查通过；
- DeepSeek 单案例 Live Eval 试跑成功，usage 元数据正常；
- DeepSeek 完整 10-case baseline 已保存，运行时工作区干净并绑定 Git commit `4274964`；
- Prompt v2 baseline 在干净工作区完成并绑定 Git commit `f111d2d`；
- 两个版本的完整评测均为 10/10 合法响应，无 Transport、Provider 或 Schema 错误；
- 相同 `seed-v1` 的真实对比中，Recall 保持 `1.0`，Precision 从 `0.4444` 提升到 `1.0`，误报从 10 降到 0，Severity Match 从 `0.25` 提升到 `1.0`；
- 首次 `holdout-v1` baseline 在干净工作区完成并绑定 Git commit `a19c430`，10/10 获得合法响应；
- Holdout 原始 Recall 为 `0.75`，人工审计确认 8/8 目标问题均被识别，并定位两个 Matcher 漏配和一个 Fixture 缺陷；
- `holdout-v2` 修复后的路径 containment negative case 已完成真实 DeepSeek 精确试跑，Schema 合法、0 Findings、0 False Positives、Case Pass 通过，总计 2752 Tokens；该结果是 dirty-worktree partial trial，不是 Model Baseline。

---

## 9. 尚未完成与下一步

### 9.1 最近下一步

1. 实施 Agent 架构接缝：`ReviewEngine`、`ReviewAgent`、General Agent、Orchestrator 和 passthrough Aggregator；
2. 保持现有 Prompt、Provider、Report 和模型调用次数不变，先证明结构迁移零行为回归；
3. 再实现专业 Reviewer、Deterministic Planner 和 Agent Registry，先接入 Eval 而不是生产 Worker；
4. 在生产多 Agent 前完成 Finding 去重、标签冲突规则和 Agent 级持久化恢复；
5. 使用新的未见案例比较单 Agent 与多 Agent 的质量、Token 和延迟。

### 9.2 后续能力

- 自动化回归阈值；
- 行级 Finding 和 GitHub inline comment；
- 更大、更接近真实 PR 的评测集；
- Prompt injection 专项评测；
- Planner/Reviewer/Validator/Aggregator 多 Agent；
- 并行分片、限流和预算控制；
- 性能压测和实际 P50/P95 数据；
- 可观测性 Dashboard。

---

## 10. 后续更新模板

以后每完成一批代码，在本文末尾或对应章节增加以下内容：

```markdown
### YYYY-MM-DD：本批名称

#### 要解决的问题

#### 设计选择

#### 修改范围

#### 如何验证

#### 实测结果

#### 面试怎么讲

#### 准确边界与尚未完成
```

更新原则：

- 先写事实和证据，再写亮点；
- 测试通过和真实环境实测必须分开描述；
- Mock 数据不能当模型质量数据；
- 没有真实测量的性能数字不要写；
- 不记录密钥、私钥、Secret、完整 `.env` 或其他敏感数据；
- 每次代码提交前检查文档是否需要同步更新。
