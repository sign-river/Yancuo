# `.gmshare` 朋友分享包格式 v1

> 状态：阶段 K 定稿。  
> **个人同步 / 完整备份（`.ebpack`）与朋友分享是两套通道，不得混用。**  
> 实现：`yancuo_win.import_export.gmshare`

---

## 1. 目标

- 向朋友分享精选错题内容
- 默认剥离私人与设备敏感信息
- 再导入时用 `origin_package_id` + `origin_problem_id` 去重，不盲目复制

---

## 2. 文件形态

- 扩展名：`.gmshare`
- 本质：ZIP（`ZIP_DEFLATED`），路径使用 `/`

```text
manifest.json
checksums.sha256
problems.jsonl
assets/
  objects/{sha256[0:2]}/{sha256}{ext}
  index.json
```

**不包含**：完整 SQLite、`identity.json`、密钥、云配置、`sync_operations`、复习会话表。

---

## 3. manifest.json

```json
{
  "format": "graduate-mistake-book-gmshare",
  "format_version": 1,
  "package_id": "share_…",
  "created_at": "2026-07-22T00:00:00+00:00",
  "title": "高数不定积分精选",
  "app_version": "0.1.0",
  "data_format_version": 1,
  "problem_count": 3,
  "asset_count": 3,
  "includes": {
    "question": true,
    "correct_answer": true,
    "solution": true,
    "tags": true,
    "source": true,
    "original_images": true,
    "error_analysis": false,
    "user_answer": false,
    "notes": false,
    "review_history": false
  }
}
```

校验：`format` / `format_version=1`；未知字段忽略。

---

## 4. 默认拒绝列表（硬默认）

导出时**默认排除**（即使用户未勾选，下列也不得出现在包内）：

| 类别 | 字段/内容 |
|------|-----------|
| 手写错误过程 | `user_answer` |
| 私人备注 | `notes` |
| 复习史 | `next_review_at`、`review_count`、`mastery`、复习打分记录 |
| 密钥 | 任意 token / API key / credential |
| 设备与身份 | `identity.json`、device_id、user_id、database_id |
| 云与同步 | cloud 配置、`sync_operations`、locks |
| AI 私货 | prompts 中的用户密钥、原始 AI 响应全文（可选后续） |

可选包含（默认开）：题目正文、LaTeX、正确答案、解析、标签、来源元数据、`role=original` 图片。  
`error_analysis` 默认关（偏个人错因时可不开）。

---

## 5. problems.jsonl

每行一题（UTF-8）：

```json
{
  "origin_problem_id": "problem_…",
  "title": "…",
  "question_markdown": "…",
  "question_latex": "",
  "correct_answer": "…",
  "solution_markdown": "…",
  "error_analysis": "",
  "tags": ["不定积分"],
  "source_book": "",
  "source_year": "",
  "page_number": "",
  "original_number": "",
  "priority": 3,
  "assets": [
    {"role": "original", "sha256": "…", "relative_path": "objects/ab/ab….jpg", "mime_type": "image/jpeg"}
  ]
}
```

禁止字段：`user_answer`、`notes`、复习字段、本地 `id`（仅 origin）。

---

## 6. 导入语义

1. 校验 manifest 与 checksums  
2. 每题：若本地已有相同 `(origin_package_id, origin_problem_id)` → **跳过**  
3. 否则分配新本地 `problem_*` id，写入 `problem_origins`  
4. 复制允许的 assets（内容寻址，同哈希不覆盖）  
5. 状态默认 `inbox` 或 `active`（实现可选；建议 `inbox`）

```json
{
  "origin_package_id": "share_…",
  "origin_problem_id": "problem_…",
  "imported_from": "shared-package"
}
```

---

## 7. 与 `.ebpack` 的区别

| | `.ebpack` | `.gmshare` |
|--|-----------|------------|
| 用途 | 本人备份/迁移 | 朋友分享 |
| 载荷 | 完整 SQLite | 脱敏 JSONL + 可选原图 |
| 身份 | 可含 identity | **禁止** |
| 再导入 | 换机恢复 | 新 ID + origin 去重 |
