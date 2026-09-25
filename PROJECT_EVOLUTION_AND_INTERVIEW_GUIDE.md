# AgentReview 项目演进记录与面试讲解指南

> 最后更新：2026-09-25
> 记录范围：从 `cc5c769 docs: add phase one implementation interview guide` 之后，到当前 Provider usage metadata 改造完成为止。

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

## 4. 当前能力快照

截至 2026-09-25，系统已经具备：

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

- 10 个固定种子案例；
- 2 个 negative samples；
- 确定性 Matcher；
- Recall、误报、类别、严重程度、Schema、Case Pass 和 latency 指标；
- Mock evaluator self-test；
- Provider token usage 数据通道。

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
4. **可评估**：固定 10 个 seed cases，计算 Recall、误报、类别、严重程度、Schema 和 Case Pass，避免只凭一次 Demo 判断模型质量。

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

> 建立包含 correctness、security、performance、maintainability、reliability 和 negative samples 的 10 案例评测种子集，实现同文件文本匹配、一对一 Finding 对齐及 Recall、误报、类别、严重程度、Schema 和 Case Pass 指标，为 Prompt 和 Agent 架构迭代提供回归基础。

暂时不要写入简历的内容：

- “多 Agent 已实现”；
- “行级评论已实现”；
- “达到某个真实模型 Recall/Precision”；
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

当前最近一次结果：

```text
Eval tests: 17 passed
All tests: 182 passed, 2 third-party deprecation warnings
Ruff: passed
mypy app evals: passed
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
- 数据库只读检查和唯一索引检查通过。

---

## 9. 尚未完成与下一步

### 9.1 最近下一步

1. 实现受保护的 DeepSeek live eval；
2. 保存包含 Git SHA、Dataset SHA、Model、Prompt Version 和指标的 baseline；
3. 使用显式价格快照计算 cost，不在 Provider 中硬编码价格；
4. 用第一次真实 baseline 审核 Matcher、种子案例和标签是否需要调整。

### 9.2 后续能力

- 真实 DeepSeek 10-case baseline；
- Baseline 对比和回归阈值；
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
