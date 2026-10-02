# AgentReview 项目演进记录与面试讲解指南

> 最后更新：2026-10-01
> 记录范围：从 `cc5c769 docs: add phase one implementation interview guide` 之后，到首个 Security 架构对照 Holdout 冻结完成。

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

### 3.19 Agent 架构接缝第一批

#### 要解决的问题

原来的 `ReviewPipelineService` 直接依赖 `AIReviewService`。这对于单模型流程足够简单，但如果直接在 Pipeline 中添加多个专业 Reviewer，任务状态、Agent 选择、结果聚合和发布流程会耦合在同一个服务中，后续难以独立测试和演进。

本批目标不是增加模型能力，而是在不改变生产行为的前提下建立稳定接缝。

#### 设计选择

新增的调用关系是：

```text
ReviewPipelineService
        → ReviewEngine (Protocol)
            → ReviewOrchestrator
                → GeneralReviewAgent (ReviewAgent)
                    → existing AIReviewService
                → PassthroughReviewAggregator
                    → existing ReviewReport
```

具体边界：

- `ReviewEngine` 是 Pipeline 唯一依赖的评审入口；
- `ReviewAgent` 定义角色和 `review(context)` 契约；
- `AgentRole` 当前只开放 `general`，没有提前声称已有专业 Agent；
- `GeneralReviewAgent` 只适配现有 `AIReviewService`，不复制 Prompt 或输出映射逻辑；
- `AgentReviewResult` 把角色和 Report 一起交给 Aggregator，为后续结果来源追踪预留边界；
- `PassthroughReviewAggregator` 强制只接受一个结果，并原样返回同一个 `ReviewReport`；
- `ReviewOrchestrator` 不捕获或转换 Provider 异常，Worker 仍按原异常类型决定是否重试。

#### 改变与未改变的行为

改变的是内部依赖方向：Pipeline 不再认识具体的 `AIReviewService`，Worker Factory 负责装配 Engine、Orchestrator、General Agent 和 Aggregator。

以下行为保持不变：

- 每次需要生成 Report 时仍只调用一次模型；
- Prompt v2、Provider、结构化输出和业务校验不变；
- Report/Finding 数据结构和数据库 Schema 不变；
- Report 已存在时仍跳过 Context 和模型调用；
- Publication、Head SHA 校验和 GitHub Review 发布不变；
- Eval Runner 仍可直接使用 `AIReviewService`，没有被迫经过生产 Orchestrator。

#### 自动化验证证据

新增测试覆盖：

- General Agent 把同一个 Context 委托给现有 AI 服务；
- Orchestrator 符合 `ReviewEngine` Protocol；
- Aggregator 收到 `general` 角色、原 Context 和原 Report；
- Passthrough Aggregator 拒绝零个或多个结果，避免静默丢失输出；
- 空 Agent 配置快速失败；
- `LLMTransientError` 原样传播，确保 Worker 重试策略不失效；
- Pipeline 完整流程仍只有一次 LLM Prompt，并继续通过恢复与发布回归测试。

本批验证结果：

```text
Targeted Agent/Pipeline tests: 10 passed
All tests: 222 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (90 source files)
git diff --check: passed
```

#### 真实集成证据

本批没有重新调用 DeepSeek，也没有向真实 GitHub PR 发布 Review。此前的真实链路证据仍然有效，但本批结论只建立在零行为变化的代码设计和自动化回归上。合并后如需生产级确认，应使用一个低风险测试 PR 验证最终装配路径。

#### 面试怎么讲

> 我先把生产 Pipeline 从具体 AI 服务解耦为 ReviewEngine，再用 GeneralReviewAgent 包装原有实现，并通过 Orchestrator 和单结果透传 Aggregator 保持完全相同的输出。这一批只改变依赖结构，模型仍调用一次，数据库和发布链路都不变。同时我专门验证了 Provider 异常不会在 Orchestrator 中被吞掉或改写，因此原来的 Celery 重试策略仍然成立。

#### 当时限制

- 现在仍是一个 General Agent，不是真正的多 Agent；
- 没有 Planner、Agent Registry 或专业 Prompt；
- Aggregator 只支持单结果透传，没有去重、冲突处理或标签校准；
- 没有 Agent 级持久化、Usage、Latency、预算或断点恢复；
- 尚无本批架构接缝的新增真实 DeepSeek/GitHub 联调证据。

---

### 3.20 专业 Agent、Registry 与确定性 Planner 第二批

#### 要解决的问题

第一批只建立了 General Agent 接缝，还不能回答以下问题：

- 不同专业 Reviewer 如何拥有独立且可追踪的 Prompt；
- Planner 如何在不额外调用模型的情况下选择 Reviewer；
- 如何先单独评测一个专业 Agent，而不立即改变生产 Worker；
- 如何记录专业 Agent 的角色、Prompt Version 和 Token Usage；
- 如何让历史 General baseline 在新增 Agent 元数据后继续可读。

本批仍不解决多结果合并。Validator、去重和冲突处理属于下一批，如果现在直接拼接多个 Agent 输出，会把未设计的聚合行为混入质量实验。

#### 专业 Agent 与 Prompt

`AgentRole` 现在包含：

```text
general
correctness
security
performance
maintainability
```

四个专业角色使用独立 Prompt Version：

| Agent | Prompt Version | 关注范围 |
|---|---|---|
| Correctness | `review-correctness-v1` | 行为契约、边界条件、控制流、错误处理、资源生命周期和可靠性 |
| Security | `review-security-v1` | 信任边界、注入、鉴权、认证、敏感数据和路径处理 |
| Performance | `review-performance-v1` | N+1、重复 I/O、异步阻塞、超线性算法和无界资源消耗 |
| Maintainability | `review-maintainability-v1` | 重复业务规则、多个事实源、不可达代码和客观漂移风险 |

当前没有单独的 Reliability Agent。第一版把 reliability 纳入 Correctness Agent，避免为了标签对称额外增加模型调用；后续真实评测如果显示职责过重，再决定是否拆分。

专业 Prompt 复用 General Prompt 的安全边界、证据策略和 Severity Rubric，只追加窄化职责，不复制 PR/Diff 序列化逻辑。Prompt Version 由实际 Prompt Builder 单一来源产生，Agent 元数据不能单独伪造另一版本。

#### Agent Result 与 Registry

`AgentReviewResult` 现在携带：

```text
role
report
usage
```

General 与 Specialist Agent 都通过 `AIReviewService.review_with_metadata()` 获取 Report 和真实 Provider Usage。生产 Orchestrator 目前仍只使用 Report，但 Eval 可以记录 Input/Output/Total Tokens。

`ReviewAgentRegistry` 负责：

- 按 `AgentRole` 注册和获取 Agent；
- 拒绝重复角色；
- 拒绝空 Registry；
- 对未注册角色快速失败；
- 使用同一 Provider 构建 General 和四个专业 Agent。

#### Deterministic Planner

Planner 不调用 LLM，也不读取期望 Finding。它只查看 PR title/body、文件路径和 Patch，通过固定信号选择角色：

- Correctness/可靠性是所有代码变更的基础 Reviewer；
- SQL、命令执行、鉴权、上传路径和凭据相关信号选择 Security；
- 数据访问、异步阻塞、重复全量集合操作和复杂度信号选择 Performance；
- 重复/规范化/单一事实源语义，以及同一可执行新增行至少出现三次，选择 Maintainability。

Planner 输出每个角色的可审计 `reasons`。选择某个 Agent 只表示“值得检查”，不代表 Planner 已判定存在缺陷。例如安全的参数化查询仍可能路由给 Security Agent，最终可以返回零 Finding。

#### Eval-first 接入

默认命令保持 General Agent，不改变历史使用方式：

```powershell
python -m evals.runner
```

只查看 Planner 选角，不创建 Provider、不访问网络：

```powershell
python -m evals.runner --plan-only --case-id security-sql-injection
```

使用 Mock 自测单个专业 Agent 的评测管线：

```powershell
python -m evals.runner --agent-role security --case-id security-sql-injection
```

真实专业 Agent 仍必须显式同时指定 Provider 和 `--live`：

```powershell
python -m evals.runner --provider deepseek --live `
  --agent-role security --case-id security-sql-injection
```

实现阶段没有立即执行最后一条真实命令。随后在 Planner 误选修复提交后，按角色逐个执行了 6 次低成本真实 DeepSeek partial trial；Mock 结果仍只证明 evaluator plumbing 正常，真实 partial trial 也不能替代完整模型 baseline。

`EvaluationRun` 和每个 `CaseEvaluation` 都新增 `agent_role`。历史 baseline 没有该字段时默认解释为 `general`，Comparator 描述中也会显示角色，避免把不同 Agent 的结果误认为同一 Prompt 实验。

#### 生产行为边界

生产 `pipeline_factory.py` 没有切换到 Registry 或 Planner，仍然装配：

```text
ReviewOrchestrator
    → one GeneralReviewAgent
    → one model call
    → PassthroughReviewAggregator
```

因此本批没有改变真实 GitHub Review 的内容、模型调用数、重试语义、数据库 Schema 或 Publication 行为。

#### 自动化验证证据

新增测试覆盖：

- Registry 包含 General 和四个专业角色；
- 重复、空和未知角色配置快速失败；
- 四个专业 Agent 使用正确的职责文本和独立 Prompt Version；
- Prompt Version 从真实 Builder 派生；
- Agent Result 保留 Usage；
- Planner 对 Seed/Holdout 的安全、性能、维护性案例选择预期角色；
- 安全 negative samples 仍进入 Security 检查，但不会被 Planner 标记为缺陷；
- 专业 Agent 的 Mock/Live 测试路径记录角色、Prompt 和 Usage；
- `--plan-only` 不创建 Provider 或读取 Git 状态；
- 历史 baseline 自动默认为 General Agent；
- 现有生产 Pipeline、恢复、Baseline、Comparator 和 Rescore 全部回归通过。

验证结果：

```text
Eval tests: 56 passed
All tests: 246 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (94 source files)
git diff --check: passed
```

#### 离线命令实测

对 `security-sql-injection` 执行 `--plan-only`，Planner 输出：

```text
correctness: baseline correctness and reliability review
security: database input boundary
```

随后使用 Mock 显式运行 Security Agent，结果记录：

```text
agent_role: security
prompt_version: review-security-v1
case_pass: true
usage: null
```

这是无网络的 evaluator self-test，不是模型质量证据。

#### 完整路由审计与误选修复

第二批提交后对完整 `seed-v1` 执行 `--plan-only`，首次结果中 `performance-quadratic-membership` 除了 Correctness 和 Performance，还被错误路由到 Maintainability。

根因不是模型，而是 Planner 把孤立的 `duplicate`、`duplicated` 和 `copied` 当作代码重复信号。该案例中的 `find_duplicates` 和 `duplicates` 表示“查找重复数据”，不是“存在重复代码”，因此产生语义碰撞。如果直接进入真实多 Agent 执行，这个误选会多产生一次无必要的模型调用和 Token 成本。

修复方式：

- 删除孤立的 `duplicate`、`duplicated` 和 `copied` 关键词；
- 只保留 `duplicate business rule`、`duplicated logic`、`same rule`、`copy-paste` 等明确短语；
- 继续保留“同一可执行新增行至少出现三次”的结构化重复信号；
- 新增针对 `performance-quadratic-membership` 的负向路由回归测试。

修复后重新执行完整 `--plan-only`：

- `performance-quadratic-membership` 只选择 Correctness 和 Performance；
- `maintainability-duplicated-rule` 仍选择 Correctness 和 Maintainability；
- 10 个 Seed 案例的候选 Agent 调用总数从 17 降为 16；
- Planner 单元测试 12 项通过。

这个过程说明 Planner 的评测不能只验证“应该选中的角色存在”，还要验证“不应该选中的角色不存在”，否则过度路由会在上线后直接放大成本。

#### 真实集成证据

Planner 修复提交 `24d1476` 后，使用 `deepseek-flash` 对四个正例和两个负例逐个运行专业 Agent。所有命令均显式包含 `--provider deepseek --live --agent-role ... --case-id ...`，没有发布 GitHub Review，也没有切换生产 Worker。

| 角色 | 案例 | Prompt | 结果 | Tokens（输入/输出/总计） | Latency |
|---|---|---|---|---:|---:|
| Correctness | `correctness-empty-average` | `review-correctness-v1` | 1/1 命中，0 FP，Category/Severity 正确 | 1058/380/1438 | 2534 ms |
| Security | `security-sql-injection` | `review-security-v1` | 1/1 命中，0 FP，Category/Severity 正确 | 1080/380/1460 | 2496 ms |
| Performance | `performance-n-plus-one` | `review-performance-v1` | 1/1 命中，0 FP，Category/Severity 正确 | 1100/398/1498 | 2460 ms |
| Maintainability | `maintainability-duplicated-rule` | `review-maintainability-v1` | 1/1 命中，0 FP，Category/Severity 正确 | 1126/384/1510 | 2247 ms |
| Correctness | `negative-safe-average` | `review-correctness-v1` | 0 Finding，负例通过 | 1062/144/1206 | 1292 ms |
| Security | `negative-parameterized-query` | `review-security-v1` | 0 Finding，负例通过 | 1148/279/1427 | 1836 ms |

六次真实调用的汇总结果：

- `successful_case`、Schema Validity 和 Case Pass 均为 6/6；
- 四个正例 Finding 命中 4/4，False Positive 为 0，Category/Severity Match 均为 4/4；
- 两个负例均输出 0 Finding，说明专业 Prompt 在这些代表案例上没有为了“完成职责”而强行报错；
- 总 Usage 为 6574 Input、1965 Output、8539 Total Tokens；总墙钟时间约 12.87 秒；
- 相同 6 个案例的 General v2 结果为 8205 Total Tokens、约 14.47 秒。专业 Agent 单角色运行多使用 334 Tokens，约增加 4.1%；一次性网络调用的延迟波动很大，不能据此声称专业 Agent 更快；
- Security 正例正确识别 SQL 注入，但对 stacked/union exploitation 的措辞比 Patch 能直接证明的事实更强，后续 Validator 应约束“已证实影响”和“潜在影响”的表达边界；
- Maintainability Agent 明确指出“当前没有运行时缺陷”，只报告重复业务规则带来的未来漂移风险，说明窄职责 Prompt 能区分当前故障与客观维护风险。

这些运行使用 `seed-v1`、固定 Dataset SHA 和同一 Git commit，但工作区因与试跑无关的个人文档改动显示为 dirty，并且每次只执行一个案例。因此它们被记录为真实 partial trial，而不是可发布的 Model Baseline。现有 Seed 又已经参与开发，结果只能证明职责遵循和链路可用，不能证明对未见真实 PR 的泛化提升。

#### 面试怎么讲

> 第二批我没有直接让四个 Agent 一起跑，而是先把角色、独立 Prompt、Registry 和确定性 Planner 做成可单测组件。Planner 只根据 PR 和 Patch 的可审计信号选角，不再用一个 LLM 决定要调用哪些 LLM。专业 Agent 先通过 Eval Runner 单独运行，结果记录 agent role、prompt version 和 token usage；生产仍保持一个 General Agent。这样下一步做聚合时，可以区分路由、单 Agent 能力和 Aggregator 各自带来的变化。

> 随后我用四个正例和两个负例做了 6 次真实 DeepSeek 单角色试跑，6/6 Case Pass，正例 4/4 命中且没有误报，负例均保持 0 Finding。与相同案例的 General v2 相比，总 Token 增加约 4.1%，但简单案例上的质量指标相同，所以我没有据此宣称专业 Agent 已提升质量，也没有把单次网络延迟当作性能结论。这批证据的价值是验证角色职责、Prompt 隔离、Usage 记录和负例克制性，为下一步 Validator 与 Aggregator 提供可审计起点。

#### 当前限制

- 尚未同时执行多个专业 Agent；
- 尚无 Validator、Finding 指纹、去重或冲突处理；
- Planner 是关键词和重复行启发式，不是 AST/语义分析；
- Patch 中的恶意文本可能触发更多角色，生产启用前必须配合 Agent 数量和 Token Budget；
- 专业 Agent 只有 6 个已见 Seed 案例的真实 partial trial，尚无完整 baseline、未见混合缺陷案例或稳定重复运行；
- 生产仍是 General Agent，不能声称系统已经完成多 Agent 评审。

---

### 3.21 Finding Validator、Identity 与确定性 Aggregator 第一批

#### 要解决的问题

专业 Agent 已能独立运行，但原有 `PassthroughReviewAggregator` 明确只接受一个结果。如果直接把多个 Agent 的 Report 拼在一起，会产生三个风险：

- General 与 Specialist 可能重复报告同一个根因；
- 同一个问题可能使用不同 Category 或 Severity；
- 模型输出虽然经过单 Agent 服务验证，进入新的聚合信任边界时仍缺少防御性检查和决策审计。

本批先实现纯确定性的验证、Finding Identity 和聚合能力，不启用生产多 Agent，不增加模型调用，也不做数据库 Migration。

#### 聚合边界验证

新增 `ReviewFindingValidator`，对每个 `AgentReviewResult` 再次验证：

- Finding 文件必须属于当前 `ReviewContext.reviewable_files`；
- Title 长度、Explanation、Suggestion 和 Confidence 必须合法；
- 同一个 Agent 不能返回完全重复的 Finding；
- 验证后的 Finding 保留来源 `AgentRole`。

非法结果会抛出 `ReviewFindingValidationError`，不会被静默丢弃后继续发布。这与现有单 Agent 的 fail-closed 行为保持一致。

#### Finding Identity

生产侧新增独立的 Finding Identity，未反向依赖 `evals.matcher`。第一版规则为：

1. 必须引用同一个文件；
2. 精确身份使用规范化后的 File、Title 和 Explanation 生成 SHA-256 指纹；
3. 近似身份使用 Title 与组合文本的 token containment；
4. Category、Severity、Confidence 和 Suggestion 不参与“是否同一问题”的判断。

不使用 Category 和 Severity 做匹配，是为了让标签冲突在聚合后仍然可见。匹配阈值采取保守策略，并使用 complete-link 分组：新 Finding 必须与组内每一个成员都匹配，避免相似关系的传递链把两个不同问题错误合并。

当前 `ReviewFinding` 没有行号，因此第一版不计算范围重叠，也不从自然语言猜测行号。

#### 确定性聚合与冲突规则

新增 `DeterministicReviewAggregator`，同时提供：

```text
aggregate(...) -> ReviewReport
aggregate_with_metadata(...) -> ReviewAggregationResult
```

第二个接口额外返回：

- `decisions`：每个 Finding 是 `kept` 还是 `merged`，以及原因；
- `conflicts`：Category 或 Severity 的观察值、最终值和选择原因；
- `source_roles`：参与聚合的 Agent 角色。

同一问题的规范 Finding 按以下顺序选择：

1. 专业角色与 Finding Category 是否一致；
2. Confidence 是否更高；
3. Explanation 与 Suggestion 是否提供更多内容；
4. 固定 Role 顺序和规范化文本作为最终 tie-break。

Severity 不采用简单的最大值，否则一个夸大的 General Finding 就可能把风险等级抬高。最终 Category、Severity、Confidence 和文本都来自被选中的规范 Finding；所有冲突进入审计元数据。最终 Finding 使用固定顺序排序，Summary 由代码生成，Risk Level 通过 `derive_review_risk()` 重新计算，因此 Agent 输入顺序不会改变最终 Report。

#### 兼容性与生产边界

`DeterministicReviewAggregator` 仍实现现有 `ReviewAggregator` Protocol，输出仍是已有 `ReviewReport`。数据库 Report、Finding、Publication 和 Markdown Formatter 都不需要修改。

生产 `pipeline_factory.py` 本批没有变化，仍是：

```text
one GeneralReviewAgent
    -> PassthroughReviewAggregator
    -> one ReviewReport
```

因此本批没有改变生产模型调用数、GitHub Review 内容、重试策略或持久化行为。

#### 自动化验证证据

新增测试覆盖：

- 合法 Finding 保留来源角色；
- 未知文件、空文本、非法 Confidence 和单 Agent 重复 Finding 被拒绝；
- 精确身份忽略 Category、Severity 与 Suggestion；
- 同文件的重排文本可以匹配；
- 同文件的两个不同 SQL 注入入口不会误合并；
- 跨文件 Finding 不会合并；
- General 与 Security 的重复 Finding 合并后优先采用 Security 的职责一致标签；
- Category 与 Severity 冲突被记录；
- 多个空 Report 可以生成无 Finding 的最终 Report；
- 重复 Agent Role 和空结果被拒绝；
- 调换 Agent 结果顺序后完整聚合结果保持一致；
- 混合模型名称使用明确的 `multi-agent` 标识。

验证结果：

```text
Targeted tests: 24 passed
All tests: 265 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (96 source files)
```

#### 真实集成证据

本批没有调用 DeepSeek、没有执行真实 GitHub Webhook，也没有发布 GitHub Review。现有 6 次专业 Agent partial trial 是上一批的真实证据，不能被当作本批 Aggregator 的真实集成证据。

#### 面试怎么讲

> 多 Agent 最容易出现的问题不是“能不能同时调用多个模型”，而是同一根因被重复发布以及标签冲突。我先在生产链路之外实现了确定性 Validator 和 Aggregator：匹配身份不使用 Category/Severity，保证冲突仍然可审计；规范 Finding 优先采用职责匹配的专业 Agent，而不是盲目选择最高 Severity；相同输入不受 Agent 返回顺序影响。这个阶段保持数据库和生产单 Agent 行为不变，用单元测试先固定合并语义。

#### 当前限制

- 文本身份是保守的词法启发式，还不是 AST、Embedding 或行范围匹配；
- 当前审计元数据尚未持久化；
- Aggregator 已接入 Eval 多 Agent Runner，但尚无真实模型多 Agent 结果；
- 尚未测试多个 Agent 对复杂、混合缺陷 PR 的真实输出；
- 尚未实现部分 Agent 失败、并发、预算和 Agent 级恢复；
- 生产仍使用 `PassthroughReviewAggregator`。

---

### 3.22 Eval-only Planned Multi-Agent Runner

#### 要解决的问题

上一批已经能合并多个 Agent Result，但 Planner、Registry、专业 Agent 和 Aggregator 仍是分散组件。系统还不能在同一个评测案例里回答：

- Planner 实际选择了哪些角色；
- 每个 Agent 的 Prompt、Latency、Finding 和 Usage 是什么；
- 聚合前后减少了多少重复 Finding；
- 哪个 Agent 的结果被保留、合并，或者提供了独有贡献；
- 某个 Agent 失败时，已完成的执行是否仍可观测。

本批把这些组件串成完整的 Eval-only 多 Agent 链路，同时保持生产 Worker 不变。

#### 计划驱动 Orchestrator

新增 `PlannedReviewOrchestrator`：

```text
ReviewContext
    -> DeterministicReviewPlanner
    -> ReviewAgentRegistry
    -> selected Specialist Agents (sequential)
    -> DeterministicReviewAggregator
    -> PlannedReviewResult
```

`PlannedReviewResult` 保留 Plan、每个 Agent Execution 和 Aggregation 审计结果。每个 Execution 记录 Role、真实 Prompt Version、Agent Result 和独立 Latency。

第一版故意顺序执行，而不是直接并行：先固定角色选择、错误边界、Usage 累加和聚合指标，后续才能用相同口径判断并行是否真的降低墙钟时间。

Agent 调用失败会抛出 `PlannedAgentExecutionError`，其中包含失败 Role、Prompt Version、失败 Latency 和已经完成的 Agent Execution。聚合失败则使用 `PlannedReviewAggregationError` 保留全部已完成执行。Eval 可以记录失败点，但不会把不完整结果伪装成成功 Report。

#### Eval 模式与安全门禁

新增默认无网络命令：

```powershell
python -m evals.runner --multi-agent --case-id security-sql-injection
```

它使用 Mock，仅用于验证 Planner、Agent、Aggregator 和 Metrics plumbing，不是模型质量证据。

真实模型仍需要三个显式条件：

```powershell
python -m evals.runner --provider deepseek --live --multi-agent `
  --case-id security-sql-injection
```

安全规则：

- `--multi-agent` 与 `--agent-role`、`--plan-only` 互斥；
- Mock 模式不加载 Settings、不创建真实 Provider、不读取真实 API Key；
- 多 Agent 实验暂时禁止 `--save-baseline`；
- 未知 Case 仍在 Git 状态和 Provider 创建之前失败；
- Live 多 Agent 即使执行完整数据集也标记 `is_model_baseline=false`。

#### 新增可观测指标

评测结果新增向后兼容的 `execution_mode`。历史 JSON 缺失该字段时默认解释为 `single_agent`；多 Agent 使用 `planned_multi_agent`，顶层和 Case 的 `agent_role` 为 `null`，避免伪装成 General Agent。

每个多 Agent Case 记录：

- `planned_agents` 与 Planner reasons；
- `agent_executions`：Role、Prompt、Outcome、Latency、Finding Count、Usage、Error Type；
- `pre_aggregation_finding_count` 与 `post_aggregation_finding_count`；
- `merged_finding_count`、`aggregation_conflict_count` 和 `duplicate_rate`；
- 每个角色的 raw、kept、merged 和 unique contribution。

Run 级 Metrics 对 Agent Calls、Finding、Conflict、Contribution 和 Usage 做汇总。只有所有 Agent 都返回 Usage 时才计算 Case 总 Token；Mock 或不完整 Usage 保持 `null`，不会伪造成本。

#### Mock 完整链路实测

对 `security-sql-injection` 执行无网络多 Agent 命令，得到：

```text
planned roles: correctness, security
total agent calls: 2
pre-aggregation findings: 2
post-aggregation findings: 1
merged findings: 1
duplicate rate: 0.5
case pass: true
usage: null
```

Mock 为两个 Agent 生成同一个期望 Finding。Aggregator 选择职责匹配的 Security 结果，合并 Correctness 的重复结果。该结果证明完整评测 plumbing 和贡献统计正确，不证明真实模型也会产生相同质量。

完整 `seed-v1` Mock 自测中，Planner 产生 16 次 Agent 调用，10/10 Case Pass，Aggregator 合并 5 个重复 Finding，False Positive 为 0。

#### 自动化验证证据

新增测试覆盖：

- Planner 选择的角色按固定顺序从 Registry 执行；
- 每个 Agent Latency 和完成结果被保留；
- Agent 失败保留已完成执行和失败 Role；
- Aggregation 失败保留全部 Agent Execution；
- Mock 多 Agent 链路执行 Correctness + Security 并完成去重；
- Live-shaped Provider 的多 Agent Token Usage 正确相加；
- 单 Agent 失败被分类为对应 Transport/Provider/Schema Outcome；
- Run 级 Agent Calls、Duplicate Rate 和 Contribution 正确汇总；
- CLI 互斥门禁与 baseline 禁止规则；
- 默认 Mock 不创建真实 Provider 或读取 Git 状态；
- 历史单 Agent Baseline、Comparator 和 Rescore 保持兼容。

验证结果：

```text
Targeted tests: 43 passed
All tests: 274 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (98 source files)
Mock multi-agent CLI: passed
```

#### 真实集成证据

本批没有调用真实 DeepSeek、没有发布 GitHub Review，也没有生成多 Agent baseline。当前只有 Mock 完整链路和此前单专业 Agent 的真实 partial trial；不能据此声称多 Agent 提升了 Recall、Precision 或延迟。

#### 面试怎么讲

> 我没有把多 Agent 直接切进生产，而是先做 Eval-only 编排器。Planner 选择角色后顺序执行专业 Agent，再通过确定性 Aggregator 去重；评测结果同时保留每个 Agent 的 Prompt、Latency、Token、原始 Finding 和贡献。Mock 的 SQL 注入案例会调用 Correctness 与 Security 两次，聚合前两个 Finding、聚合后一个，重复率 50%。Mock 只验证链路，不代表模型质量；真实多 Agent baseline 在 Schema 和恢复策略稳定前也被代码明确禁止保存。

#### 当前限制

- 多 Agent 仍是顺序执行，尚未实现并发；
- 任一 Agent 失败会让 Case 失败，尚未定义生产 `PARTIAL` 策略；
- Agent Execution 与 Aggregation 审计尚未持久化；
- 现有 Seed 是开发集，Mock 输出由 Expected Findings 生成；
- 本阶段完成时尚无真实 DeepSeek 多 Agent 结果；后续真实试跑与修复见 3.23；
- 生产仍是单 General Agent 和 `PassthroughReviewAggregator`。

---

### 3.23 真实多 Agent 去重缺陷与 Finding Identity v2

#### 真实试跑发现的问题

在提交 `e6f43d0` 后，对 `security-sql-injection` 执行了第一次真实 DeepSeek 多 Agent partial trial。Planner 正确选择 Correctness 和 Security，两个 Agent 都成功返回一个合法 Finding：

```text
Correctness: 1906 Tokens, 4456 ms, 1 Finding
Security:    1514 Tokens, 2836 ms, 1 Finding
Total:       3420 Tokens, 7293 ms
```

两个 Finding 引用同一个文件、同一段 SQL f-string、同一个直接插值根因，也都建议参数化查询。Correctness 额外说明合法邮箱包含单引号时查询也会损坏，但这不是另一个独立根因。

实际聚合结果却是：

```text
pre-aggregation findings: 2
post-aggregation findings: 2
merged findings: 0
duplicate rate: 0.0
false positives: 1
case pass: false
```

因此这里的 False Positive 主要是 Aggregator 的漏合并，不是模型生成了无关问题。这个结果也说明 Mock 使用相同 Expected Finding 文本，只验证了完全相同或高度相似的输出，不能覆盖真实模型的同义改写。

#### 根因

Finding Identity v1 要求：

- Title token containment 至少 `0.75`；
- Title + Explanation token containment 至少 `0.65`。

真实标题分别使用 `broken lookups / interpolation / email` 和 `interpolated query / find_user`。虽然语义相同，标题词面重叠不足，旧规则在检查共同代码证据前就判定为不同问题。

#### Finding Identity v2

新增版本常量：

```text
finding_identity_version: finding-text-v2
```

v2 保留原有高阈值词法路径，同时增加一条保守的真实模型同义改写路径：

1. 文件路径必须相同；
2. 两个标题至少共享两个非通用信号词；
3. 两个标题至少共享一个连续的非通用 bigram，例如 `sql injection`；
4. 两个 Explanation 必须引用同一个长度足够的 Markdown code span。

只有四项同时满足才合并。Category、Severity 和 Confidence 仍不参与身份判断，避免隐藏标签冲突。Suggestion 中常见的“使用参数化查询”也不作为共同代码证据，避免两个不同 SQL 入口因为修复建议相似而被误合并。

新增负向回归确保：即使两个 Finding 引用同一段 SQL，如果一个报告 SQL 注入、另一个报告缺少索引，也不能合并。原有“email 注入”和“sort column 注入”不同入口测试继续保留。

Identity Version 同时写入 `ReviewAggregationResult` 和多 Agent `EvaluationRun`。历史单 Agent baseline 缺少该字段时默认 `null`，不需要重写历史文件。

#### 自动化验证证据

新增或加强的测试覆盖：

- 第一次真实试跑的两个标题和共同 SQL code span 可以合并；
- Aggregator 最终保留职责匹配且 Confidence 更高的 Security Finding；
- 同一代码片段的 SQL 注入与缺少索引不能合并；
- 不同 SQL 注入入口不能合并；
- 多 Agent Run 输出 `finding-text-v2`；
- 历史 baseline 的 Identity Version 保持 `null`。

验证结果：

```text
Targeted tests: 24 passed
All tests: 277 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (98 source files)
Mock multi-agent CLI: passed
```

#### 真实集成证据

修复前的真实 DeepSeek partial trial 绑定 Git commit `e6f43d0` 和固定 Seed SHA，两个 Agent 与 Usage 通道均成功。工作区因个人文档显示 dirty，因此不是 baseline。该次运行证明真实多 Agent 执行链路可用，同时证明 Identity v1 会漏合并这组同根因输出。

提交 `dd95c4b` 后，使用相同 Dataset、Case、Provider 和显式 `--multi-agent` 参数完成 Identity v2 真实重验：

```text
finding_identity_version: finding-text-v2
planned roles: correctness, security
successful agent calls: 2/2
pre-aggregation findings: 2
post-aggregation findings: 1
merged findings: 1
duplicate rate: 0.5
false positives: 0
case pass: true
```

两个 Agent 分别使用 2276 和 1409 Tokens，总计 3685 Tokens；总墙钟时间约 8.55 秒。Aggregator 保留 Security Finding，并把 Correctness Finding 记为 merged；两个角色的 `unique_finding_count` 都是 0，说明这个案例中第二个调用提供了重复确认，但没有新增最终问题。

修复前后对比：

| 指标 | Identity v1 | Identity v2 |
|---|---:|---:|
| 聚合前 Findings | 2 | 2 |
| 聚合后 Findings | 2 | 1 |
| Merged | 0 | 1 |
| Duplicate Rate | 0.0 | 0.5 |
| False Positive | 1 | 0 |
| Case Pass | false | true |
| Total Tokens | 3420 | 3685 |
| Latency | 7293 ms | 8547 ms |

两次 Input Tokens 都是 2151，Token 差异来自模型输出长度；延迟也包含网络和生成波动。Identity 是本地确定性逻辑，不能把第二次 Token 或延迟变化归因于 Identity v2。

本次 `aggregation_conflict_count=1`，说明两个原始 Finding 的 Category 或 Severity 至少有一项不同。但当前 Case JSON 只暴露冲突数量，没有输出冲突字段、观察值和选择原因；每个 Agent Execution 也只保留 Finding Count，没有序列化原始 Finding。因此不能从这份结果猜测具体冲突标签。这是下一批需要补齐的可观测性缺口。

重验绑定 commit `dd95c4b`，仍因个人文档显示 `git_dirty=true`，并且只执行一个已见 Seed 案例，所以是 partial trial，不是 baseline，也不能外推为多 Agent 整体质量或稳定性能结论。

#### 面试怎么讲

> 第一次真实多 Agent 试跑里，Correctness 和 Security 都正确发现了同一个 SQL 插值问题，但模型使用不同措辞，导致词法去重漏合并，评测器把第二条算成误报。我没有去调低一个全局相似度阈值，而是增加了保守的证据路径：同文件、共同根因 bigram、至少两个信号词，并且 Explanation 引用同一代码片段才合并。同时加了“同一 SQL 代码上的注入与缺少索引不能合并”的负例。这样修复的是同根因识别，同时控制过度合并风险。

> 修复提交后我用相同真实案例重验，两个 Agent 仍各输出一条同根因 Finding，v2 成功把结果从两条合并成一条，False Positive 从 1 降到 0，Case Pass 恢复。这个 A/B 同时暴露了下一层问题：评测 JSON 只有 conflict count，没有冲突明细和原始 Agent Finding，所以我不会猜冲突标签，而是把审计可观测性作为下一批工作。

#### 当前限制

- v2 依赖模型在 Explanation 中使用 Markdown code span；没有共同代码引用的深度同义改写仍可能漏配；
- bigram 与信号词仍是词法启发式，不是 AST 或语义 Embedding；
- 当前试跑是已见 Seed 单案例，不能证明泛化质量；
- Eval 只输出 Aggregation Conflict 数量，尚未输出冲突字段、观察值与选择原因；
- Agent Execution 尚未序列化各 Agent 原始 Finding，无法从运行 JSON 完整重放聚合判断；
- 多 Agent 仍为顺序执行，两次 Token 和延迟只是单点观测，不是稳定成本或性能结论。

---

## 4. 当前能力快照

截至 2026-09-27，系统已经具备：

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
- Eval-only Planner 多 Agent 完整编排；
- Agent 调用数、聚合前后 Finding、Duplicate Rate、Conflict 和 Contribution 指标；
- 具备证据约束、测试降噪和明确 Severity Rubric 的版本化 Prompt v2；
- 在相同 `seed-v1` 上保存并比较 `review-v1` 与 `review-v2` 的真实 DeepSeek baseline；
- 用 SHA-256、ID/Patch 隔离和覆盖测试冻结的 `holdout-v1` 泛化验证集；
- 保存首次 Holdout 原始 baseline，并区分模型错误、Matcher 漏配和 Fixture 缺陷的人工审计；
- 版本化的 `text-v1` / `text-v2` Matcher，以及绑定原 Baseline SHA-256 的纯离线 Rescore artifact；
- 在不调用模型的条件下重新计算 Finding 对齐、Recall、误报、Category、Severity 和 Case Pass；
- 保留 `holdout-v1` 原始证据，并通过严格 lineage 测试冻结只修复已知 Fixture 的 `holdout-v2`；
- 使用 `--case-id` 做精确、低成本的 Mock 或 Live partial evaluation，并在 Provider 初始化前校验选择。

### 4.4 Agent 架构基础

- Pipeline 通过 `ReviewEngine` Protocol 依赖评审能力；
- `ReviewAgent`、`AgentRole` 和 `AgentReviewResult` 提供角色化执行边界；
- General Agent 复用现有 `AIReviewService`；
- Orchestrator 负责编排，Passthrough Aggregator 保持单 Agent 输出不变；
- Registry 提供 General、Correctness/Reliability、Security、Performance 和 Maintainability Agent；
- 四个专业 Agent 使用独立版本 Prompt，并通过 Eval Runner 单独评测；
- Deterministic Planner 根据 PR、路径和 Patch 信号输出角色及原因；
- `--plan-only` 默认离线且不创建模型 Provider；
- 生产当前仍为一次 General 模型调用，没有多结果聚合、动态生产路由或并发执行。

### 4.5 安全边界

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

可以按五层展开：

1. **可运行**：FastAPI、Celery、Redis、MySQL、GitHub App、DeepSeek 组成真实闭环。
2. **可恢复**：Webhook、Task、Report 和 Publication 都有幂等策略；可恢复错误重试，永久错误快速失败。
3. **可验证**：使用 Fault Stub 可控注入 Timeout、429、500、503、401 和非法输出，用日志和数据库共同验收。
4. **可评估**：区分开发集和冻结 Holdout，计算 Recall、Precision、F1、误报、类别、严重程度、Schema 和 Case Pass，避免只凭一次 Demo 或调优过的数据判断模型质量。
5. **可演进**：Pipeline 依赖 `ReviewEngine`，当前用单 General Agent 保持兼容，后续专业 Agent 可以先接 Eval 验证，再逐步进入生产链路。

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

### 6.7 为什么先加 Agent 接缝而不是直接并行四个 Agent

直接增加模型调用会同时改变质量、成本、延迟和恢复语义，出现问题时难以判断是架构迁移还是专业 Prompt 导致。第一批只改变依赖结构，并通过 General Agent 复用原逻辑，使一次改动只验证一个假设。等兼容边界稳定后，再分别引入 Planner、专业 Agent、聚合规则和 Agent 级恢复。

### 6.8 为什么 Planner 先用确定性规则

如果 Planner 本身也调用 LLM，每次评审会多一次费用和失败点，而且路由变化难以解释。确定性 Planner 可以输出命中的具体原因，单元测试也能冻结路由行为。它的目标不是判断代码一定有缺陷，而是用低成本信号缩小专业 Reviewer 的候选范围；真实质量仍由后续模型评测决定。

### 6.9 为什么聚合结果还需要审计快照

#### 要解决的问题

仅记录 `2 -> 1` 和一个 Conflict 数量，只能证明发生了合并，不能回答“两个 Agent 原来分别说了什么、哪条被选中、冲突发生在哪个标签、为什么选择这个值”。真实 SQL 注入重验已经出现过 `aggregation_conflict_count=1`，旧 JSON 无法进一步解释。

#### 设计选择与行为变化

- 每次 Agent 成功执行都保留结构化原始 Findings，并继续保留 `finding_count` 便于聚合统计；
- 每个聚合决策记录 `kept/merged`、来源 Role、文件、标题、规范 SHA-256 指纹和选择原因；
- Category/Severity 冲突记录字段名、全部观察值、最终选择值、规范指纹和选择原因；
- 最终 `actual_findings` 仍只保存聚合后的结果，Matcher 和质量指标仍对最终结果评分；
- 新字段都提供空元组默认值，因此历史单 Agent baseline 不需要重写；
- 改动只位于 Eval 输出层，生产 Worker 仍使用单 General Agent 与透传 Aggregator。

#### 自动化验证证据

Mock 多 Agent 测试验证了两个 Agent 的原始 Findings、同一规范指纹下的 `kept/merged` 决策和贡献统计。专门的冲突用例让 Correctness 与 Security 对同一 Finding 返回不同 Category/Severity，验证最终选择 Security/High，同时完整保留 `correctness/security` 与 `medium/high` 的观察值。历史 baseline 兼容测试验证新增字段缺失时加载为空。

#### 真实集成证据

提交 `5d2e66d` 后，使用真实 DeepSeek 对 `security-sql-injection` 做了一次显式多 Agent partial trial。Correctness 与 Security Agent 均成功，各返回一个 Finding；新 JSON 完整保留两份原始输出，并记录 Security Finding 为 `kept`、Correctness Finding 为 `merged`，两条决策指向同一个规范指纹。聚合结果为 `2 -> 1`，Case Pass=true、False Positive=0，总计 3637 Tokens、约 7.83 秒。

本次两个 Agent 都给出 `security/high`，所以 `aggregation_conflict_count=0`，这是正确的无冲突结果。上一次运行中的一个 Conflict 没有在本次随机模型输出中复现，因此仍不能倒推上次冲突的具体字段。此次运行没有访问数据库或 GitHub，也没有发布 Review。

#### 面试怎么讲

> 多 Agent 聚合不能只输出一个最终答案，否则出现误合并或标签争议时无法定位责任。我把每个原始 Finding、规范指纹、保留/合并决策和标签冲突都建模成结构化评测快照，使最终报告可以追溯到具体 Agent，同时让质量评分仍只针对去重后的发布候选结果。

#### 准确边界与尚未完成

这些快照当前只存在于 Eval 运行结果，没有写入业务数据库，也不等于生产级 tracing。多 Agent baseline 保存仍被禁用；尚未实现 Agent Execution 持久化、部分失败恢复、并行执行和预算控制。

### 6.10 为什么增加独立的聚合挑战集

#### 要解决的问题

原有 Seed 和 Holdout 主要是单缺陷案例，默认 Mock 又会让所有被选 Agent 返回相同结果。它可以验证管线连通，却不能充分证明聚合器能区分“同一根因的不同表述”和“相邻代码上的不同根因”，也无法稳定制造标签冲突与独有贡献。

#### 设计选择与行为变化

- 新增四案例 `aggregation-v1` 开发集，不修改或重写现有 Seed、Holdout 与历史 baseline；
- 覆盖 SQL 注入与 N+1 重叠、路径穿越与资源未关闭相邻、跨文件性能与可维护性缺陷，以及多 Agent 负例；
- 数据集继续使用普通 `review-eval-v1` Schema，因此既能离线运行，也能以后显式选择单案例做 live trial；
- 测试专用 Role-aware Provider 按 Prompt Version 返回预设输出，精确制造部分重叠、Category/Severity 冲突、空 Agent 输出和独有 Finding；
- 正式 `MockLLMProvider`、真实 Provider、Aggregator 和生产 Pipeline 均未修改。

#### 自动化验证证据

默认离线多 Agent 自测执行 4 个 Case、10 次 Agent 调用，将 16 条确定性 Mock Findings 聚合为 6 条期望 Findings，4/4 Case Pass、0 False Positive、Usage 保持 `null`。角色脚本测试进一步验证：重复 SQL 注入与独有 N+1 形成 `3 -> 2`；Category/Severity 双冲突选择 Security/High；共享同一 `open(...)` 代码证据的路径穿越与资源泄漏保持 `2 -> 2`；跨文件两个专业 Agent 各贡献一条独有 Finding；负例保持 `0 -> 0`。三 Agent、两个根因的全部输入排列得到相同聚合结果。

#### 真实集成证据

本批没有调用 DeepSeek、数据库或 GitHub，没有产生新的模型质量、成本或延迟结论。Mock 与脚本 Provider 结果只证明评测器和聚合器的确定性行为。

#### 面试怎么讲

> 我没有只用单缺陷样例验证多 Agent。为了防止去重规则过度合并，我建立了独立聚合挑战集，把同一行代码上的 SQL 注入与 N+1、同一个 open 表达式上的路径穿越与资源泄漏放在一起，并用角色脚本稳定制造标签冲突。这样可以分别验证重复合并、不同根因保留、冲突决策和 Agent 独有贡献。

#### 准确边界与尚未完成

`aggregation-v1` 是已知标签的开发集，不是新的盲测 Holdout。当前贡献统计表示聚合层面的独有 Finding，不等于经过人工确认的真实有效贡献。相邻路径/资源案例已完成一次真实 DeepSeek partial trial；后续质量比较仍需要新的未见案例。

### 6.11 Finding Identity v3：Explanation 根因证据

#### 要解决的问题

`aggregation-adjacent-path-and-resource` 的第一次真实 DeepSeek 试跑中，Correctness 同时发现路径穿越和文件句柄未关闭，Security 也发现路径穿越。两个路径穿越 Finding 都引用 ``UPLOAD_ROOT / filename``，标签也都是 `security/high`，但标题分别使用“escaping upload root”和“path traversal / arbitrary file read”，没有满足 Identity v2 的两个共同标题信号词和共同 bigram，导致 `3 -> 3`、False Positive=1、Case Pass=false。

#### 设计选择与行为变化

- Identity 版本提升为 `finding-text-v3`，保留精确指纹、高阈值 containment 和 v2 的标题加代码证据路径；
- 新增保守 fallback：必须同文件、Explanation 中存在完全相同的 inline code span；
- 去除 inline code 后，两段 Explanation 还必须至少共享四个非通用信号词和一个连续信号 bigram；
- Category、Severity、Confidence 和 Suggestion 仍不参与身份判断，标签冲突仍可被独立审计；
- 没有放宽为模糊代码包含，也没有使用向量或 LLM 判断，保持确定性和低成本。

#### 自动化验证证据

真实试跑的两个路径穿越表述已固化为回归，能够合并；同一案例中的文件句柄未关闭仍保持独立，测试结果从 `3 -> 3` 修复为 `3 -> 2`、Merged=1、False Positive=0、Case Pass=true。SQL 注入与缺少索引、路径穿越与资源泄漏两个“相同代码证据但不同根因”反例继续不合并。全部聚合挑战和输入顺序测试保持通过。

#### 真实集成证据

修复前的真实运行绑定 commit `2991ae8`：Recall=1.0、3 条聚合结果、1 条重复误报、Case Pass=false，总计 4643 Tokens、约 13.07 秒。

Identity v3 真实重验绑定 commit `06cedad`，Dataset SHA 保持不变。Correctness 本次只返回资源泄漏，Security 只返回路径穿越，因此原始结果已经是 `2 -> 2`，两条均为独有贡献；最终 Recall=1.0、False Positive=0、Category/Severity Match=1.0、Case Pass=true，总计 4044 Tokens、约 9.43 秒。真实 Provider 链路和“不同根因不误合并”通过，但因为本次没有重复路径 Finding，v3 新增的 Explanation 合并 fallback 没有在这次网络调用中触发；该分支仍由上一次真实输出的确定性自动化回放验证。

#### 面试怎么讲

> 多 Agent 的难点不是让多个模型都输出，而是判断不同措辞是否属于同一根因。标题规则在 SQL 注入上有效，但真实路径穿越输出使用了完全不同的标题。我没有直接降低文本阈值，而是要求相同代码证据，并在移除代码后继续验证 Explanation 的根因词和连续词组，同时用相同代码上的资源泄漏反例防止过度合并。

#### 准确边界与尚未完成

规则仍是词法启发式，不理解任意同义词，也可能遗漏没有共同 inline code span 的重复问题。两次 live 输出不同，因此不能把 Token、延迟或 Case Pass 的变化全部归因于 Identity v3；也不需要为了等待随机重复输出而继续付费重跑。更长期可以引入结构化 `evidence`、行号或规范化 root-cause key，减少对自然语言相似度的依赖。

### 6.12 可复现的多 Agent 架构实验工件与离线对照

#### 要解决的问题

此前 Eval Runner 只把 partial trial 打印到终端。完整单 Agent baseline 可以保存，但多 Agent baseline 被安全门禁禁止，导致 General、单专业 Agent 和 Planned Multi-Agent 的同条件结果需要人工复制，容易丢失元数据，也无法由程序阻止跨 Dataset、跨模型或跨提交误比较。已有 `evals.compare` 只支持两份完整单 Agent baseline，不能表达当前 eval-only 多 Agent 实验。

#### 设计选择与行为变化

- 新增版本化 `EvaluationArtifact`，用显式 `--output` 保存任意 Eval 结果，但不改变 `is_model_baseline`，因此 partial trial 和多 Agent trial 不会被伪装成 baseline；
- 工件在创建真实 Provider 前预检目标是否存在，并在最终写入时继续使用 exclusive-create 防止竞争覆盖；推荐输出到已被 Git 忽略的 `evals/results/`；
- `--output` 与 `--save-baseline`、`--plan-only` 互斥，保持“实验工件”“正式 baseline”“纯规划输出”三种语义分离；
- 保留原有两 baseline 对比接口，同时增加 General、显式单专业 Agent、Planned Multi-Agent 三工件离线比较；
- 比较前强制校验 Provider、Model、Dataset Version/SHA、Matcher、Git Commit、dirty 标志和 Case IDs 一致，并校验三个工件的执行模式与角色；
- 输出三组 pairwise 质量、错误、Token 和延迟指标，以及多 Agent 相对 General 的命中增量、误报增量、每额外 1000 Token 的命中增量、Agent 调用数和聚合后独有贡献数；逐案例给出 `improved/regressed/mixed/unchanged` 和具体变化原因。

#### 自动化验证证据

自动化测试覆盖工件读写往返、非法 JSON、拒绝覆盖、真实 Provider 创建前的文件冲突失败、CLI 保存、模式互斥、三架构对比、Mock/错误角色拒绝、Git Commit/Case ID 不一致拒绝，以及纯离线 CLI 输出。旧 baseline comparator 的跨提交 Prompt 对比测试保持通过，证明原有能力没有被新实验门禁收窄。

#### 真实集成证据

本批没有调用 DeepSeek、数据库或 GitHub，也没有产生新的模型质量、Token 或延迟结论。当前只完成实验采集和比较工具；真实三架构结论必须等新未见案例冻结、代码提交后，再以同一提交和同一 Dataset SHA 分别运行三种模式。

#### 面试怎么讲

> 在比较单 Agent 和多 Agent 前，我先解决实验可复现性。系统把每次运行保存为带 Dataset SHA、Git Commit、模型、Matcher、案例集合和 Usage 的不可覆盖工件，比较器只接受身份一致的三份真实 DeepSeek 结果。这样可以防止把不同数据或不同代码产生的指标误当成架构收益，同时把 Recall 增益和额外 Token、延迟放在同一个报告里。

#### 准确边界与尚未完成

相同 `git_dirty=true` 只能说明三次运行都有未提交改动，不能证明未提交内容完全相同，因此正式对照应使用干净提交。评测工件可能包含模型生成的短代码引用，应按普通 Review 输出管理，不能当作无敏感信息的纯指标文件。本批没有实现 Planner 自动选择“单专业 Agent”的批量路由，新 Holdout 可以先按冻结的案例域逐案例选择明确角色；也没有允许多 Agent 工件成为正式 baseline。

### 6.13 Security 架构对照 Holdout v1

#### 要解决的问题

此前多 Agent 的真实试跑都来自已经参与开发的 Seed 或 Aggregation 案例，能够证明链路、去重和审计，但不能证明架构在新输入上的泛化收益。与此同时，三架构比较器要求单专业工件对应一个明确角色，因此把 Security、Performance、Maintainability 混在同一数据集会使“单专业 Agent”基线失去清晰含义。

#### 设计选择与行为变化

- 新增 `architecture-security-holdout-v1` 四案例冻结分片，所有案例都只路由 Correctness 与 Security；
- 一个混合案例同时包含 API Key 前缀认证绕过和剩余有效期方向错误，用于观察两个专业角色是否产生互补召回；
- 两个纯 Security 案例分别覆盖管理员字段 Mass Assignment 和登录后 Open Redirect；
- 一个 Negative 使用 `hmac.compare_digest` 比较完整 Webhook Secret，用于观察 General、Security 和 Multi-Agent 的误报；
- Expected Finding 共四条：Security 三条、Correctness 一条；Severity 为两条 High、两条 Medium；
- Dataset SHA 固定为 `ef6c5dc7a804b4c89a4b051c664006d7145943f99b6ae25d526db7d95b41873e`。

#### 自动化验证证据

冻结测试验证 Dataset Version/SHA、案例数、负例数、Finding Category/Severity 分布、Expected Range 位于新增行内，以及新 Case ID、Head SHA、Patch 与已有四套数据集不重复。Planner 对四个案例均稳定输出 `correctness + security`。General、Security 和 Planned Multi-Agent 的 Mock 路径均 4/4 Case Pass、Recall=1.0、False Positive=0、Usage=null；Multi-Agent 自测执行 8 次 Agent 调用并从 8 条 Fixture Finding 聚合为 4 条。Mock 结果只证明评测器与聚合器自洽，不证明模型质量。

#### 真实集成证据

本批没有调用 DeepSeek、数据库或 GitHub。数据集在第一次 live 输出之前完成标签、范围、Matcher 关键词和 SHA 冻结；真实三架构结果必须在本批提交之后运行并单独记录。

#### 面试怎么讲

> 我没有继续用已经调过规则的 Seed 案例证明多 Agent 更好，而是先冻结一个角色一致的新 Security 分片。它既有跨角色混合缺陷，也有纯安全缺陷和安全负例；在模型运行前就固定答案和 SHA。这样后续 General、Security-only 和 Planned Multi-Agent 的差异可以同时从召回、误报、Token、延迟和角色独有贡献解释，而不会因为看过输出后修改标准污染结论。

#### 准确边界与尚未完成

这是四案例的 Security 分片，不代表所有语言、框架或漏洞类型，也不能外推生产准确率。Expected Finding 由项目开发者构造而非多位安全专家独立标注。第一次 live 结果之后不应修改本版本的 Prompt、Planner、Matcher、标签或 Fixture；若发现数据缺陷，应保留 v1 证据并创建修正版。Performance 和 Maintainability 仍需要独立的新分片。

### 6.14 Security Holdout 三架构真实对照

#### 要解决的问题

在冻结 `architecture-security-holdout-v1` 之后，需要用同一模型、同一数据、同一代码提交和同一 Matcher 回答一个具体问题：General Agent、Security Specialist 和 Planned Multi-Agent，哪一种在当前实现下提供了更好的质量/成本平衡。不能用三次终端输出做人工印象比较，也不能因为多 Agent 架构更复杂就预设它一定更好。

#### 设计选择与实验控制

- 三次运行都使用 `deepseek-flash`、`text-v2` Matcher 和同一冻结 Dataset；
- Dataset Version 为 `architecture-security-holdout-v1`，SHA-256 为 `ef6c5dc7a804b4c89a4b051c664006d7145943f99b6ae25d526db7d95b41873e`；
- 三份工件都绑定干净提交 `e2d4b2ce2c3f4a448056e33b871ccd903ae63c09`，`git_dirty=false`；
- General 使用 `review-v2`，单专业架构使用 `review-security-v1`，多 Agent 使用 `multi-agent-v1` 和 `finding-text-v3`；
- 三种模式都执行相同 4 个 Case，多 Agent 共执行 8 次 Agent 调用；
- 工件保存在 Git 已忽略的 `evals/results/`，不会冒充正式 Model Baseline，也不提交模型原始输出；
- 对照由离线 Comparator 完成，没有再次调用模型、数据库或 GitHub。

#### 自动化验证证据

本批只记录此前已实现实验工具产生的真实结果，没有修改生产代码或测试代码，因此没有新增自动化测试数量。Comparator 成功通过 Provider、Model、Dataset Version/SHA、Matcher Version、Git Commit、Dirty 状态和 Case ID 集合的一致性门禁；三份工件均可离线解析和比较。文档修改完成后只执行文本格式与 Git 差异检查，不把它们表述为新的模型质量证据。

#### 真实 DeepSeek 对照结果

三种架构均为 4/4 `successful_case`，Schema Validity 均为 `1.0`，没有 Transport、Provider 或 Schema Error。

| 架构 | Recall | Precision | F1 | Case Pass | False Positive | Category Match | Severity Match | Total Tokens | Mean Latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| General Agent | 1.0 | 1.0 | 1.0 | 0.75 | 0 | 1.0 | 0.75 | 8,381 | 5,639.12 ms |
| Security Specialist | 0.75 | 1.0 | 0.8571 | 0.75 | 0 | 1.0 | 0.6667 | 7,211 | 3,871.84 ms |
| Planned Multi-Agent | 1.0 | 0.8 | 0.8889 | 0.75 | 1 | 1.0 | 0.75 | 18,126 | 11,267.44 ms |

与 General 相比，Security Specialist 少使用 1,170 Tokens、平均延迟降低约 1.77 秒，但少召回一条混合案例中的 Correctness Finding。Planned Multi-Agent 没有增加命中数或 Case Pass，反而增加 1 条 False Positive、9,745 Tokens 和约 5.63 秒平均延迟；每额外 1,000 Tokens 带来的净新增命中为 0。

#### 逐案例解释

1. **管理员字段 Mass Assignment**：三种架构都命中唯一目标 Finding，标签正确且无误报。多 Agent 中 Correctness 与 Security 报告同一根因，Aggregator 正确合并。
2. **API Key 前缀认证与有效期方向错误**：General 同时命中两条目标 Finding；Security Specialist 只报告职责范围内的认证绕过，因此漏掉 Correctness Finding。多 Agent 恢复两条目标召回，但保留了一条额外 Security Finding，并把认证绕过评为 `critical` 而不是 Expected `high`。
3. **登录 Open Redirect**：General 找到问题但评为 `high`，Expected 为 `medium`；Security Specialist 给出正确的 `security/medium`。多 Agent 聚合时选择 Security 标签和严重程度，使该案例相对 General 得到改善。
4. **常量时间 Webhook Secret 负例**：三种架构均返回 0 Finding，没有负例误报。

#### 多 Agent 误报定位

混合 API Key 案例中，Correctness Agent 正确报告 `seconds_until_expiry` 使用 `now - expires_at`，导致 UI 剩余时间符号反转。Security Agent 针对同一表达式额外推测：“如果下游把非负值当成仍然有效，可能继续接受过期凭证”。但 Fixture 明确提供的是 UI 展示契约，没有证据说明该 helper 参与鉴权或有效期强制执行，因此该安全影响属于基于未提供调用场景的推测。

Aggregator 没有把这条 Security 描述与 Correctness 的有效期方向错误识别为同一问题，最终同一行代码保留两条 Finding。这里同时暴露两个独立改进点：

- Specialist Prompt 的“不得推测未提供的下游调用场景”约束仍可能失效；
- Finding Identity v3 对“相同代码根因、不同影响叙述”的识别仍不稳定。

不能简单把 Identity 阈值继续放宽，因为同一表达式上也可能存在多个真实根因。更稳妥的后续方向是加入证据约束或最终仲裁，区分“相同根因的复述”和“缺少代码证据的影响推测”。

#### 工程结论与行为决策

本轮没有证明多 Agent 优于 General。当前 Security 分片上，General 在 Recall、Precision、F1 和总成本之间表现最好；Security Specialist 更快、更省 Token，但不适合单独处理包含跨领域缺陷的 PR；Planned Multi-Agent 展现了角色互补和标签仲裁价值，但这些收益尚不足以抵消误报、Token 和延迟成本。

因此生产 Worker 继续保持单 General Agent，不把 eval-only Planned Multi-Agent 接入真实 PR 主链路。下一批优先处理证据约束与聚合仲裁，再用新的冻结分片重新评估；并发执行只能改善墙钟延迟，不能解决 Token 增长或误报，因此不作为当前第一优先级。

#### 面试怎么讲

> 我没有为了证明多 Agent 更先进而只展示成功案例，而是在模型运行前冻结了一个 Security Holdout，并让 General、Security-only 和 Planned Multi-Agent 在同一模型、同一 Dataset SHA、同一 Git Commit 下运行。结果 General 的 Recall、Precision 和 F1 都是 100%；多 Agent 保持 100% Recall，但 Precision 降到 80%，Token 从 8,381 增加到 18,126，平均延迟从约 5.64 秒增加到 11.27 秒。进一步审计发现，额外误报来自 Security Agent 推测了数据中没有提供的下游鉴权用途，同时聚合器没有把它和同一代码根因的 Correctness Finding 对齐。因此我没有把多 Agent 接入生产，而是把证据约束和最终仲裁作为下一步。这体现的是用可复现实验决定架构，而不是先选复杂架构再寻找支持结论。

#### 准确边界与尚未完成

- 这只是 4 个 Security 案例的一次真实运行，样本量不足以外推生产准确率或随机稳定性；
- Expected Severity 由项目开发者设定，`high/critical` 等边界存在业务语境差异；
- 多 Agent 当前按顺序执行，平均延迟包含多个 Agent 调用的累计时间，尚未验证并发执行收益；
- Cost 仍为 `null`，没有在 Provider 中硬编码价格，也不从 Token 擅自推算费用；
- `unique_finding_count` 表示多 Agent 内部聚合后的角色独有项，不代表相对 General 的净新增有效缺陷；
- 本次结果不修改冻结 Dataset、Prompt、Matcher 或标签；若后续人工审计认为 Ground Truth 有问题，应保留 v1 并创建新版本；
- Performance 和 Maintainability 架构分片尚未建设，证据约束/仲裁、并发、预算和 Agent Execution 持久化也尚未完成。

### 6.15 Security Prompt v2 与 Finding Identity v4

#### 要解决的问题

Security 三架构真实对照中，Correctness Agent 报告 `seconds_until_expiry` 的减法方向错误；Security Agent 针对同一表达式进一步推测“某个下游消费者可能把正数当作凭证仍然有效”。Fixture 只给出了 UI 展示契约，没有证明该 helper 参与鉴权。Identity v3 又因为两条 Finding 的标题和影响描述差异较大而没有合并，最终同一根因被发布两次，并产生一条自动评分 False Positive。

直接给所有 Finding 增加必填 Evidence 字段会同时影响 Provider JSON Schema、领域对象、数据库持久化、历史 Eval 工件和大量 Fixture。本批先在 eval-only 专业 Agent 与聚合边界做最小兼容改造，不修改数据库，也不改变生产单 General Agent 链路。

#### 设计选择与行为变化

- Security Prompt 从 `review-security-v1` 升级到 `review-security-v2`；
- 明确规定函数名、可能的 Caller 或假设的下游消费者都不能证明安全影响，必须由已提供 Patch 或显式 PR 契约证明该值参与安全决策；
- 如果数据只证明 UI 或功能契约错误，Security Agent 应当忽略，而不是把假设性后果重分类为安全 Finding；
- Finding Identity 从 `finding-text-v3` 升级到 `finding-text-v4`，完整保留原有精确、文本 containment、标题加代码证据和 Explanation 根因路径；
- 新增保守的“问题代码 + 修复代码”路径：两条 Finding 必须在同一文件，Explanation 中引用兼容的缺陷代码片段，Suggestion 中也引用兼容的修复代码片段；
- 代码片段比较保留运算符和操作数顺序，允许短表达式被更完整的表达式包含，例如 ``now - expires_at`` 与 ``int((now - expires_at).total_seconds())``；
- Category、Severity 和 Confidence 仍不参与 Finding Identity，避免标签反向污染去重；
- Aggregator 版本升级为 `multi-agent-v2`。当分组只能依靠“问题代码 + 修复代码”成立且 Category 冲突时，不再让专业角色匹配绝对优先，而是先按 Confidence、再按 Role Alignment 和确定性 Tie-break 选择 Canonical Finding；
- 这种特殊仲裁会在 `AggregationDecision.reason` 中明确记录，Category/Severity Conflict 仍保留所有观测值和最终选择；
- 普通强身份匹配继续使用原有“Role Alignment 优先”策略，因此 SQL 注入等明确安全问题的行为没有改变。

#### 自动化验证证据

新增四类回归：

1. 使用本次真实有效期 Finding 的核心文本，验证 Identity v4 将不同影响描述识别为 `problem_and_remediation_code`；
2. Aggregator 回放 Correctness `0.95` 与推测性 Security `0.80` 冲突，验证最终只保留 Correctness Finding，并记录 Confidence 优先的证据仲裁原因；
3. Security Prompt 测试冻结 `review-security-v2` 的调用链证据要求；
4. 在未修改的 `architecture-security-holdout-v1` 上回放本次两个 Agent 的四条真实输出形状，验证聚合从 `4 -> 3` 修正为 `4 -> 2`、两个目标均命中、False Positive 从 1 降为 0。

原有 SQL 注入重复、路径穿越/资源泄漏相邻根因、缺少索引等反例继续通过，说明新路径没有把“同一代码表达式上的不同问题”全部合并。最终验证为：

```text
Targeted evidence/aggregation tests: 26 passed
All tests: 310 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (100 source files)
git diff --check: passed
```

#### 真实集成证据

本批没有调用 DeepSeek、数据库或 GitHub，也没有生成新的 Token、Latency 或模型质量数据。回放输入来自上一批已经保存并人工审计的真实 DeepSeek 输出，但回放执行本身是确定性自动化测试，只能证明新 Identity 和 Aggregator 对该失败样本的行为。

回放后该混合案例仍不是完整 Case Pass：API Key 前缀认证绕过在真实输出中被 Security Agent 评为 `critical`，而冻结 Expected Severity 是 `high`，所以 Severity Match 仍为 `1/2`。本批修复的是重复误报和推测性标签主导问题，没有篡改历史标签，也没有声称解决所有严重程度分歧。

#### 面试怎么讲

> 多 Agent 的一次真实对照暴露了一个典型问题：两个 Agent 引用了同一错误表达式，也给出了同一个反向减法修复，但一个按 UI 契约描述为 Correctness，另一个根据假设的下游消费者扩展成 Security，旧聚合器因此发布两条。我没有直接降低文本相似度阈值，也没有立即改数据库 Schema，而是版本化 Security Prompt，要求安全影响必须由已提供调用链证明；同时把 Identity 扩展为“问题代码和修复代码双证据”。只有这种较弱路径发生跨类别冲突时，聚合器才不让 Specialist 标签绝对优先，并把选择原因写入审计记录。真实输出回放把 `4 -> 3` 修复成 `4 -> 2`，误报降为 0，但我仍保留 `critical/high` 的 Severity 分歧，没有把局部修复包装成整个案例满分。

#### 准确边界与尚未完成

- Identity v4 依赖模型在 Explanation 和 Suggestion 中使用反引号引用代码；没有结构化 Evidence 的输出仍可能漏合并；
- Confidence 是模型自报值，不是客观真实性概率；本批只在严格的双代码证据和跨类别冲突条件下将它置于 Role Alignment 之前；
- 新规则能合并同一根因复述，但不能通用判断任意自然语言安全影响是否属于幻觉；
- Prompt v2 是否能在真实调用中减少推测性输出尚未验证；旧 Security Holdout v1 已参与本次开发，只能作为回归集，不能再作为未见盲测；
- 下一步应先建立新的 Security Architecture Holdout v2，冻结后再运行 General、Security v2 和 Multi-Agent v2，验证质量收益是否能在未见案例上复现。

### 6.16 Security Architecture Holdout v2

#### 要解决的问题

Security Holdout v1 已经用于发现并修复推测性安全影响与重复 Finding，因此它现在属于开发回归数据。继续在 v1 上运行修改后的 Prompt 和 Aggregator，可以验证旧失败是否回归，却不能证明改动对未见输入有效。需要在任何新真实模型输出出现之前，独立设计、标注并冻结第二个 Security 架构分片。

#### 设计选择与行为变化

- 新增 `architecture-security-holdout-v2`，不是对 v1 标签或 Fixture 的覆盖，而是四个全新案例组成的独立分片；
- 混合案例跨两个文件，同时包含关闭 JWT 签名验证的 Security 缺陷和分页 Slice 少返回一项的 Correctness 缺陷，用于再次测试专业角色互补；
- 第二个正例使用通用 `random.Random` 生成密码重置码，覆盖此前数据集中没有出现的凭证随机性问题；
- 第三个正例把 Bearer Access Token 放入浏览器 URL 查询参数，覆盖浏览器历史、代理日志、分析系统和 Referer 传播导致的凭证泄漏；
- Negative 使用 `secrets.token_urlsafe(32)` 生成高熵密码重置 Token，并只持久化 SHA-256 Digest；PR 契约明确未变更 Caller 负责 TLS、十五分钟过期和单次使用，用于测试 Security Prompt v2 是否还会凭空报告缺失的下游保护；
- 四个案例全部稳定路由到 `correctness + security`，没有引入 Performance 或 Maintainability Agent，使 General、Security-only 和 Planned Multi-Agent 保持可比较语义；
- Expected Finding 共四条：Security 三条、Correctness 一条；Severity 为三条 High、一条 Medium；包含一个多缺陷案例、一个多文件案例和一个 Negative；
- Dataset SHA 冻结为 `d786fddace3bd5a58f75700fe1182edca97768f281bb6ab16014bc45c7ff531b`。

#### 自动化验证证据

冻结测试验证以下内容：

- Dataset Version、SHA、案例数、Expected Finding 数、Negative 数和 Category/Severity 分布固定；
- 新 Case ID、Head SHA 和 Patch 与 Seed v1、Holdout v1/v2、Aggregation v1、Architecture Security v1 全部不重复；
- 每个 Expected Range 都位于对应文件的新增行范围内；
- Planner 对四个案例都只返回 Correctness 与 Security；
- General、Security Specialist 和 Planned Multi-Agent 的 Mock 自检均为 4/4 Case Pass、Recall=1.0、False Positive=0、Usage=null；
- Multi-Agent Mock 执行 8 次 Agent 调用，并从 8 条 Fixture Finding 聚合为 4 条。

Mock 输出仍然只证明数据加载、路由、Runner、Aggregator 和 Metrics 自洽，不证明 DeepSeek 能发现这些问题。

#### 真实集成证据

本批没有调用 DeepSeek、数据库或 GitHub，也没有生成新的真实 Token、Latency 或质量结论。v2 在首次模型运行前已经完成案例、契约、标签、范围、Matcher Terms、Planner 路由和 SHA 冻结。正式对照必须在提交本批代码、工作区干净后分别生成 General、Security v2 和 Multi-Agent v2 三份不可覆盖工件。

#### 面试怎么讲

> 第一套 Security Holdout 帮我定位了 Specialist 推测下游影响和 Aggregator 重复发布的问题，但一旦我根据它修改 Prompt 和 Identity，它就不再是盲测。我没有在原数据上反复跑到分数变好，而是保留 v1 作为历史回归，再冻结一套完全不同的 v2：未验证 JWT、分页边界、密码重置随机性、Access Token URL 泄漏和一个带明确外部契约的安全负例。所有标签和 SHA 都在调用模型之前固定，这样下一次三架构比较才能检验修改是否泛化，而不是检验是否记住上次失败。

#### 准确边界与尚未完成

- v2 仍只有四个手工构造案例，不代表真实仓库的语言、框架和威胁分布；
- JWT、随机数和 Token URL 的 Expected Severity 由项目开发者标注，尚未经过多位安全专家独立裁决；
- Negative 的外部契约来自 PR Body，主要验证模型是否尊重明确边界，不能证明未展示的 Caller 实现真的安全；
- 数据集现在已经进入仓库开发流程，后续不能根据真实输出静默修改；若发现 Fixture 或标签问题，必须保留 v2 并创建新版本；
- 下一步只应先提交冻结分片，再由用户显式运行三次付费 DeepSeek 评测。本批不能提前给出多 Agent 是否改进的结论。

---

## 7. 当前可用于简历的表述

以下表述与现有实现和实测一致：

> 构建基于 FastAPI、Celery、Redis、MySQL、GitHub App 和 DeepSeek Responses API 的事件驱动 PR 自动评审系统，完成 Webhook → Diff → 结构化 AI Review → GitHub Review 的真实端到端联调。

> 通过 Delivery ID、PR Head SHA、数据库唯一约束、Report 断点恢复及 Publication 远程标记对账实现业务幂等；实测 GitHub Webhook Redelivery 不会重复创建任务、调用模型或发布 Review。

> 通过 `ReviewEngine`、`ReviewAgent`、Orchestrator 和透传 Aggregator 将生产 Pipeline 与具体 AI 服务解耦，在保持单次模型调用和既有 Report/Publication 行为不变的前提下，为后续专业 Agent 扩展建立可测试边界。

> 实现角色化 Agent Registry、四类版本化专业 Prompt 和可审计的确定性 Planner，并以 Eval-first 方式支持单专业 Agent 的隔离评测；生产链路继续使用单 General Agent，避免在去重、恢复和预算机制完成前扩大调用。

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
| Agent 架构接缝 | 222 | ReviewEngine、General Agent、Orchestrator、透传聚合、异常传播和 Pipeline 零行为回归 |
| 专业 Agent 与 Planner | 245 | 四类专业 Prompt、Registry、确定性路由、Agent 元数据、Eval 隔离运行和历史 baseline 兼容 |
| Planner 误选修复 | 246 | 区分重复数据与重复代码，增加负向路由断言并将 Seed 候选调用从 17 降到 16 |
| Validator 与确定性 Aggregator | 265 | 聚合边界验证、Finding 身份、跨 Agent 去重、标签冲突审计和输入顺序无关性 |
| Eval-only Planned Multi-Agent Runner | 274 | Planner/Registry/Agent/Aggregator 串联、执行级指标、贡献统计、失败角色与安全门禁 |
| Finding Identity v2 | 277 | 用真实多 Agent 同义输出构建回归，共同根因与代码证据去重，并保留不同问题负例 |
| 聚合审计快照 | 278 | 原始 Agent Findings、kept/merged 决策、规范指纹、Category/Severity 冲突值与历史 baseline 兼容 |
| 多缺陷聚合挑战集 | 286 | 四类混合案例、角色脚本冲突、独有贡献、负例和复杂输入顺序稳定性 |
| Finding Identity v3 | 289 | 真实路径穿越复述回归、Explanation 根因 fallback 和相同代码不同根因保护 |
| 架构实验工件与离线对照 | 301 | 不可覆盖工件、Provider 前置失败、三架构身份门禁、逐案例解释和旧 baseline 兼容 |
| Security 架构 Holdout v1 | 306 | 四个新案例、冻结 SHA、历史数据隔离、Planner 路由、三模式 Mock 自测和 Negative 约束 |
| Security Prompt v2 与 Finding Identity v4 | 310 | 调用链证据约束、问题/修复双代码证据、跨类别保守仲裁、真实输出回放和不同根因保护 |
| Security 架构 Holdout v2 | 315 | 四个新案例、冻结 SHA、全历史数据隔离、双角色稳定路由、三模式 Mock 自测和契约负例 |

当前最近一次结果：

```text
Targeted Security architecture holdout v2 tests: 5 passed
All tests: 315 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed (100 source files)
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
- 四个专业 Agent 已完成 6 次真实 DeepSeek 单案例 partial trial：四个正例全部命中、两个负例均为 0 Finding，6/6 Case Pass、0 False Positive、Schema 全部合法；总计 8539 Tokens。
- 相同 6 个案例的 General v2 使用 8205 Tokens；专业单角色试跑多使用 334 Tokens（约 4.1%），质量指标在这些简单已见案例上相同。该比较不等于 Planner 实际多 Agent 总成本，也不构成泛化质量结论。
- 第一次真实 DeepSeek 多 Agent SQL 注入 partial trial 绑定 commit `e6f43d0`：Correctness 与 Security 均成功，总计 3420 Tokens、约 7.29 秒；Identity v1 未合并两个同根因 Finding，导致 1 个评测 False Positive 和 Case Pass 失败。该结果是 dirty-worktree partial trial，不是 baseline。
- Finding Identity v2 真实重验绑定 commit `dd95c4b`：两个 Agent 均成功，聚合从 `2 -> 2` 修复为 `2 -> 1`，Merged=1、False Positive=0、Case Pass=true；总计 3685 Tokens、约 8.55 秒。该结果仍是 dirty-worktree partial trial，不是 baseline。
- 聚合审计快照真实重验绑定 commit `5d2e66d`：Correctness 与 Security 各返回一条 SQL 注入 Finding，新格式完整记录两份原始 Findings；Security Finding 被 `kept`、Correctness Finding 被 `merged`，两者共享规范指纹 `ce3d975a…513d39e2`。聚合为 `2 -> 1`、Duplicate Rate=`0.5`、False Positive=0、Case Pass=true，总计 3637 Tokens、约 7.83 秒。
- 本次两个 Agent 均输出 `security/high`，所以 Conflict=0；此前一次 Conflict 没有复现，不能猜测旧冲突字段。两个 Agent 的 `unique_finding_count` 都为 0，因此本案例证明的是真实 Provider 下的去重与可追溯性，不证明多 Agent 提升 Recall。运行绑定已提交代码，但因个人文档使 `git_dirty=true`，且只运行已见 Seed 单案例，所以仍不是 baseline。
- `aggregation-v1` 相邻缺陷案例首次真实运行绑定 commit `2991ae8`：Correctness 发现路径穿越和资源泄漏，Security 也发现路径穿越，两个目标缺陷均命中且标签正确；Identity v2 没有合并路径穿越复述，最终 `3 -> 3`、False Positive=1、Case Pass=false，总计 4643 Tokens、约 13.07 秒。这是 Identity v3 的真实失败输入来源，但 v3 尚未真实重验。
- Identity v3 真实重验绑定 commit `06cedad`：Correctness 独立发现资源泄漏，Security 独立发现路径穿越，最终 `2 -> 2`、两者 `unique_finding_count=1`、False Positive=0、Case Pass=true，总计 4044 Tokens、约 9.43 秒。此次没有生成重复路径 Finding，因此证明了真实链路和不同根因保留，但不构成新 fallback 的 live 合并证据；fallback 由修复前真实输出回放测试覆盖。运行仍为 dirty-worktree 单案例 partial trial，不是 baseline。
- Security 三架构正式对照绑定干净提交 `e2d4b2c` 和冻结 Dataset SHA `ef6c5dc7…1873e`：General、Security Specialist、Planned Multi-Agent 均完成 4/4 合法响应且无链路错误。General 为 Recall/Precision/F1=`1.0/1.0/1.0`、0 False Positive、8,381 Tokens、平均 5.64 秒；Security Specialist 为 `0.75/1.0/0.8571`、7,211 Tokens、平均 3.87 秒；Multi-Agent 为 `1.0/0.8/0.8889`、1 False Positive、18,126 Tokens、平均 11.27 秒。本分片没有证明多 Agent 相对 General 的净质量收益，因此生产路径保持 General。

---

## 9. 尚未完成与下一步

### 9.1 最近下一步

1. 提交已经冻结的 Security Architecture Holdout v2，在干净工作区分别生成 General、Security v2 和 Planned Multi-Agent v2 三份真实 DeepSeek 工件并离线比较；
2. 根据 v2 对照决定是否继续 Multi-Agent；若仍未超过 General，则正式收口实验并进入 GitHub 行级评论；若达到质量/成本门槛，再补 Agent Execution 持久化、`PARTIAL` 策略、并发上限和 Token Budget；
3. Multi-Agent 决策完成后，依次实现行级 Diff 映射与幂等发布、大型 PR 分片与预算控制，最后完成可观测性、README 和演示收尾。

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
