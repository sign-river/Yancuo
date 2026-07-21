# 外部工作区格式 v1

> 状态：阶段 D 定稿。变更前须说明原因与兼容性影响。  
> 实现：`yancuo_win.import_export.workspace`  
> **禁止**直接用外部工具修改 SQLite；只编辑本工作区文件后走「导入外部修改」。

---

## 1. 目录结构

```text
{workspace_root}/
├── manifest.json
├── instructions.md
├── problems/
│   └── {problem_id}/
│       ├── problem.md
│       ├── metadata.json
│       └── assets/
│           ├── original.jpg   # 可选，按实际扩展名
│           └── …
└── schemas/
    └── problem.schema.json
```

---

## 2. manifest.json

```json
{
  "format": "yancuo-workspace",
  "format_version": 1,
  "exported_at": "2026-07-21T12:00:00+00:00",
  "database_id": "db_…",
  "app_version": "0.1.0",
  "problem_ids": ["problem_…"],
  "warning": "Do not edit the SQLite database. Import changes via the app."
}
```

`format` 必须为 `yancuo-workspace`，`format_version` 必须为 `1`。

---

## 3. metadata.json（权威结构化字段）

```json
{
  "id": "problem_…",
  "revision": 7,
  "status": "active",
  "priority": 5,
  "title": "短标题",
  "subject_name": "高等数学",
  "chapter_name": "一元函数积分学",
  "tags": ["换元积分"],
  "asset_files": [
    {"role": "original", "filename": "original.jpg", "sha256": "…"}
  ]
}
```

导入时以 `revision` 作为冲突检测基线：若库中当前 `revision` 与导出时不同，则进入**冲突**，不得静默覆盖。

---

## 4. problem.md

YAML 风格 front matter（可用简易解析，字段与 metadata 对齐）+ Markdown 正文分区：

```text
---
id: problem_…
revision: 7
priority: 5
title: 短标题
tags:
  - 换元积分
---

# 原题

……

# 我的错误过程

……

# 正确答案

……

# 正确解法

……

# 核心公式

……

# 错因

……

# 备注

……
```

分区映射：

| 标题 | 字段 |
|------|------|
| 原题 | `question_markdown` |
| 我的错误过程 | `user_answer` |
| 正确答案 | `correct_answer` |
| 正确解法 | `solution_markdown` |
| 核心公式 | `question_latex` |
| 错因 | `error_analysis` |
| 备注 | `notes` |

若 front matter 与 `metadata.json` 冲突，**以 metadata.json 的 id/revision 为准**；正文分区以 `problem.md` 为准。

---

## 5. assets/

- 导出时复制当前题目关联图片；**不得**在导入时用工作区文件覆盖库内 `role=original` 且 `is_immutable=true` 的对象。  
- `metadata.asset_files[].filename` 必须存在于 `assets/`，否则该题导入失败。  
- 阶段 D 导入**不**把外部新图片写回对象库（避免绕过审核）；仅校验引用完整。

---

## 6. 导入与审核

1. 校验 manifest / schema / 资源引用。  
2. 对每题生成 `ReviewSession(source=workspace)` + `ReviewItem`。  
3. `base_revision` = 导出时 revision；`before_json` = 导入时库内快照；`proposed_json` = 工作区解析结果。  
4. 若 `problem.revision != base_revision` → `ReviewItem.status = conflict`。  
5. 用户在审核 UI：接受外部 / 保留内部（拒绝）/ 冲突下强制接受外部。  
6. 接受后写入版本 `source=workspace`。

---

## 7. 兼容性

- `format_version` 仅加法演进；破坏性变更须升主版本并双读。  
- 与 AI 审核共用 `review_items` 表，靠 `review_sessions.source` 区分。
