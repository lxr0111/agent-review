# AgentReview 项目规划

## 1. 项目定位

AgentReview 是一个面向 GitHub Pull Request 的多 Agent 自动代码评审平台。PR 创建或更新后，系统异步获取 Diff，构建语法感知上下文，由专业 Review Agent 分析风险，经过验证、去重和排序后发布 PR Review。

目标岗位是 Agent/AI 应用工程，兼顾 Python 后端。因此项目既要体现 Agent 工作流、工具调用、结构化输出和质量评测，也要体现 Webhook、异步任务、幂等、重试、数据一致性和安全性。

一句话简历描述：

> 基于 FastAPI、Celery、Tree-sitter 和大语言模型构建 GitHub PR 自动评审平台，支持多 Agent 动态路由、行级评论、异步重试、提交乱序保护及离线质量评测。

首版只支持 GitHub 和 Python 代码。系统只发现和解释风险，不自动修改或合并代码。

## 2. 核心链路

```text
GitHub PR Webhook
        ↓
验签、事件过滤、数据库幂等
        ↓
任务落库并立即返回 202
        ↓
Celery Worker 获取 Diff 和源码
        ↓
Tree-sitter 提取语法上下文并分片
        ↓
Planner 根据变更选择 Review Agent
        ↓
Security / Correctness / Quality / Test
        ↓
Validator 校验事实、路径和行号
        ↓
Aggregator 去重、排序、控制评论数量
        ↓
检查 PR 当前 head SHA
        ↓
发布 GitHub Review 和行级评论
```

## 3. 技术选型

| 模块 | 选型 | 原因 |
|---|---|---|
| API | Python 3.12 + FastAPI | 类型清晰，异步 I/O 和接口文档方便 |
| 数据校验 | Pydantic | 约束 Webhook、Agent 和 LLM 输出 |
| 数据库 | MySQL 8 + SQLAlchemy 2 | 保存任务、调用、意见和反馈 |
| 数据迁移 | Alembic | 管理表结构版本 |
| 异步任务 | Celery + Redis | 部署简单，支持 Worker、重试和扩展 |
| 缓存/限流 | Redis | 缓存、计数和并发控制 |
| 代码平台 | GitHub App + REST API | 短期令牌和细粒度仓库权限 |
| 代码解析 | Tree-sitter | 按函数、类等语法节点补充上下文 |
| 模型层 | 自建 LLM Provider Adapter | 避免绑定单一模型厂商 |
| HTTP | httpx | 连接池及同步/异步支持 |
| 测试与质量 | pytest、Ruff、mypy | 测试、规范和类型检查 |
| 部署 | Docker Compose | 本地复现和面试演示方便 |

首版不引入 Kafka、Kubernetes、LangChain、向量数据库和微服务。出现明确需求后再加入。

## 4. MVP 范围

必须完成：

1. 接收 PR 创建、更新和重新打开事件；
2. 校验 `X-Hub-Signature-256`；
3. 使用 GitHub delivery ID 和数据库唯一约束防重；
4. 任务先落库再投递，Webhook 快速返回 `202 Accepted`；
5. 获取文件列表、patch、base SHA 和 head SHA；
6. 过滤二进制、生成文件、锁文件和超大文件；
7. 调用一个模型生成严格结构化的候选意见；
8. 验证路径、证据和行号，只评论 Diff 中允许评论的行；
9. 发布摘要 Review 和行级评论；
10. 发布前检查最新 head SHA，旧提交结果不得覆盖新结果；
11. 保存任务、模型调用和审查意见；
12. Docker Compose 一键启动 API、Worker、MySQL、Redis；
13. 提供单元测试和可重复的端到端演示。

MVP 完成后再做 Tree-sitter、多 Agent、反馈学习、管理页面和多语言。

## 5. Agent 设计

- Planner：分析文件、语言、变更性质和风险，决定调用哪些 Agent；
- Security Agent：检查注入、越权、敏感信息和不安全反序列化；
- Correctness Agent：检查边界、空值、异常、资源释放和逻辑错误；
- Quality Agent：检查复杂度、重复逻辑和可维护性；
- Test Agent：检查缺失测试、边界用例和回归风险；
- Validator：程序化验证模型结论，必要时进行一次模型复核；
- Aggregator：合并重复或冲突意见，排序并限制评论数量。

Agent 不是只有不同角色 Prompt。每个 Agent 都要有明确职责、输入输出 Schema、可用工具、预算和失败策略。

动态路由示例：

- 只改文档：跳过代码 Reviewer，只生成摘要；
- 修改认证、权限、SQL 或依赖：启用 Security Agent；
- 修改业务逻辑：启用 Correctness Agent；
- 新增功能但没有测试：启用 Test Agent；
- 大规模重构：启用 Quality Agent，减少低价值行级评论。

首版 Agent 仅使用只读工具：获取 Diff、获取指定文件、查询局部符号、读取仓库规则和静态分析结果。不得执行仓库代码或访问任意 URL。

## 6. 审查意见协议

```json
{
  "category": "correctness",
  "severity": "high",
  "confidence": 0.91,
  "path": "src/service.py",
  "start_line": 42,
  "end_line": 45,
  "title": "异常被吞掉导致任务状态错误",
  "explanation": "问题及影响",
  "evidence": "来自源码的事实依据",
  "suggestion": "修改建议"
}
```

Validator 至少检查：

- 文件属于本次 PR；
- 行号位于可评论的 Diff 行；
- evidence 真实存在于提供的上下文；
- 分类、严重程度和置信度合法；
- 内容非空且没有超长；
- 与已有意见不重复；
- 置信度达到发布阈值。

## 7. 模块结构

采用模块化单体和独立 Worker。API 与 Worker 共享业务代码，但运行在不同进程。

```text
app/
  api/                 # FastAPI 路由和依赖
  core/                # 配置、日志、安全、异常
  domain/              # 任务、分片、意见及核心规则
  services/            # 用例编排
  agents/              # Planner、Reviewer、Validator、Aggregator
  providers/
    github/             # GitHub App、Diff、Review 适配器
    llm/                # 模型厂商适配器
    parser/             # Tree-sitter 适配器
  repositories/        # 数据访问接口及实现
  workers/             # Celery 任务
  models/              # SQLAlchemy ORM
  schemas/             # Pydantic DTO
  prompts/              # 带版本的 Prompt
tests/
  unit/
  integration/
  e2e/
```

不用硬套 DDD。核心规则和外部依赖能清晰分离即可。

## 8. 数据模型

### webhook_events

- `id`、`provider`、`delivery_id`、`event_type`；
- `payload_digest`、`status`、`received_at`；
- 唯一约束：`(provider, delivery_id)`。

### review_tasks

- 仓库、PR、`base_sha`、`head_sha`；
- 状态、当前阶段、模型、Prompt 版本；
- 错误码、起止时间、`version`；
- 唯一约束：`(repository_id, pr_number, head_sha, prompt_version)`。

状态机：

```text
RECEIVED → QUEUED → FETCHING → PLANNING → REVIEWING
         → VALIDATING → AGGREGATING → PUBLISHING → COMPLETED

处理中 → FAILED / CANCELLED / STALE / PARTIAL
```

### review_chunks

- `task_id`、路径、语言、起止行；
- 内容哈希、Token 数、状态、重试次数。

### model_calls

- 任务、分片、Agent 类型、模型和 Prompt 版本；
- 输入/输出 Token、耗时、状态和错误码。

不要长期保存包含敏感源码的完整 Prompt。

### findings

- 任务、分片、问题指纹、Agent 类型；
- 类别、严重度、置信度、路径和行号；
- 标题、解释、建议、验证状态和发布状态。

### feedback

- `finding_id`；
- `accepted / false_positive / ignored`；
- 备注和时间。

反馈首版只用于统计和规则排序，不宣称为模型训练。

## 9. Diff、AST 与分片

1. 获取 PR 文件和 patch；
2. 收集新增/修改行及行号；
3. 使用 Tree-sitter 找到覆盖变更行的最小完整函数、方法或类；
4. 补充必要的 import、类签名和相邻定义；
5. 明确标记新增代码、旧代码和外部上下文；
6. 按语法单元及 Token 预算装箱；
7. 超大节点按安全边界降级切分，并标记上下文不完整；
8. 保存新文件行号到 GitHub Diff 评论位置的映射。

Tree-sitter 提供语法树，不直接提供完整跨文件调用图。调用图属于后续静态分析能力。

## 10. 一致性、并发和重试

- API 不执行长任务，Worker 固定并发消费；
- 使用 Redis 限制全局、租户和仓库的模型并发；
- 每个 PR 设置文件数、Diff 行数、Token 和调用成本预算；
- 单个大 PR 不得占满全部 Worker；
- 任务只传 ID，不在队列消息中放大段源码；
- 网络超时、HTTP 429 和服务端 5xx 可重试；
- 签名错误、权限不足、非法输入不可重试；
- 重试采用指数退避和随机抖动；
- 分片部分失败时保存成功结果并标记 `PARTIAL`。

发布前重新获取 PR 当前 head SHA：相等才发布；不相等则标记 `STALE`。状态更新使用 `WHERE id=? AND version=?` 的条件更新，避免并发覆盖。

## 11. 安全要求

- 使用 GitHub App installation token，不存长期用户令牌；
- Secret 只从环境变量或 Secret Manager 注入；
- 不接受事件中传入的任意回调 URL，防止 SSRF；
- 日志不记录源码全文和凭证；
- 数据查询始终携带 installation/tenant ID；
- 代码、注释、README 都作为不可信数据，不能覆盖系统指令；
- 首版不执行仓库代码；静态工具以后必须放入受限沙箱；
- 临时代码及时清理，数据库设置源码数据保留周期。

## 12. API 和异步任务

```text
POST /api/v1/webhooks/github
GET  /api/v1/reviews/{task_id}
GET  /api/v1/reviews/{task_id}/findings
POST /api/v1/findings/{finding_id}/feedback
GET  /health/live
GET  /health/ready
GET  /metrics
```

Celery 任务：

```text
start_review(task_id)
fetch_context(task_id)
plan_review(task_id)
review_chunk(task_id, chunk_id, agent_type)
aggregate_review(task_id)
publish_review(task_id)
```

## 13. 评测体系

建立 30～50 个小型 PR 样例，包含 SQL 注入、硬编码密钥、鉴权遗漏、空值、边界错误、异常吞噬、资源泄漏、并发错误、缺少测试和无问题代码。人工标注类别、严重程度、文件和行号。

核心指标：

- Precision：发布意见中真实问题的比例；
- Recall：标注问题中被发现的比例；
- 行号准确率和重复评论率；
- 无问题 PR 的误报率；
- 用户接受率和误报反馈率；
- 单 PR Token 和模型成本；
- P50/P95 完成时间；
- Webhook P95 响应时间；
- 任务成功率和重试率。

所有指标必须附带样本量、模型版本、Prompt 版本和阈值。

## 14. 测试重点

单元测试：验签、事件幂等、Diff 解析、AST 定位、Token 分片、输出 Schema、意见去重、状态机、重试分类、新旧 SHA 判断。

集成测试：MySQL 并发唯一约束、Celery 重试、Redis 限流、GitHub/LLM Mock、Worker 中断恢复、同一 PR 两个 commit 乱序完成。

端到端测试：在演示仓库提交包含已知缺陷的 PR，系统自动发布 Review，并校验路径、行号、严重度和摘要。

## 15. 开发路线

### Phase 0：工程骨架（1～2 天）

FastAPI、配置、日志、MySQL、Alembic、Redis、Celery、Docker Compose、pytest、Ruff、mypy 和 CI。

验收：一条命令启动全部服务，API、数据库和 Worker 健康检查通过。

### Phase 1：最小闭环（3～5 天）

Webhook 验签和幂等、异步任务、获取 Diff、单模型结构化审查、发布摘要 Review。

验收：演示仓库新建 PR 后自动收到可靠的总结评论。

### Phase 2：可靠性和行级评论（4～6 天）

状态机、重试、Diff 行映射、行级评论、head SHA 保护、Token 统计。

验收：重复事件只有一个任务；旧提交结果不会覆盖新结果。

### Phase 3：语法感知上下文（4～7 天）

Tree-sitter、函数/类定位、上下文补充、Token 分片和超大 PR 降级。

验收：函数内一行变化时获得完整函数上下文，分片不超预算。

### Phase 4：多 Agent（5～8 天）

Planner、四类 Reviewer、Validator、Aggregator、并发与预算限制。

验收：不同变更触发不同 Agent，重复意见只发布一次。

### Phase 5：评测和作品化（4～7 天）

离线数据集、质量/成本/性能报告、Prompt Injection 测试、README、架构图、演示视频和部署文档。

按业余开发约需 4～6 周。Phase 0～3 足以成为完整后端 AI 项目，Phase 4～5 决定 Agent 特色和作品质量。

## 16. 推荐 Issue 划分

1. `foundation`：工程、配置、日志、CI；
2. `webhook`：验签、解析、幂等和任务创建；
3. `github-provider`：Token、PR、Diff 和 Review；
4. `task-engine`：Celery、状态机、重试、乱序保护；
5. `llm-provider`：统一接口、结构化输出和 Fake LLM；
6. `context-builder`：Diff、Tree-sitter 和分片；
7. `agents`：Planner、Reviewers、Validator、Aggregator；
8. `observability`：日志、指标、Token 和成本；
9. `evaluation`：样例、标注和评测脚本；
10. `docs-demo`：README、架构图和演示。

## 17. 完成后的简历模板

**AgentReview——基于多 Agent 的 GitHub PR 智能代码评审平台**

技术栈：Python、FastAPI、Celery、MySQL、Redis、Tree-sitter、GitHub App、Docker、LLM API

- 设计 GitHub Webhook 异步评审链路，通过验签、数据库唯一约束和状态机实现事件防重及可靠处理，使 Webhook P95 响应时间达到 `[实测值]`；
- 基于 Tree-sitter 将 Diff 映射到函数和类节点，在 Token 预算内构建上下文并行审查，P95 完成时间为 `[实测值]`；
- 实现 Planner、专业 Reviewer、Validator 和 Aggregator 工作流，支持动态路由、结构化输出、事实校验和意见去重；
- 发布前校验 PR 最新 head SHA，避免旧结果覆盖新提交，并通过指数退避和限流处理模型超时与 429；
- 构建 `[样本数]` 个 PR 的离线评测集，达到 Precision `[实测值]`、Recall `[实测值]`，并统计 Token 成本与用户反馈。

方括号数据只能在真实测试后填写。

## 18. 项目成功标准

- 可运行：创建 PR 后自动发布 Review；
- 可复现：他人能按 README 用 Docker Compose 启动；
- 可靠：重复事件、超时和提交乱序都有测试；
- 可解释：每个 Agent 职责和路由规则清楚；
- 可评估：有固定数据集和质量、性能、成本指标；
- 可展示：有整洁仓库、架构图、演示和实测结果；
- 可答辩：简历中的每个技术点都对应代码、测试或数据。
