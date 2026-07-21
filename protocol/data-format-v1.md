# 研错库数据格式 v1（初稿）

> 状态：阶段 A 冻结 MVP 核心字段。变更前须说明原因与兼容性影响。  
> 权威实现：Windows `yancuo_win.data.models`；安卓端后续按本规范对齐。  
> `schema_version` / `data_format_version` 当前均为 **1**。

---

## 1. 原则

1. SQLite 为本地工作库；图片以 content-addressed 对象存储。  
2. 资源路径只存相对对象路径，禁止本机绝对路径。  
3. 角色为 `original` 的资源不可变，不得被识别结果覆盖。  
4. 题目修改通过 `revision` + `versions` 追溯；AI/外部导入不得静默覆盖。  
5. 未在本文标注为 MVP 的字段，实现方可延后写入，但不得占用冲突语义。

---

## 2. 标识与身份

| 字段 | 格式 | 说明 |
|------|------|------|
| `user_id` | `usr_` + hex | 本地用户，不依赖云 |
| `device_id` | `dev_win_` / `dev_android_` + hex | 设备 |
| `database_id` | `db_` + hex | 本库实例 |
| 实体 `id` | 前缀 + hex（如 `problem_`） | 全局唯一字符串主键 |

本地身份文件示例（`identity.json`）：

```json
{
  "user_id": "usr_…",
  "device_id": "dev_win_…",
  "database_id": "db_…",
  "display_name": "本地用户",
  "created_at": "2026-07-21T10:00:00+00:00"
}
```

---

## 3. 题目状态机（Problem.status）

| 状态 | 含义 | MVP |
|------|------|-----|
| `inbox` | 收件箱，未整理完成 | 是 |
| `active` | 正式题库 | 是 |
| `archived` | 归档，默认不出现在日常列表 | 是（可后置 UI） |
| `trashed` | 回收站 | 是 |

非法迁移应由应用服务层拒绝。

---

## 4. 核心实体字段

### 4.1 Subject

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | |
| name | string | 是 | 唯一 |
| sort_order | int | 是 | 默认 0 |

### 4.2 Chapter

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | |
| subject_id | string | 是 | FK |
| parent_id | string? | 否 | 树形 |
| name | string | 是 | |
| sort_order | int | 是 | |

章节体系**不得**在业务代码中写死考研数学目录，应由数据/模板提供。

### 4.3 Problem（MVP）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | |
| status | string | 是 | 见状态机 |
| subject_id / chapter_id | string? | 否 | |
| problem_type | string? | 否 | 题型名，MVP 可用字符串 |
| title | string? | 否 | |
| question_markdown | text | 是 | 可空字符串 |
| question_latex | text | 是 | |
| user_answer | text | 是 | |
| correct_answer | text | 是 | |
| solution_markdown | text | 是 | |
| error_analysis | text | 是 | |
| notes | text | 是 | |
| source_book / source_year / page_number / original_number | string? | 否 | |
| priority | int | 是 | 1–5，默认 3 |
| difficulty / mastery | int? | 否 | |
| is_favorite / needs_redo / allow_print / human_confirmed | bool | 是 | |
| revision | int | 是 | 从 1 起 |
| created_at / updated_at | datetime | 是 | UTC |
| deleted_at | datetime? | 否 | trashed 时填充 |

**第二版扩展位（已建列，逻辑后启）：** `next_review_at`, `review_count`

### 4.4 Asset

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | |
| problem_id | string? | 否 | 入库前可暂无题 |
| role | string | 是 | `original` / `processed` / `answer` / `user_work` / `attachment` |
| sha256 | string | 是 | hex 小写 |
| relative_path | string | 是 | 如 `objects/8c/8cf9….jpg` |
| mime_type | string? | 否 | |
| size_bytes / width / height | int? | 否 | |
| is_immutable | bool | 是 | `original` 应为 true |

对象落盘：

```text
{asset_dir}/objects/{sha256[0:2]}/{sha256}{ext}
```

### 4.5 Tag / ProblemTag

| 字段 | 说明 |
|------|------|
| tags.id / name / color / parent_id / is_system | 系统标签由程序维护 |
| problem_tags (problem_id, tag_id) | 多对多 |

### 4.6 Version

| 字段 | 说明 |
|------|------|
| id | |
| problem_id | |
| revision | 与写入后题目 revision 对应 |
| source | `manual` / `ai` / `workspace` / `import` / `sync` |
| summary | 人类可读摘要 |
| snapshot_json | 变更快照（字段子集即可） |
| created_at / created_by | |

---

## 5. 库元数据 meta_kv

| key | 示例 value |
|-----|------------|
| schema_version | `1` |
| data_format_version | `1` |

程序打开库时：若 `schema_version` 高于软件支持版本，应拒绝并提示升级。

---

## 6. JSON 表示（跨端 / 工作区预览）

题目对外交换时建议字段名使用 snake_case，与上表一致。完整 JSON Schema 将置于 `protocol/schemas/problem.schema.json`（阶段 B/D 补齐）。

不确定字段（AI）预留结构（阶段 C）：

```json
{
  "uncertain_fields": [
    {
      "field": "question_latex",
      "content": "ln x 或 ln|x|",
      "reason": "图片模糊"
    }
  ]
}
```

---

## 7. 兼容性规则

- **加法兼容**：新增可空列或新表，应递增 `schema_version` 并提供迁移。  
- **破坏性变更**：重命名/删除列、改变 status 枚举语义，必须写迁移说明与回滚策略，并同步更新本文件与安卓实现。  
- **包格式**（`.ebpack`）不在 v1 本文件范围，见后续 `ebpack-format-v1.md`。

---

## 8. 非目标（本版本明确不做）

- 增量同步 Operation 事件模型  
- 端到端加密载荷格式  
- 朋友分享包 `.gmshare`  
- 将 Word/PDF 作为主存储  
