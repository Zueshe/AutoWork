# 阶段 0 交付物 · 核心领域模型草案与阶段 1 API 契约

版本：V0.1（评审草案）  
日期：2026-10-18  
状态：**待评审**（评审要点见 §9；评审通过后改为 V1.0「已评审」）  
依据：[设计方案](company-ai-console-design-v1.md) §7 / §12 / §13 / §14.3；[开发计划](development-plan-quality-gates.md) §10（阶段 1 范围）、§9.1（阶段 0 交付物）  
边界：本文只覆盖阶段 1 要落地的六个核心实体与 9 个接口。执行、发布、资源、试点的完整模型在阶段 2/3 扩展，但相关字段与枚举**现在就冻结**，避免阶段 3 重建（设计文档 §16.1 的明确要求）。

> **引用约定：** 本文内部小节写 `§x.y`；引用[设计方案](company-ai-console-design-v1.md)写「设计文档 §x」；引用[开发计划](development-plan-quality-gates.md)写「开发计划 §x」。三份文档都有 §4–§9，未标注文档名的引用一律指本文。

## 1. 建模原则

从设计文档提炼六条，作为本文所有取舍的判据；评审时若对某个字段有疑问，先回到这张表。

| # | 原则 | 来源（设计文档） | 落到模型上意味着 |
| --- | --- | --- | --- |
| 1 | 业务记录与执行记录分离 | §7.1 | WorkItem 与 ExecutionRun 是两条独立主线，用 `plan_ref` 关联，**不用状态覆盖** |
| 2 | 可审批对象不可变且版本化 | §7.5 | Revision 表只追加；审批绑定「对象 + 版本 + 内容摘要」 |
| 3 | 派生内容继承来源限制 | §14.3 | 受众默认取来源可见范围的**交集**（不是并集）；来源变化触发重评/撤回 |
| 4 | 外部动作有代次与幂等键 | §6.9 / §7.7 | 阶段 1 不启用，但字段与唯一约束现在就留 |
| 5 | 审计只追加、可追溯 | §14.3 | AuditEvent 不参与业务外键；**读取受限数据也写审计** |
| 6 | 状态只能由规则迁移 | §7.1 | 接口不接收 `status` 字段；迁移集合在服务端枚举 |

## 2. 实体关系总览

```text
System ──1:N── WorkItem ──1:N── WorkItemSupplement（补充，只追加）
                    │
                    ├─N:M─ WorkItemRelation（重复 / 父子 / 依赖）
                    │
                    ├─1:N── Evidence ──────────┐（带 access_scope 与来源引用）
                    │                          │
                    ├─1:N── AnalysisRevision ──┘（不可变；事实/假设/排除项分列）
                    │            └─1:N── PublishedSummary（派生内容，受众取交集）
                    │                        └─ ContentPublication（发布/撤回动作）
                    │
                    ├─1:N── DeliveryPlanRevision ──> DeliveryScope ──> ExecutionControl
                    │                                      └── ExecutionRun ──> StepRun ──> Operation
                    ├─1:N── Approval（绑定不可变对象 + 摘要）
                    └─1:N── Handover

AuditEvent / Outbox / Inbox：横切表，不参与业务外键
阶段 1 只写实线部分；虚线（计划、执行、交接）表结构先建，数据与接口按 §7 的形态处理
```

## 3. 六个核心实体

### 3.1 WorkItem（工单）

**目的：** 描述"要解决什么"。同一工单可以有多次分析与执行，**失败记录不覆盖**。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | text | PK | 形如 `BUG-2026-0108`，类型前缀 + 年 + 序号，对外可见 |
| `kind` | enum | bug / requirement / usage | 与 `id` 前缀一致，服务端校验 |
| `system_id` | text | FK→System，可空 | **允许为空**（"暂不确定"，由分流确认） |
| `title` | text | ≤ 200 | |
| `description` | text | | 原始提交，**提交后不可改写** |
| `status` | enum | 见 §3.1.1 | 只能由服务端迁移 |
| `status_reason` | text | 可空 | 待补充/阻塞/退回的原因 |
| `requester_id` | text | FK→User | |
| `owner_id` | text | FK→User，可空 | 未分派时为空 |
| `priority` | enum | P1–P4 | |
| `visibility_scope` | text | 非空 | 可见范围标签；与提交人组织范围相关 |
| `current_analysis_revision_id` | text | FK，可空 | 指向当前有效分析版本 |
| `version` | integer | 非空，默认 1 | 乐观锁，写接口 `If-Match` 比对 |
| `created_at` / `updated_at` / `closed_at` | timestamptz | | |

**不变量**

1. `description` 提交后不可变；补充信息一律走 WorkItemSupplement（只追加）。
2. `status` 只能按 §3.1.1 迁移；非法迁移返回 409 而不是静默忽略。
3. `system_id` 为空时不得进入"已批准"及之后的任何状态（阶段 2 起生效）。

#### 3.1.1 状态机与阶段 1 子集

完整状态集（设计文档 §7.1）：
`已提交 → 分流中 → 分析中 → 待方案审批 → 已批准 → 实施中 → 待业务验收 → 已关闭`
分支状态：待补充、重复并关联、转使用支持、暂缓、拒绝、等待外部、已取消、实施阻塞 / 重新规划。

| 迁移 | 阶段 1 | 说明 |
| --- | --- | --- |
| 已提交 → 分流中 | ✅ | 写入时自动进入 |
| 分流中 → 分析中 | ✅ | 归属确认后 |
| 分析中 → 待补充 | ✅ | 信息不足，需提交人补充 |
| 待补充 → 分析中 | ✅ | 收到补充后 |
| 分析中 → 已关闭 | ✅ | 仅限"重复并关联"或"转使用支持"→ 关闭 |
| 已提交 / 分流中 → 已取消 | ✅ | 仅限提交人本人 |
| 分析中 → 待方案审批 及之后全部迁移 | ❌ 阶段 2 | 阶段 1 审批只读（§7 范围表） |

> **本条是阶段 1 最容易被误解的地方**：试点用户在阶段 1 看不到"等待审批"。界面上必须用固定文案说明"分析完成后由责任团队线下推进"，不能让用户以为流程卡住（对应开发计划 §10.3 的固定进度文案约定）。

### 3.2 Evidence（证据）

**目的：** 让分析结论"有据可依"。每条证据都必须能回答：从哪来、什么时候取、查询条件是什么、给谁看。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | text | PK | |
| `work_item_id` | text | FK→WorkItem | |
| `source_type` | enum | connector / manual / attachment | |
| `source_ref` | text | 非空 | 仓库、日志流、工单或附件标识 |
| `connector_revision` | text | 可空 | 取自哪一版能力清单（来源固定，设计文档 §6.1） |
| `query` | jsonb | 可空 | 实际查询条件，保证可复现 |
| `captured_at` | timestamptz | 非空 | 证据的**时间点**，不是入库时间 |
| `snapshot_ref` | text | 可空 | 对象存储引用 |
| `checksum` | text | 可空 | 快照校验值 |
| `summary` | text | 非空 | 展示用摘要（正文按权限裁剪，敏感字段不入库） |
| `access_scope` | text | **非空** | 来源可见范围；派生内容据此取交集 |
| `sensitivity` | enum | public / internal / restricted | 数据分级，决定能否进入模型上下文（D8） |
| `retention_until` | timestamptz | 可空 | |
| `status` | enum | active / superseded / revoked | 删除源证据时置 revoked |

**不变量**

1. `access_scope` 与 `sensitivity` **不允许为空**——缺这两个字段的证据无法参与派生内容计算。
2. `status = revoked` 后不得再被新的 AnalysisRevision 引用。
3. `checksum` 存在时必须能与 `snapshot_ref` 对应（写入时校验，读时抽查）。

### 3.3 AnalysisRevision（分析版本）

**目的：** 承载"事实 / 假设 / 已排除"三类结论，**提交后不可变**。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | text | PK | |
| `work_item_id` | text | FK→WorkItem | |
| `revision_no` | integer | `(work_item_id, revision_no)` 唯一 | 从 1 递增 |
| `status` | enum | draft / submitted / superseded | **同一工单至多一个 submitted** |
| `internal_summary` | text | | 内部分析结论（受限） |
| `facts` | jsonb[] | 默认 `[]` | 已确认事实，每条带证据引用 |
| `assumptions` | jsonb[] | 默认 `[]` | 待验证假设 |
| `excluded` | jsonb[] | 默认 `[]` | 已排除的候选原因 |
| `evidence_refs` | text[] | 默认 `{}` | 只能引用 active 的 Evidence |
| `tool_runs` | jsonb[] | 默认 `[]` | 检索/查询工具与版本（可追溯） |
| `model_version` | text | 可空 | AI 未接入时为空 |
| `created_by` | text | FK→User | |
| `created_at` / `submitted_at` | timestamptz | | |

**不变量**

1. `status = submitted` 后**任何字段不可变**；更正只能新建 `revision_no + 1`，并把旧版置 `superseded`。
2. 同一工单同时只允许一个 `submitted` 版本（用部分唯一索引强制，见 §5）。
3. `facts` 与 `assumptions` **必须分列**：设计文档 §7.3 要求区分已验证结果与未确定项，混在一起会让审批人误判。

### 3.4 PublishedSummary（派生内容）

**目的：** 面向更大受众的发布版本。**这是全模型里权限最容易出错的地方**，单独成节。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | text | PK | |
| `source_type` / `source_id` | text | 非空 | 来源对象（分析结论、摘要、评论、通知、测试报告、交接包） |
| `source_revision_no` | integer | 可空 | 来源版本，变化即需重评 |
| `content_version` | integer | 非空，默认 1 | 发布内容自身的版本 |
| `body` | text | 非空 | 发布正文；**发布后索引与预览都读这一份** |
| `audience_scope` | text | 非空 | 目标受众 |
| `inherited_scope` | text | 非空 | 服务端计算的**来源可见范围交集** |
| `publication_basis` | enum | approved_rule / per_revision_review | 发布依据类型，**必填** |
| `basis_ref` | text | 非空 | 指向批准规则或逐版审查记录 |
| `status` | enum | draft / published / pending_recheck / revoked | |
| `published_by` / `published_at` / `revoked_at` / `revoke_reason` | | | |

**不变量**

1. **默认 `audience_scope = inherited_scope`**，且 `inherited_scope` 由服务端对所有来源的 `access_scope` 取**交集**（不是并集）。
2. 只有当 `publication_basis = approved_rule` 且 `basis_ref` 指向的规则**明确覆盖**扩大范围时，`audience_scope` 才允许大于交集；否则拒绝写入（409）。
3. `publication_basis` 必须可解析到实际记录；模型自称"已脱敏"不构成依据（设计文档 §14.3）。
4. 来源 `revoked`、分级提高或 `access_scope` 收紧时，关联的 PublishedSummary 进入 `pending_recheck`，**不得继续沿用旧受众**；撤回复用时写入 `ContentPublication`。

**ContentPublication（动作记录）**：`id / summary_id / action(publish|revoke|recheck) / actor_id / basis_ref / created_at / external_delivery_ref`。已投递到外部渠道（邮件等）时记录投递引用，能撤回则撤回，不能撤回则留档。

### 3.5 Approval（审批）

**目的：** 把"谁、在什么对象版本上、批准了什么范围"固定下来。**阶段 1 只读**。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `approval_decision_id` | text | **PK（幂等键）** | 重复投递同 ID 不产生第二条决定 |
| `subject_type` | enum | plan / acceptance / release / resource | |
| `subject_ref` | text | 非空 | 不可变对象引用 |
| `subject_digest` | text | 非空 | 对象规范化摘要，绑定"批的是哪一版内容" |
| `decision` | enum | approved / returned / rejected / held | |
| `approver_id` / `approver_role` | text | 非空 | |
| `scope` | jsonb | 默认 `{}` | 批准范围（动作、环境、额度的上限） |
| `valid_until` | timestamptz | 可空 | 过期即不可用于执行 |
| `revoked_at` | timestamptz | 可空 | 撤权/离职导致 |

**不变量**

1. 同一 `approval_decision_id` 重复提交返回首次结果，**不重复生效、不重复触发副作用**（设计文档 §7.5.1）。
2. `subject_digest` 与对象当前摘要不一致时，该审批不可用于执行（阶段 2 在网关校验）。
3. 后续决定（如先退回后批准）是**新的一行**，通过 `created_at` 取当前有效决定，不覆盖历史。
4. 阶段 1 不开放写接口；表中数据来自种子，前端只读展示"待办"。

### 3.6 ExecutionRun（执行记录，阶段 1 只读占位）

**目的：** 表达"按哪个方案、在什么版本上做了什么"。阶段 1 不写入，但**结构先冻结**。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | text | PK |
| `delivery_scope_id` | text | 交付范围（并行子交付时各自独立） |
| `plan_ref` | text | 不可变计划版本引用 |
| `execution_epoch` | integer | **执行代次**：表示当前执行资格，不替代资源锁的 fencing |
| `status` | enum | 待调度 / 准备环境 / 实现 / 验证 / 待发布审批 / 发布中 / 观察中 / 成功 / 待人工处理 / 失败 / 已被替代 / 取消请求中 / 已取消 / 回滚中 / 已回滚 / 恢复处理中 |
| `baseline_ref` | text | 执行前基线 |
| `checkpoint` | jsonb | 检查点（重启后从此恢复） |
| `repair_rounds` | integer | 已用自动修复轮次（**不得被新执行或人工交接重置**） |
| `cost_minor` / `currency` | bigint / text | 累计成本（分），与预算同口径 |
| `started_at` / `ended_at` | timestamptz | |

**配套 RunControl（原设计中的 DeliveryScope / ExecutionControl）：**

| 字段 | 说明 |
| --- | --- |
| `delivery_scope_id` | PK；形如 `BUG-2026-0108/main` |
| `active_plan_ref` | 当前有效计划；**客户端不得自行指定** |
| `execution_epoch` | 单调递增；替代生效时在同一事务内 +1 |
| `mode` | auto / pausing / manual / blocked |
| `control_version` | 乐观锁；冻结与派发资格登记必须走同一版本检查 |
| `budget_used_minor` / `repair_count_used` | 同一范围的累计额度，**不因新执行清零** |

**为什么阶段 1 就要定：** 设计文档 §16.1 要求"阶段 1 就预留 deliveryKind、系统归属和计划版本字段，避免阶段 3 重建模型"。上面的 `active_plan_ref + execution_epoch + mode + control_version` 组合正是计划替代（设计文档 §7.5.1）的落地形态，晚定会导致 WorkItem 与执行两张表都要改。

### 3.7 其余实体与阶段对照

| 实体 | 主要阶段 | 本文是否展开 |
| --- | --- | --- |
| System / Application / Service / Environment / Capability | 1（只读导入） | 字段见设计文档 §12；阶段 1 由种子清单导入 |
| WorkItemRelation / WorkItemSupplement | 1 | 结构简单，见 §5 DDL |
| DeliveryPlanRevision / PlanStep | 2 | 阶段 1 仅预留引用字段 |
| Handover | 2 | 阶段 1 只建表不用 |
| ChangeSet / Artifact / TestRun / TestResult / Release / MigrationRun | 2 | 同上 |
| ProvisioningRequest / Resource | 3 | 同上 |
| PilotEvaluation | 0 建模，1–4 持续 | 与开发计划 §11 的门槛模板配套，另行定稿 |
| AuditEvent / Outbox / Inbox | 1 起 | 阶段 1 至少记录读写审计与 Outbox 骨架 |

## 4. 跨实体不变量（可直接变成测试）

这 12 条是后续写单测的清单，每条都应有一条失败的用例：

1. `WorkItem.description` 提交后不可变：UPDATE 该列被拒绝。
2. `WorkItem.status` 非法迁移返回 409，且不写库。
3. 非提交人不能取消他人工单（403）。
4. `AnalysisRevision` 提交后字段不可变；更正产生 `revision_no + 1`，旧版置 superseded。
5. 同一工单至多一个 `submitted` 分析版本（并发提交时唯一索引生效）。
6. `AnalysisRevision.evidence_refs` 只能包含 `status = active` 的证据。
7. `PublishedSummary.audience_scope` 超出 `inherited_scope` 且无有效 `approved_rule` 依据时，写入被拒绝。
8. `inherited_scope` 必须等于各来源 `access_scope` 的**交集**（构造两个来源交集小于并集的用例）。
9. 来源 `revoked` 后，关联摘要进入 `pending_recheck`，且不再对新受众可见。
10. 相同 `approval_decision_id` 重复投递只产生一条决定，副作用只发生一次。
11. `ExecutionControl.execution_epoch` 单调递增；`control_version` 不匹配时替代操作返回冲突。
12. 所有写接口缺少幂等键或 `If-Match` 时返回 400（不静默接受）。

## 5. 阶段 1 数据库草案（参考 DDL）

PostgreSQL 方言，**待 D2 确认**；若 D2 选择其他数据库，字段与约束语义不变，只替换类型与部分索引写法。

```sql
-- 工单
CREATE TABLE work_item (
  id                          text PRIMARY KEY,           -- BUG-2026-0108
  kind                        text NOT NULL CHECK (kind IN ('bug','requirement','usage')),
  system_id                   text REFERENCES system(id), -- 允许空：暂不确定
  title                       text NOT NULL,
  description                 text NOT NULL,
  status                      text NOT NULL,
  status_reason               text,
  requester_id                text NOT NULL REFERENCES app_user(id),
  owner_id                    text REFERENCES app_user(id),
  priority                    text NOT NULL CHECK (priority IN ('P1','P2','P3','P4')),
  visibility_scope            text NOT NULL,
  current_analysis_revision_id text REFERENCES analysis_revision(id),
  version                     integer NOT NULL DEFAULT 1,  -- 乐观锁（If-Match）
  created_at                  timestamptz NOT NULL DEFAULT now(),
  updated_at                  timestamptz NOT NULL DEFAULT now(),
  closed_at                   timestamptz
);
CREATE INDEX work_item_status_updated_idx ON work_item (status, updated_at DESC);
CREATE INDEX work_item_system_idx ON work_item (system_id);

-- 补充（只追加）
CREATE TABLE work_item_supplement (
  id           text PRIMARY KEY,
  work_item_id text NOT NULL REFERENCES work_item(id),
  author_id    text NOT NULL REFERENCES app_user(id),
  body         text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- 证据
CREATE TABLE evidence (
  id                 text PRIMARY KEY,
  work_item_id       text NOT NULL REFERENCES work_item(id),
  source_type        text NOT NULL CHECK (source_type IN ('connector','manual','attachment')),
  source_ref         text NOT NULL,
  connector_revision text,
  query              jsonb,
  captured_at        timestamptz NOT NULL,
  snapshot_ref       text,
  checksum           text,
  summary            text NOT NULL,
  access_scope       text NOT NULL,                        -- 派生内容据此取交集
  sensitivity        text NOT NULL CHECK (sensitivity IN ('public','internal','restricted')),
  retention_until    timestamptz,
  status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded','revoked')),
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evidence_work_item_idx ON evidence (work_item_id, status);

-- 分析版本（不可变）
CREATE TABLE analysis_revision (
  id               text PRIMARY KEY,
  work_item_id     text NOT NULL REFERENCES work_item(id),
  revision_no      integer NOT NULL,
  status           text NOT NULL CHECK (status IN ('draft','submitted','superseded')),
  internal_summary text,
  facts            jsonb NOT NULL DEFAULT '[]',
  assumptions      jsonb NOT NULL DEFAULT '[]',
  excluded         jsonb NOT NULL DEFAULT '[]',
  evidence_refs    text[] NOT NULL DEFAULT '{}',
  tool_runs        jsonb NOT NULL DEFAULT '[]',
  model_version    text,
  created_by       text NOT NULL REFERENCES app_user(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  submitted_at     timestamptz,
  UNIQUE (work_item_id, revision_no)
);
-- 同一工单同时只允许一个 submitted 版本
CREATE UNIQUE INDEX analysis_revision_single_submitted_idx
  ON analysis_revision (work_item_id) WHERE status = 'submitted';

-- 派生内容（发布版本）
CREATE TABLE published_summary (
  id                  text PRIMARY KEY,
  source_type         text NOT NULL,
  source_id           text NOT NULL,
  source_revision_no  integer,
  content_version     integer NOT NULL DEFAULT 1,
  body                text NOT NULL,
  audience_scope      text NOT NULL,
  inherited_scope     text NOT NULL,   -- 服务端计算的来源交集；无法用 CHECK 表达扩充规则
  publication_basis   text NOT NULL CHECK (publication_basis IN ('approved_rule','per_revision_review')),
  basis_ref           text NOT NULL,
  status              text NOT NULL CHECK (status IN ('draft','published','pending_recheck','revoked')),
  published_by        text REFERENCES app_user(id),
  published_at        timestamptz,
  revoked_at          timestamptz,
  revoke_reason       text
);
CREATE INDEX published_summary_source_idx ON published_summary (source_type, source_id);

-- 审批（阶段 1 只读）
CREATE TABLE approval (
  approval_decision_id text PRIMARY KEY,                    -- 幂等键
  subject_type         text NOT NULL CHECK (subject_type IN ('plan','acceptance','release','resource')),
  subject_ref          text NOT NULL,
  subject_digest       text NOT NULL,
  decision             text NOT NULL CHECK (decision IN ('approved','returned','rejected','held')),
  approver_id          text NOT NULL REFERENCES app_user(id),
  approver_role        text NOT NULL,
  scope                jsonb NOT NULL DEFAULT '{}',
  valid_until          timestamptz,
  revoked_at           timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX approval_subject_idx ON approval (subject_type, subject_ref, created_at DESC);

-- 交付范围与运行控制（阶段 1 建表不写业务数据）
CREATE TABLE delivery_scope (
  id             text PRIMARY KEY,
  work_item_id   text NOT NULL REFERENCES work_item(id),
  parent_scope_id text REFERENCES delivery_scope(id),
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE execution_control (
  delivery_scope_id text PRIMARY KEY REFERENCES delivery_scope(id),
  active_plan_ref   text,
  execution_epoch   integer NOT NULL DEFAULT 0,
  mode              text NOT NULL DEFAULT 'auto' CHECK (mode IN ('auto','pausing','manual','blocked')),
  control_version   integer NOT NULL DEFAULT 1,
  budget_used_minor bigint NOT NULL DEFAULT 0,
  repair_count_used integer NOT NULL DEFAULT 0,
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- 执行记录（阶段 1 只读）
CREATE TABLE execution_run (
  id                text PRIMARY KEY,
  delivery_scope_id text NOT NULL REFERENCES delivery_scope(id),
  plan_ref          text NOT NULL,
  execution_epoch   integer NOT NULL,
  status            text NOT NULL,
  baseline_ref      text,
  checkpoint        jsonb,
  repair_rounds     integer NOT NULL DEFAULT 0,
  cost_minor        bigint NOT NULL DEFAULT 0,
  currency          text NOT NULL DEFAULT 'CNY',
  started_at        timestamptz,
  ended_at          timestamptz
);
```

## 6. 阶段 1 API 契约

### 6.0 通用约定

| 项 | 约定 |
| --- | --- |
| 认证 | `Authorization: Bearer <token>`；**服务端逐请求校验**，前端隐藏不作为授权 |
| 写接口幂等 | 必须带 `Idempotency-Key`；同键同请求返回首次结果，不重复产生副作用 |
| 对象版本 | 写接口必须带 `If-Match: <version>`；不匹配返回 409 与最新版本号 |
| 错误格式 | `{ "error": "可读原因", "code": "MACHINE_CODE", "detail": {...} }` |
| 分页 | `?limit=&cursor=`，响应含 `next_cursor`；默认 20，上限 100 |
| 时间 | ISO 8601 带时区 |
| 可见性 | 读接口按调用者有效权限过滤；**无权访问返回 404 而非 403**（不泄露对象存在性） |
| 状态字段 | 任何写接口**不接受** `status`；状态由服务端迁移 |

### 6.1 `GET /api/v1/systems` — 系统目录

| 项 | 内容 |
| --- | --- |
| 权限 | 登录用户；只返回其可见系统 |
| 查询 | `?domain=&health=&q=&limit=&cursor=` |
| 响应 | `{ items: [{ id, name, english, domain, owner, health, level, issue_count }], next_cursor }` |
| 阶段 1 | 真实数据（种子清单导入 + 手工维护） |

### 6.2 `GET /api/v1/systems/{systemId}` — 系统详情

| 项 | 内容 |
| --- | --- |
| 权限 | 登录用户且该系统可见，否则 404 |
| 响应 | 目录字段 + `environments[]` + `dependencies[]`；**不含未授权的能力与凭证信息** |
| 阶段 1 | 真实数据；技术细节字段按权限裁剪 |

### 6.3 `GET /api/v1/systems/{systemId}/capabilities` — 能力与环境范围

| 项 | 内容 |
| --- | --- |
| 权限 | 登录用户；**能力清单按调用者过滤**，不返回其无权使用的动作 |
| 响应 | `{ connector_revision, capabilities: [{ key, env, granted, since }] }` |
| 阶段 1 | 只读；`connector_revision` 用于固定来源版本（Evidence 引用它） |

### 6.4 `GET /api/v1/work-items` — 工单列表

| 项 | 内容 |
| --- | --- |
| 权限 | 只返回调用者可见范围内的工单（提交人可见自己的 + 被授权范围的） |
| 查询 | `?status=&kind=&system_id=&owner=me&q=&limit=&cursor=` |
| 响应 | `{ items: [{ id, kind, system_id, title, status, priority, owner_id, updated_at }], next_cursor }` |
| 阶段 1 | 真实数据；列表不返回 `description`（避免越权拉取正文） |

### 6.5 `POST /api/v1/work-items` — 提交问题 / 需求

| 项 | 内容 |
| --- | --- |
| 权限 | 登录用户 |
| 请求 | `{ kind, system_id?, title, description, priority?, context_scope? }`（`system_id` 可省略 = 暂不确定） |
| 响应 | `201 { id, status: "已提交", version: 1 }` |
| 幂等 | `Idempotency-Key` 必填；同键重复提交返回同一 `id` |
| 校验 | `kind` 与 `title` 必填；`description` 非空；不接受 `status`、`owner_id`、`visibility_scope` 由服务端决定 |
| 阶段 1 | 真实写入；创建后自动进入"分流中" |

### 6.6 `GET /api/v1/work-items/{id}` — 工单详情

| 项 | 内容 |
| --- | --- |
| 权限 | 可见范围内；否则 404 |
| 响应 | 工单字段 + `supplements[]` + `current_analysis`（**内部分析与已发布摘要分开返回**，前者按权限裁剪） |
| 阶段 1 | 真实数据；响应体按调用者权限分层，受限字段直接不出现 |

### 6.7 `POST /api/v1/work-items/{id}/supplements` — 补充信息

| 项 | 内容 |
| --- | --- |
| 权限 | 提交人本人，或该工单责任团队 |
| 请求 | `{ body, attachments? }` |
| 幂等 | `Idempotency-Key` 必填 |
| 行为 | 只追加，不修改原 `description`；"待补充"状态下补充后自动回到"分析中" |
| 阶段 1 | 真实写入 |

### 6.8 `POST /api/v1/work-items/{id}/analysis-runs` — 发起分析

| 项 | 内容 |
| --- | --- |
| 权限 | 责任人/责任团队；普通提交人不可发起 |
| 请求 | `{ scope: { source_types: ["connector","attachment"], time_window? } }` |
| 响应 | `202 { run_id, status: "queued" }`；结果以 `AnalysisRevision` 落库 |
| 幂等 | `Idempotency-Key` 必填；同一工单并发发起只允许一个进行中的 run |
| 阶段 1 | **模板 + 人工修订**；未接模型时报告由模板生成并标注来源，字段结构不变 |

### 6.9 `POST /api/v1/work-items/{id}/content-publications` — 发布 / 撤回派生内容

| 项 | 内容 |
| --- | --- |
| 权限 | 对来源数据有发布授权的责任人（D3 定义） |
| 请求 | `{ summary_id?, source_type, source_id, action: "publish"\|"revoke", audience_scope, publication_basis, basis_ref }` |
| 校验 | ① `publication_basis` 可解析；② `audience_scope` 不超过 `inherited_scope`，除非依据为 `approved_rule` 且规则覆盖该范围；③ 来源证据均为 active |
| 行为 | 写入 `PublishedSummary` + `ContentPublication`；来源权限变化时由后台任务置 `pending_recheck` |
| 阶段 1 | 真实写入（这是阶段 1 里权限规则最重的一个接口，必须先有 §4 的第 7/8/9 条测试） |

### 6.10 `GET /api/v1/approvals?status=pending` — 审批待办（只读视图）

| 项 | 内容 |
| --- | --- |
| 权限 | 仅返回调用者是批准人的记录 |
| 响应 | `{ items: [{ approval_decision_id, subject_type, subject_ref, subject_digest, decision, created_at }] }` |
| 阶段 1 | **只读**；数据来自种子，界面明确标注"演示数据，不可操作" |

## 7. 阶段 1 范围复核表（D6 输入）

| 能力 | 接口 | 阶段 1 形态 | 备注 |
| --- | --- | --- | --- |
| 系统目录 | 6.1 / 6.2 | 真实 | 种子清单导入 |
| 能力与环境范围 | 6.3 | 真实（只读） | 决定 Evidence 能否固定来源版本 |
| 工单提交 / 列表 / 详情 | 6.4 / 6.5 / 6.6 | 真实写入 | |
| 补充信息 | 6.7 | 真实写入 | |
| 分析 | 6.8 | 真实（模板 + 人工修订） | 模型接入可选 |
| 派生内容发布 | 6.9 | 真实写入 | 权限规则最重 |
| 审批 | 6.10 | **只读占位** | 写接口留到阶段 2 |
| 执行 / 发布 / 试点 | — | **只读占位** | 表结构已建，阶段 1 不写业务数据 |

## 8. 待确认（依赖决策）

| 决策 | 影响本文的哪一部分 | 不定的后果 |
| --- | --- | --- |
| D2 技术栈 | §5 DDL 方言、ID 生成方式、乐观锁实现 | 建表返工 |
| D3 身份与授权 | §6 每个接口的权限判据；`visibility_scope` 的具体取值 | 所有读接口的过滤条件无法实现 |
| D6 阶段 1 范围 | §7 范围表（尤其是 6.9 是否进阶段 1） | 范围蔓延 |
| D8 模型数据边界 | `Evidence.sensitivity` 能否进入模型上下文；`internal_summary` 是否可发给模型 | 分析功能可能整体不可用 |
| 试点评估口径 | `PilotEvaluation`（本文未展开） | 与开发计划 §11 的门槛模板配套，另行定稿 |

## 9. 评审要点（请评审人回答这五个问题）

1. **字段够不够？** 六个实体的字段能否覆盖阶段 1 的所有界面需求？尤其是工单列表与详情页——**缺字段会导致后端返工，这是最贵的一类改动**。
2. **受众交集是否过严？** §3.4 规定发布受众默认等于来源交集。如果一份摘要同时引用了 `internal` 与 `restricted` 证据，交集会小到几乎没人能看。你们接受"结论只能给很小范围"，还是需要**分片发布**（按来源拆成多个发布版本）？这一条直接决定产品形态。
3. **Evidence 存摘要还是存原文？** 现在只存 `summary` + `snapshot_ref`。若业务上需要看日志原文，则 `snapshot_ref` 必须能按权限取回，且取回动作要写审计——请确认边界（与 D8 联动）。
4. **阶段 1 的状态子集是否够用？** 阶段 1 的工单只会走到"分析中 / 待补充"，看不到"待方案审批"。试点用户若预期看到审批态，需要提前沟通否则会被认为"流程卡住"。
5. **工单编号是否要与 JIRA 打通？** 现在是 `BUG-2026-0108` 自增形态。若要与 `hrjira` 编号互引，规则必须在阶段 1 定下来，后期改号成本很高。

---

评审通过后：把本文版本改为 V1.0「已评审」，在 [开发计划](development-plan-quality-gates.md) §8.1 勾掉"核心领域模型草案"这一项，并按 §9 的答复更新 §5/§6。
