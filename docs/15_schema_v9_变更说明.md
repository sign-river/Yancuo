# schema v9 变更说明

## 背景

schema v8 已建立正式笔记文档、内容块和原图资产，但 AI 提取结果在确认前仍只存在于内存。关闭确认窗口或程序后，标题、分类建议、块顺序、来源区域和原图路径都会丢失。`NOTE-05A` 因此增加独立的笔记分类暂存模型；确认前不创建 `NoteDocument`，也不把草稿塞入题目录入表。

## 迁移

| 项目 | 变更 |
| --- | --- |
| `schema_version` | **8 → 9** |
| `note_intake_sessions` | 新增笔记录入会话：分类模式、生命周期、用户指令、草稿元数据、失败原因与完成时间 |
| `note_intake_assets` | 新增内容寻址来源图：会话、顺序、哈希、相对路径、MIME、尺寸与不可变标记 |
| `note_draft_groups` | 新增临时分类组：顺序、标题、现有/新建/未解析分类、标签提案、目标状态和后续正式笔记引用 |
| `note_draft_blocks` | 新增临时内容块：组内顺序、类型、Markdown/LaTeX、来源资产、来源区域与不确定字段 |
| `data_format_version` | 保持 **1** |

四张表均为 Windows 当前使用的权威草稿状态，不进入 `SearchDocument` 或 FTS。正式 `NoteDocument`、`NoteBlock`、`NoteAsset` 和 `note_tags` 结构保持不变。

## 生命周期

```text
draft → processing → review → completed
             ↓  ↑
           failed

draft / failed / review → cancelled
```

- `draft`：分类模式、用户指令和已内部化的不可变原图已保存；
- `processing`：AI 请求正在执行；重启后无法继续的遗留任务会修复为 `failed`；
- `review`：分类组和内容块已在同一事务中完整保存，可跨重启恢复；
- `failed`：保留原图、指令和既有草稿，可明确重试；
- `completed` / `cancelled`：终态，不再出现在待处理列表；
- `save_extraction()` 先验证全部分类、标签、来源资产和 JSON，再原子替换整个组/块快照，失败时保留旧快照。

## 分类与数据边界

- `classification_mode` 当前只接受 `ai` 或 `custom`；
- `category_resolution` 只接受 `existing`、`create_new` 或 `unresolved`；
- 已有分类使用稳定科目/章节 ID，并在保存时验证存在性与从属关系；新分类只保存提案，不提前创建正式目录；
- 未解析分类只能把未来正式笔记目标标为 `inbox`，不能绕过确认直接进入 `active`；
- 分类组保留已有标签 ID、建议标签、分类理由和最相近目录等 JSON 扩展位，为 `NOTE-05C`—`NOTE-05E` 使用；
- 当前正式 `NoteBlock` 尚未保存具体 `note_asset_id`，所以本切片明确限制每个会话只使用一张来源图；多图笔记需在后续单独设计正式来源映射；
- 当前扁平 `NoteExtractionDraft` 可无损适配成一个 `unresolved` 组，既有笔记 UI 尚未切换到分类组确认器。

## 原图与清理

来源图在 AI 请求或确认前复制到现有 SHA-256 对象库，外部原文件被移动或删除后仍可恢复。题目 `Asset`、题目 `IntakeAsset`、正式 `NoteAsset` 和 `NoteIntakeAsset` 共享同一个物理对象目录；清理题目或草稿时必须确认四类引用均不存在，不能误删仍被笔记使用的同哈希文件。

显式放弃先把会话标记为 `cancelled`；安全清理入口只删除这类会话，并在数据库提交后按上述四类引用重新判断物理对象。`completed` 会话当前保留，供 `NOTE-05E` 后续记录每个分类组对应的正式笔记并保证提交幂等；其保留期限在正式多篇入库交付时再确定。

`.ebpack format_version=1` 不变，完整 SQLite 快照会自然携带 v9 草稿表，schema v9 的 `asset_count` 统计包内去重后的实际对象文件数。校验和表必须覆盖固定载荷与对象索引中的全部文件，索引、实际对象和 manifest 计数必须一致。Windows 恢复后继续按当前程序迁移；Android 本地新建库仍是核心 schema v7，但未加密包导入器可校验并保留加法式 schema v9 快照，Android UI 不读写 v8 笔记或 v9 草稿表。

## 迁移保护

- v8 旧库升级前使用 SQLite online backup API 创建并验证副本；迁移或核心表验证失败时原子恢复；
- 启动迁移目标以程序内置 `SCHEMA_VERSION` 为准，旧外部 TOML 不会阻止必要迁移；
- `yancuo-migrate` 命令行入口同样执行升级前备份、FTS 重建、核心表校验和失败恢复；
- 空库可直接到 v9，v8→v9 为加法迁移，重复执行不重复建表或清空草稿；
- Android 导入时除完整校验和覆盖外，还核对 manifest 与快照 schema、题目计数、版本对应核心表/列，并执行 SQLite 完整性和外键检查；正式数据使用 staging + previous 替换，安装失败时恢复旧库。

## 后续范围

本次只完成 `NOTE-05A` 的数据与服务基础。识别前模式选择和默认分组由 `NOTE-05B` 承接；分类行编辑、合并和新分类建议由 `NOTE-05C` 承接；跨组拖拽由 `NOTE-05D` 承接；每个非空组生成独立正式笔记由 `NOTE-05E` 承接。标签/题目关联和统一搜索仍分别属于 `NOTE-05` 与 `SEARCH-07`。
