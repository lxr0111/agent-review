# AgentReview 多 Agent 改造前评审与设计基线

> 状态：Finding Identity v3 已完成自动化修复，等待真实多 Agent 重验，尚未实现生产链路
> 更新时间：2026-09-27
> 适用范围：从当前单 Agent 系统演进到可恢复、可评测的多 Agent 系统

## 1. 文档目的

本文记录进入多 Agent 改造前的系统状态、引入动机、设计边界、风险和实施顺序。它解决三个问题：

1. 明确当前已经实现和实测的能力，避免把规划写成成果；
2. 解释为什么在单 Agent 已经可用的情况下仍考虑多 Agent；
3. 为后续每一批 Agent 改造提供架构和评测基线。

本文不替代：

- `PROJECT_PLAN.md`：项目最初的总体方案；
- `PHASE1_IMPLEMENTATION_AND_INTERVIEW_GUIDE.md`：Phase 0～1 历史快照；
- `PROJECT_EVOLUTION_AND_INTERVIEW_GUIDE.md`：逐批修改、验证证据和面试讲解记录。

## 2. 阶段命名说明

最初的 `PROJECT_PLAN.md` 使用以下阶段：

| 原计划阶段 | 内容 |
|---|---|
| Phase 1 | Webhook、Diff、单模型审查、Summary Review |
| Phase 2 | 可靠性、状态机、重试、行级评论 |
| Phase 3 | Tree-sitter、语法上下文、分片 |
| Phase 4 | Planner、专业 Reviewer、Validator、Aggregator |
| Phase 5 | 评测、报告和作品化 |

实际实施顺序发生了合理调整：

- Phase 2 中的幂等、状态机、重试、Head SHA 保护已经完成；
- Phase 2 的行级评论尚未完成；
- Phase 3 的 Tree-sitter 和语法分片尚未开始；
- Phase 5 的评测基础被提前完成，用于给 Prompt 和 Agent 改造提供回归基线；
- 接下来准备进入原计划 Phase 4 的 Agent 能力。

因此后续统一称为“Agent 化阶段”或“Multi-Agent Phase”，不再把它简称为 Phase 2，避免和原计划混淆。

## 3. 当前系统的准确定位

当前系统是：

> 一个真实 GitHub App 联调通过、具备幂等和故障恢复、能够调用 DeepSeek 生成结构化结果并发布 Summary Review、拥有离线质量基线的单 Agent 代码评审系统。

当前系统不是：

- 多 Agent 系统；
- 行级代码评论系统；
- AST 或调用图驱动的静态分析系统；
- LLM Exactly Once 系统；
- 已完成生产性能压测的服务；
- 已证明在真实项目分布上具备某个准确率的产品。

## 4. 当前执行链路

```text
GitHub pull_request event
          │
          ▼
Webhook signature + payload validation
          │
          ▼
Delivery idempotency + Review Task idempotency
          │
          ▼
Celery reviews.run
          │
          ▼
Fetch PR + changed files + verify head SHA
          │
          ▼
File filtering + patch budget + ReviewContext
          │
          ▼
ReviewPrompt v2 → one LLM call
          │
          ▼
Schema and business validation
          │
          ▼
Persist one ReviewReport and Findings
          │
          ▼
Recheck head SHA + idempotent publication
          │
          ▼
GitHub Summary Review
```

当前模型阶段的实际关系是：

```text
ReviewPipelineService
        → AIReviewService
            → ReviewPromptBuilder
                → LLMProvider
```

`ReviewPipelineService` 目前直接依赖具体的 `AIReviewService`，还没有统一的 `ReviewEngine` 或 Agent Orchestrator 边界。

## 5. 已经实现的能力

### 5.1 GitHub 与异步主链路

- GitHub App Installation Token；
- Webhook Secret 验签；
- `opened`、`synchronize`、`reopened` 事件；
- PR 元数据与 Changed Files 获取；
- FastAPI 快速接收，Celery 异步执行；
- MySQL 持久化任务、文件、报告、Finding 和 Publication；
- GitHub Summary Review 发布；
- 新 Commit 根据新 Head SHA 创建新评审任务。

### 5.2 幂等与一致性

- Webhook 唯一身份：`provider + delivery_id`；
- Review Task 唯一身份：`repository + PR number + head SHA`；
- 每个 Task 只允许一个 Report；
- 每个 Report 和 Provider 只允许一个 Publication；
- 应用层快速复用与数据库唯一约束共同兜底；
- 发布前重新获取 PR 并校验 Head SHA；
- GitHub Review Marker 用于远程对账，避免本地状态丢失后重复发布。

### 5.3 重试与阶段级恢复

- Timeout、连接错误、HTTP 429、HTTP 500/503 可重试；
- HTTP 401/403 和非法结构化输出不可重试；
- 指数退避和随机抖动；
- `Retry-After` 优先于默认退避；
- Report 已保存后，Publication 重试不会重新调用模型；
- 终态任务不会被重复执行。

系统保证业务结果幂等，不保证 LLM Exactly Once。如果模型已收到请求但客户端在响应返回前超时，重试可能产生第二次模型调用。

### 5.4 模型 Provider 与 Prompt

- Mock、OpenAI Responses、DeepSeek Responses Provider；
- 自定义 Base URL、API Key 和 Model；
- 结构化输出与 Pydantic 校验；
- Provider 错误分类；
- Input、Output、Total Token Usage；
- Prompt v2 的证据约束、Prompt Injection 边界、Testing 降噪和 Severity Rubric。

### 5.5 评测与回归

- 10-case `seed-v1` 开发集；
- 10-case `holdout-v1` 冻结验证集；
- 修复已知 Fixture 的 `holdout-v2` corrected regression set；
- Mock evaluator self-test；
- DeepSeek live baseline；
- Dataset SHA-256、Git Commit、Prompt、Model 元数据；
- Matcher v1/v2；
- 离线 Rescore；
- Baseline Comparator；
- `--case-id` 精确 partial trial；
- Recall、Precision、F1、False Positive、Category、Severity、Schema、Case Pass、Latency 和 Usage。

## 6. 已验证结果

### 6.1 自动化证据

截至进入 Agent 化阶段前：

```text
Eval tests: 51 passed
All tests: 217 passed
Ruff: passed
mypy app evals: passed
git diff --check: passed
```

### 6.2 真实集成证据

- Docker Compose 中 API、MySQL、Redis、Worker 正常；
- 真实 GitHub PR 完成 Webhook → Worker → DeepSeek → GitHub Review；
- 同一 PR 新 Commit 能重新评审；
- Webhook Redelivery 不重复创建任务、调用模型或发布 Review；
- Timeout、429、500/503、401 和非法结构化输出故障场景已验证；
- Report 已持久化后的 Publication 恢复不会重新调用模型；
- `holdout-v2` 修复后的路径 containment negative case 使用真实 DeepSeek 返回零 Finding。

### 6.3 Prompt 回归结果

相同 `seed-v1` 上的单次真实结果：

| 指标 | Prompt v1 | Prompt v2 |
|---|---:|---:|
| Recall | 1.0 | 1.0 |
| Precision | 0.4444 | 1.0 |
| False Positives | 10 | 0 |
| Negative Sample Pass Rate | 0.0 | 1.0 |
| Severity Match | 0.25 | 1.0 |
| Case Pass Rate | 0.1 | 1.0 |

这是小型开发集上的单次结果，不能外推为生产准确率。

### 6.4 Holdout 审计结果

`holdout-v1` 的原始自动 Recall 为 0.75。人工审计确认：

- 8/8 个目标问题实际都被模型识别；
- 两个自动漏报来自 Matcher v1 无法识别等价措辞；
- 一个自动误报来自 negative fixture 缺少 Import；
- Category 人工确认 7/8；
- Severity 人工确认 5/8。

Matcher v2 对同一批历史输出的离线 Rescore：

| 指标 | Matcher v1 | Matcher v2 |
|---|---:|---:|
| Recall | 0.75 | 1.0 |
| Precision | 0.6667 | 0.8889 |
| F1 | 0.7059 | 0.9412 |
| False Positives | 3 | 1 |
| Case Pass Rate | 0.3 | 0.5 |

剩余的一条自动 False Positive 是模型正确发现的 Fixture 缺陷，没有通过修改 Matcher 隐藏。

## 7. 当前限制

### 7.1 模型和上下文限制

- 所有类别共用一个 General Prompt；
- 一个模型调用同时承担发现问题、分类、严重程度和去重；
- 只提供 PR 元数据和 Patch，没有完整文件、符号或调用图；
- 没有 Tree-sitter 函数/类级上下文；
- 没有跨文件依赖分析；
- 超大 Patch 只能按字符预算过滤，不能语法感知分片。

### 7.2 输出和发布限制

当前 Finding 只有：

```text
title
explanation
suggestion
file_path
severity
category
confidence
```

目前缺少：

- `start_line/end_line`；
- 独立 `evidence`；
- `agent_role`；
- Finding 指纹；
- 验证状态；
- GitHub Diff Position。

因此当前只发布 Summary Review，不能可靠发布 Inline Comment。

### 7.3 可观测性与成本限制

- Token Usage 目前主要进入 Eval，没有生产级 Agent Call 表；
- 没有版本化价格快照，Cost 为 `null`；
- 没有 Agent 级 Latency、错误率和贡献度；
- 没有真实并发吞吐和生产 P50/P95；
- 没有用户反馈闭环。

## 8. 为什么考虑多 Agent

多 Agent 不是让系统“看起来更像 Agent 项目”，而是把一个职责过重的模型调用拆成可独立设计、评测和恢复的专业步骤。

当前 General Prompt 同时要求模型关注：

- correctness；
- security；
- reliability；
- performance；
- maintainability；
- evidence；
- severity；
- confidence；
- 去重和结构化输出。

这些目标会竞争模型注意力。明显的 correctness 问题可能压过需要威胁建模的安全问题；模型也可能发现了正确问题，但 Category 或 Severity 不稳定。

多 Agent 希望把任务拆为：

```text
ReviewContext
      │
      ▼
Deterministic Planner
      │
      ├── Correctness Reviewer
      ├── Security Reviewer
      ├── Performance Reviewer
      └── Maintainability Reviewer
                 │
                 ▼
              Validator
                 │
                 ▼
              Aggregator
                 │
                 ▼
         Existing ReviewReport
```

现有 Report 持久化、Publication 和 GitHub 发布链路应继续复用，不应因为多 Agent 重写整条系统。

## 9. 预期收益：需要评测验证的假设

| 方面 | 当前瓶颈 | 多 Agent 假设 |
|---|---|---|
| 专业召回 | 一个 Prompt 同时关注多个目标 | 专业 Reviewer 更容易发现细分类问题 |
| 安全审查 | 通用 Prompt 缺少持续威胁视角 | Security Reviewer 专注输入边界、鉴权和注入 |
| 性能审查 | 容易被明显正确性问题抢占注意力 | Performance Reviewer 专注复杂度、阻塞和资源消耗 |
| Category | Holdout 已出现分类偏移 | Validator 使用统一标签规则重新检查 |
| Severity | Holdout 已出现严重程度上调 | Validator 使用统一影响范围和前置条件校准 |
| 误报 | 每次调用自己决定证据是否充分 | Validator 过滤无直接证据的 Finding |
| 重复意见 | 多 Agent 可能发现同一根因 | Aggregator 使用指纹和文本相似度去重 |
| 可解释性 | 只能看到一个最终 Report | 记录 Agent Role、验证和聚合决策 |
| 可评测性 | 只能评整体 Prompt | 分别统计各 Agent 的召回、误报、Token 和贡献 |
| 可扩展性 | 修改一类规则影响统一 Prompt | 可以独立升级某个专业 Reviewer |

这些都是待验证假设，不是当前成果。只有在冻结数据或新未见数据上证明质量收益，才能写入简历。

## 10. 多 Agent 的成本和风险

### 10.1 成本与延迟

- 调用次数增加通常会增加总 Token；
- 并行可能降低墙钟时间，但不会降低总费用；
- 更多并发会提高 Provider 429 概率；
- Validator 和 Aggregator 如果也调用 LLM，会进一步增加成本和不确定性。

第一版优先使用确定性 Planner、Validator 和 Aggregator，只让 Reviewer 调用模型。

### 10.2 重复和冲突

- 多个 Reviewer 可能报告同一问题；
- 相同问题可能使用不同 Category；
- Severity 和 Confidence 可能冲突；
- 一个 Agent 的建议可能与另一个 Agent 矛盾。

必须先定义 Finding Identity、去重优先级和冲突规则，不能简单拼接输出。

### 10.3 重试费用放大

当前恢复单位是完整 Report。如果四个 Agent 中前三个成功、第四个超时，而中间结果没有持久化，Celery 重试会重新调用前三个 Agent。

因此生产多 Agent 上线前必须考虑：

- Agent Execution 唯一身份；
- 每个 Agent 的状态和 attempt count；
- 单 Agent 结果持久化；
- 单 Agent Usage 和错误码；
- 重试时只执行未完成 Agent；
- Aggregation 的幂等身份；
- 部分失败是否允许发布 `PARTIAL`。

## 11. 设计原则

1. **先兼容再扩展**：第一批只有 General Agent，生产行为和模型调用次数不变。
2. **先确定性后智能化**：Planner、Validator、Aggregator 第一版尽量使用规则，不额外调用 LLM。
3. **先离线评测再接生产**：专业 Agent 先通过 Eval 运行，不直接替换 Worker 主链路。
4. **结果可追溯**：Finding 应能追溯到 Agent Role、Prompt Version 和 Model。
5. **成功结果可恢复**：生产多 Agent 调用前先解决 Agent 级持久化。
6. **预算是输入，不是事后统计**：限制最大 Agent 数、Token 和并发。
7. **允许降级**：Planner 或专业 Agent 不可用时能够回退 General Agent。
8. **不破坏现有可靠性**：Report、Publication、Head SHA 和远程对账继续复用。

## 12. 分批实施顺序

### 第一批：Agent 架构接缝

目标：改变结构，不改变行为。

实施状态：已于 2026-09-26 完成。生产链路当前通过单个 General Agent 运行，仍然只有一次模型调用。

- 新增 `ReviewEngine` Protocol；
- 新增 `ReviewAgent` Protocol 和 `AgentRole`；
- 用 `GeneralReviewAgent` 包装现有 `AIReviewService`；
- 新增 `ReviewOrchestrator`；
- 新增单 Agent passthrough Aggregator；
- `ReviewPipelineService` 改为依赖 `ReviewEngine`；
- Pipeline Factory 组装一个 General Agent；
- 仍然只有一次 LLM 调用。

本批完成后仍不能声称多 Agent 已经完成。

### 第二批：专业 Agent 和 Planner

实施状态：已于 2026-09-26 完成，当前仅接入 Eval，生产仍使用单 General Agent。

- Correctness、Security、Performance、Maintainability Agent；
- 每个 Agent 使用独立 Prompt Version；
- Agent Registry；
- Deterministic Planner；
- Planner 根据 Patch、路径和文件类型选择 Agent；
- 先只接 Eval，不替换生产 Pipeline。

实际实现补充：

- Correctness Agent 同时承担第一版 reliability 审查；
- `AgentReviewResult` 保留 role、report 和 usage；
- Eval 支持 `--agent-role` 隔离运行单个专业 Agent；
- Eval 支持无模型调用的 `--plan-only` 路由审计；
- 历史 baseline 缺少 `agent_role` 时默认解释为 General；
- 尚未执行多 Agent 合并；已完成 6 次真实专业 Agent partial trial，但尚未运行完整专业 Agent baseline。

完整 Seed 路由审计曾发现一个语义碰撞：数据去重案例中的 `duplicates` 误触发 Maintainability。规则已收紧为明确的业务规则/代码重复短语，并用负向断言保证该案例只选择 Correctness 和 Performance。修复后 10 个 Seed 案例的候选调用数从 17 降为 16。

真实 DeepSeek 验收随后按单角色、单案例执行，避免在 Aggregator 尚未设计时混入多结果行为：

| 试跑范围 | 结果 |
|---|---|
| 四个专业角色的代表正例 | 4/4 Finding 命中，0 False Positive，Category/Severity 全部匹配 |
| Correctness 与 Security 负例 | 2/2 保持 0 Finding |
| 稳定性 | 6/6 `successful_case`、Schema 合法、Case Pass |
| Usage | 6574 Input + 1965 Output = 8539 Total Tokens |
| 同案例 General v2 | 8205 Total Tokens；专业单角色增加 334 Tokens，约 4.1% |

这些运行绑定同一代码提交和 Dataset SHA，但因与试跑无关的个人文档改动导致 `git_dirty=true`，而且使用的是已参与开发的 Seed 案例。因此它们只能作为真实 Provider、角色职责与 Usage 链路的 partial-trial 证据，不能登记为完整 baseline，也不能据此声称多 Agent 已提升泛化质量。一次性总延迟虽低于相同案例的历史 General 结果，也不能排除网络波动。

### 第三批：Validator 和 Aggregator

实施状态：Validator/Aggregator 第一阶段与 Eval-only Runner 第二阶段均已完成，尚未接入生产 Worker。

- 已完成文件路径、文本字段和 Confidence 的聚合边界验证；
- 已完成基于规范化 File、Title 和 Explanation 的 SHA-256 Finding 指纹；
- 已完成同文件文本相似匹配和保守 complete-link 分组；
- 已完成跨 Agent duplicate 去重；
- 已完成 Category、Severity 冲突审计；
- 已完成职责匹配、Confidence、内容完整度和固定 tie-break 的规范 Finding 选择；
- 已完成确定性 Summary、Finding 排序和 Risk Level；
- 已记录 `kept`、`merged` 和冲突原因；
- 尚未持久化审计结果，也没有行号范围参与身份判断。

第二阶段实际实现：

- `PlannedReviewOrchestrator` 串联 Planner、Registry、专业 Agent 和 Aggregator；
- 按 Planner 固定顺序执行 Agent，并保留每个 Role、Prompt Version、Latency 和 Result；
- Agent 失败保留失败角色和已完成执行，Aggregation 失败保留全部 Agent Result；
- Eval CLI 新增 `--multi-agent`，默认 Mock 且不加载真实 Provider；
- 真实多 Agent 必须显式 `--provider deepseek --live --multi-agent`；
- `--multi-agent` 与 `--agent-role`、`--plan-only` 和 `--save-baseline` 互斥；
- Eval 记录聚合前后 Finding、Merged Count、Conflict、Duplicate Rate 和 Agent Contribution；
- 所有 Agent Usage 完整时才汇总 Token，缺失时保持 `null`；
- 历史 Eval/Baseline 缺失 `execution_mode` 时继续解释为单 Agent。

第三阶段第一次真实 DeepSeek partial trial 暴露了 Identity v1 漏合并：Correctness 与 Security 对同一个 SQL f-string 根因分别生成一个 Finding，但标题改写幅度较大，旧 token containment 没有把它们归为一组。结果是聚合前后均为 2 条、`merged=0`、评测产生 1 个 False Positive。

Finding Identity v2 的修复规则：

- 保留原有高阈值 token containment；
- 新增“同文件 + 至少两个共同信号词 + 共同非通用 bigram + Explanation 中相同 code span”的保守路径；
- 不使用 Category、Severity、Confidence 或常见 Suggestion 判定身份；
- 新增同一 SQL 代码上的注入与缺少索引负例，防止过度合并；
- 聚合与 Eval 输出显式记录 `finding-text-v2`；
- 历史 baseline 缺失该字段时保持 `null`。

提交 `dd95c4b` 后用同一个真实 DeepSeek 案例重验，Correctness 与 Security 均成功返回一个 Finding。Identity v2 将聚合结果从修复前的 `2 -> 2` 改为 `2 -> 1`，`merged=1`、Duplicate Rate=`0.5`、False Positive=`0`、Case Pass=`true`。Aggregator 保留 Security Finding，Correctness Finding 被标记为 merged；总计 3685 Tokens、约 8.55 秒。

本次重验还暴露一个审计缺口：`aggregation_conflict_count=1`，但 Eval JSON 没有输出冲突字段、观察值、选择原因或每个 Agent 的原始 Finding，所以不能判断冲突来自 Category 还是 Severity。下一步先补齐这些聚合审计快照，再扩展混合缺陷数据集。

关键设计边界：

- Category、Severity 不参与 Finding 匹配，避免标签冲突被匹配器掩盖；
- Severity 不直接取最大值，避免单个 Agent 的严重程度膨胀污染最终风险；
- 非法 Finding 采用 fail-closed，不静默丢弃；
- 生产继续使用 `PassthroughReviewAggregator`，所以数据库和 GitHub 发布行为不变。

### 第四批：Agent 级持久化与恢复

- Agent Execution ORM 和 Migration；
- Agent 状态、attempt count、usage、latency 和错误码；
- 已成功 Agent 结果复用；
- 部分失败恢复；
- Aggregation 幂等；
- 明确 `PARTIAL` 策略。

### 第五批：并发与预算

- 并行执行独立 Reviewer；
- 最大并发和最大 Agent 数；
- Token Budget；
- Provider Rate Limit；
- 超时与取消传播；
- General Agent 降级策略。

### 第六批：生产切换和评测

- 单 Agent 与多 Agent 离线 A/B；
- 新未见数据，避免继续调优现有 Holdout；
- 真实 Provider 小流量试跑；
- 回归阈值；
- 满足质量、成本和可靠性门槛后再切换 Worker Factory。

## 13. 评测计划

多 Agent 至少需要比较：

| 指标 | 目的 |
|---|---|
| Recall | 是否发现更多真实缺陷 |
| Precision / False Positives | 专业 Agent 是否带来更多噪声 |
| Category Match | 专业分类是否更稳定 |
| Severity Match | Validator 是否减少严重程度膨胀 |
| Case Pass Rate | 严格整体质量是否提高 |
| Schema Validity | 多输出链路是否稳定 |
| Total Tokens | 质量收益需要多少额外成本 |
| Latency P50/P95 | 并发是否真正降低墙钟时间 |
| Agent Contribution | 每个 Agent 提供了多少独有有效 Finding |
| Duplicate Rate | 聚合前后重复意见数量 |
| Partial Recovery | 单 Agent 失败是否会重复调用成功 Agent |

比较必须满足：

- 相同 Dataset SHA；
- 固定 Model 和 Provider 参数；
- 明确 Prompt/Agent/Planner/Aggregator Version；
- 原始输出和聚合输出都可审计；
- 不用 Mock 结果证明模型质量；
- 不把一次运行外推为稳定结论。

现有 `seed-v1` 可以用于开发回归，现有 Holdout 已被开发流程看到。对多 Agent 的泛化结论需要新的未见案例或真实 PR 样本。

## 14. Agent 化阶段完成标准

多 Agent 不能以“代码中存在多个 Agent 类”作为完成标准。至少应满足：

- Planner 对不同变更选择不同 Reviewer；
- 每个 Reviewer 有独立职责和 Prompt Version；
- 同一根因不会发布多次；
- 标签冲突有确定性规则；
- 单 Agent 成功结果不会因其他 Agent 重试而丢失；
- 有全局和单任务预算；
- Worker 仍具备 Head SHA 保护和 Publication 幂等；
- 有单 Agent vs 多 Agent 的真实模型评测；
- 文档明确质量收益、Token 代价和剩余限制。

## 15. 面试表达

### 当前可以说

> 我已经完成一个真实 GitHub App 接入的单 Agent PR 评审系统，重点实现了 Webhook 和任务幂等、错误分类、指数退避、Report 断点恢复、Publication 远程对账，以及带真实 DeepSeek baseline 的离线评测体系。在进入多 Agent 前，我先把模型质量、评测器误差和数据集缺陷分开审计，并为后续 Agent A/B 建立了可复现基线。

### 第一批 Agent 接缝完成后可以说

> 我把 Pipeline 从具体 AIReviewService 解耦为 ReviewEngine，并通过 GeneralReviewAgent 和 Orchestrator 兼容现有行为，为后续增加专业 Reviewer 建立稳定边界；这一阶段仍保持一次模型调用，没有提前声称多 Agent 提升质量。

### 只有真实完成和验证后才能说

- 实现动态 Agent 路由；
- 实现多 Agent 并行；
- Validator 降低了误报或 Severity 偏差；
- 多 Agent 提升了 Recall 或 Case Pass；
- Agent 级断点恢复避免了重复模型费用；
- 达到了某个 P50/P95、Token 或 Cost 指标。

## 16. 下一步

前三批当前已经实现：

```text
ReviewPipelineService
        → ReviewEngine
            → ReviewOrchestrator
                → GeneralReviewAgent
                    → existing AIReviewService

Eval only:
ReviewContext
        → DeterministicReviewPlanner
        → ReviewAgentRegistry
        → selected SpecialistReviewAgents (sequential)
        → DeterministicReviewAggregator
        → evaluated ReviewReport + execution metadata
```

生产链路仍通过一个 General Agent 和 `PassthroughReviewAggregator` 调用一次模型；上面的多 Agent 流程只存在于 Eval，不改变数据库和 GitHub 发布行为。

自动化验收结果为 289 项全量测试通过，Ruff 通过，mypy 对 `app` 与 `evals` 的 98 个源码文件通过。测试覆盖 Identity v2/v3 的真实同义输出、相同代码上的不同根因、原始 Agent Findings、kept/merged 决策、Category/Severity 冲突明细、聚合挑战集和历史 baseline 兼容。

真实集成方面，四个专业角色已经完成 6 次显式 DeepSeek partial trial：四个正例全部命中、两个负例保持 0 Finding，6/6 Case Pass，Usage 元数据完整。试跑没有发布 GitHub Review，也没有改变生产 Worker；它证明真实 Provider 下的职责隔离链路可用，但不是完整 baseline 或生产多 Agent 证据。

真实 DeepSeek 多 Agent SQL 注入试跑形成了三个阶段的 partial trial：Identity v1 在 commit `e6f43d0` 上未合并同根因输出；Identity v2 在 commit `dd95c4b` 上完成 `2 -> 1` 合并并恢复 Case Pass；聚合审计版本在 commit `5d2e66d` 上进一步记录两份原始 Findings、Security `kept`、Correctness `merged` 和共同规范指纹。第三次结果为 False Positive=0、Case Pass=true、3637 Tokens、约 7.83 秒。三次都只执行已见 Seed 单案例，且工作区因个人文档显示 dirty，所以不是 baseline，不能外推稳定成本、延迟或总体质量。

Eval 现在已经输出 Aggregation Conflict 明细、原始 Agent Findings 和 kept/merged 决策，并拥有混合缺陷聚合挑战集。下一步先提交 Identity v3，再用相同路径/资源案例做真实重验；之后设计 Agent Execution 持久化、`PARTIAL` 策略、并发和预算。当前仍不把多个 Agent 的输出接入生产发布链路。

## 17. 聚合审计快照

只保存最终聚合 Finding 会丢失多 Agent 决策过程。现在每个 Eval Case 同时保存三层证据：

1. `agent_executions[].findings`：每个专业 Agent 的原始结构化输出；
2. `aggregation_decisions`：每条输入 Finding 是 `kept` 还是 `merged`，以及来源角色、规范指纹和原因；
3. `aggregation_conflicts`：Category/Severity 的观察值、最终选择值、规范指纹和原因。

最终 `actual_findings` 仍是去重后的发布候选，Matcher 只对它评分。这样审计信息不会改变 Recall、False Positive、Category Match 或 Severity Match 的定义。旧 baseline 缺少这些字段时默认加载为空，不重写历史事实。

自动化测试覆盖无冲突合并、Category/Severity 双冲突、失败前已完成 Agent 的原始输出保留，以及历史 baseline 兼容。

提交 `5d2e66d` 后的真实 DeepSeek 重验验证了新审计格式：Correctness 与 Security Agent 各生成一个 SQL 注入 Finding，二者 Category/Severity 都为 `security/high`；Aggregator 按角色匹配和置信度保留 Security 的 `0.97` Finding，合并 Correctness 的 `0.95` Finding，并用同一个规范指纹关联两条决策。最终 `2 -> 1`、Duplicate Rate=`0.5`、Case Pass=true、False Positive=0，总计 3637 Tokens，墙钟时间约 7.83 秒。

这次没有标签冲突，因此 `aggregation_conflicts` 合理地为空；旧试跑中的单个 Conflict 没有复现，不能从本次结果反推旧字段。两个 Agent 的 `unique_finding_count` 都为 0，说明这个案例验证了去重和审计，而没有提供 Recall 增益证据。运行是 dirty-worktree partial trial，不是模型 baseline。

这仍是 Eval-only 能力：审计快照没有进入 ORM、数据库或 GitHub Review，生产仍运行单 General Agent。模型原始 Finding 可能包含短代码证据，保存实验 JSON 时应按普通评审输出管理，不能将其当作无敏感内容的纯指标文件。

## 18. 多缺陷聚合挑战集

原有 Seed 与 Holdout 主要验证单个目标缺陷，不足以覆盖多 Agent 聚合最危险的两类错误：同一根因没有合并，以及不同根因被过度合并。新增的 `aggregation-v1` 是开发挑战集，不是 Holdout，包含：

1. 同一循环查询上的 SQL 注入与 N+1，并让 Correctness/Security 对注入产生标签冲突；
2. 同一 `open(...)` 表达式上的路径穿越与文件句柄未关闭；
3. 跨文件的 N+1 与重复业务规则；
4. 会触发 Security 路由但实现正确的参数化查询负例。

默认 Mock 全集离线运行会执行 10 次规划后的 Agent 调用，将 16 条重复 Fixture Finding 聚合成 6 条期望 Finding，4/4 Case Pass、0 False Positive。测试专用 Role-aware Provider 则更接近聚合边界：重叠案例从 `3 -> 2`，SQL 注入完成一次合并，N+1 保持独立，并产生可审计的 Category/Severity 双冲突；相邻路径与资源问题保持 `2 -> 2`；跨文件两个专业角色各贡献一条独有 Finding；负例保持 `0 -> 0`。

复杂场景还对 Correctness、Security、Performance 三个结果的全部排列做了验证，最终 Report、Decisions、Conflicts 和 Source Roles 均保持一致。现有 Aggregator 通过挑战，因此本批没有调整 Identity 阈值或生产逻辑。

这批证据全部来自自动化测试，没有调用 DeepSeek。`aggregation-v1` 已被开发过程看到，不能作为泛化质量证明；后续真实 partial trial 也需要明确记录 Token、Latency、Git 状态和 partial 非 baseline 边界。

## 19. Finding Identity v3

`aggregation-adjacent-path-and-resource` 的第一次真实 DeepSeek 运行暴露了 v2 的新漏合并。Correctness 输出“Unvalidated filename allows escaping the upload root”，Security 输出“Path traversal / arbitrary file read in read_upload”；两者引用相同 ``UPLOAD_ROOT / filename``，Category/Severity 也同为 `security/high`，但标题词汇差异过大，导致聚合结果为 `3 -> 3`，其中一条路径穿越成为评测 False Positive。

Identity v3 保留旧规则，并增加 Explanation fallback：

- 文件必须相同；
- 两段 Explanation 必须包含完全相同且足够长的 inline code span；
- 移除 inline code 后，必须至少共享四个非通用根因信号词；
- 还必须共享一个连续的 prose signal bigram；
- Category、Severity、Confidence 和 Suggestion 不参与判断。

真实输出被固化为端到端 Eval 回归：三条原始 Finding 聚合为路径穿越和资源泄漏两条，Security 路径 Finding 被保留，Correctness 路径 Finding 被合并，Correctness 的资源 Finding 保持独有。SQL 注入/缺少索引与路径穿越/资源泄漏反例继续证明相同代码证据本身不足以触发合并。

修复前真实试跑绑定 commit `2991ae8`，使用 4643 Tokens、约 13.07 秒，Recall=1.0 但 Case Pass=false。v3 当前只通过自动化测试，尚未重新调用真实 DeepSeek；提交后需要使用同一 Dataset SHA 和 Case ID 重验，才能确认真实输出下恢复 `3 -> 2`。
