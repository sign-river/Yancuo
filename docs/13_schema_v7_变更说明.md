# schema v7 变更说明

## 背景

题库已经具备递归知识目录和稳定 `KnowledgeScope`，但现有搜索仍直接对题目字段执行简单匹配，无法同时承载正文、标签、完整知识路径和后续 AI 搜索候选召回。

schema v7 增加一个可随时重建的搜索投影。`Problem`、`Tag`、`Subject` 和 `Chapter` 仍是权威数据；搜索表损坏或丢失时不得反向覆盖业务数据。

## 迁移

| 项目 | 变更 |
| --- | --- |
| `schema_version` | **6 → 7** |
| `search_documents` | 新增；按题目保存状态、范围字段、完整路径和规范化检索文本 |
| `search_documents_fts` | Windows 本地 FTS5 trigram 虚表；由投影重建 |
| 正式题目字段 | 不变 |
| `data_format_version` | 保持 **1** |

`search_documents` 的主键是 `problem_id`，范围字段包括 `status`、`subject_id`、`chapter_id` 和 `knowledge_path`。文本字段包括 `title`、聚合后的 `body` 与 `tags_text`。

`body` 当前覆盖题干、LaTeX、用户作答、正确答案、解析、错因、笔记、来源和题型。后续笔记库会通过独立文档类型接入，不把笔记概念硬塞入题目表。

## 迁移保护

已有 schema v1—v6 数据库升级前会执行：

1. 使用 SQLite online backup API 在 `backups/` 生成独立副本；
2. 对副本执行 `PRAGMA integrity_check` 并核对原 schema 版本；
3. 执行逐版本迁移和核心表验证；
4. 任一步失败时关闭 SQLAlchemy 连接池，把已验证副本复制到临时恢复文件；
5. 校验临时恢复文件后使用原子替换恢复原数据库，并再次核对完整性和版本。

新建空库不生成无意义的迁移备份。成功升级后保留备份，供用户手工恢复。

## 普通搜索

- 3 个及以上字符使用 FTS5 trigram，支持中文子串；
- 1—2 个字符使用本地 `LIKE` 回退，避免 trigram 的最短长度限制；
- 默认只返回 `active` 正式题，可显式查询 inbox、archived 或 trashed；
- 可按科目、未分类或任意章节及其全部后代限制范围；
- 投影和 FTS 的全量重建在同一事务内完成，重复执行不会产生重复文档。

`SEARCH-02` 已补齐实时维护：

- 所有运行时 ORM 会话通过同一个事务钩子捕获题目新增、修改和删除；
- 标签、状态、分类和复习变化执行单题刷新；
- 科目、章节或标签目录变化执行全量投影刷新；
- 搜索索引写入与权威题目写入共享事务，任一失败都会整体回滚；
- 启动时检查权威数据、普通投影与 FTS 内容并自动修复；
- 设置页提供人工检查与重建入口。

这套维护不要求 AI、工作区、分享或同步模块分别调用索引 API，因此新增 ORM 写入路径默认不会绕开索引。

## 题库界面接入

`SEARCH-03` 已把题库搜索框接入本地索引：

- 浏览题库可搜索当前科目/章节子树，也可临时扩展到全部正式题目；
- 今日待复习、收藏和最近入库会在 FTS 候选上继续执行智能视图条件；
- 处理中心只搜索当前 inbox、archived 或 trashed 状态；
- 搜索结果按 FTS 相关性顺序加载题目对象，不改变题目权威数据；
- AI 搜索入口当前禁用，普通搜索不会发送网络请求。

`SEARCH-04` 已增加独立于数据库 schema 的安全表达层：

- 模型只可返回固定 `SearchSpec` JSON，不可返回 SQL、状态、科目/章节 ID 或任意字段；
- 关键词、题型、标签、优先级、收藏、时间窗口及排序均执行字段/操作符组合校验；
- 当前 `KnowledgeScope`、允许状态、候选数量和结果数量由本地程序强制提供，模型不能覆盖；
- 编译结果是纯数据计划，不持有连接、不生成 SQL，也不会自行读取权威业务表。

有限候选召回、候选 JSONL、模型返回 ID 校验和匹配原因属于 `SEARCH-05`；耗时、token、费用和隐私诊断属于 `SEARCH-06`。这些能力不由 schema v7 自动获得。

## `.ebpack` 与 Android

FTS5 trigram 依赖 Windows 随应用使用的 SQLite 能力。Android API 26 等旧系统 SQLite 不保证提供相同 tokenizer，因此：

- `.ebpack` 的 `snapshot.sqlite` 保留普通 `search_documents` 投影；
- 导出时删除 `search_documents_fts` 及其虚表影子数据；
- Android 忽略搜索投影，继续读取权威题目表；
- Windows 打开恢复库时自动创建 FTS 虚表，并在需要时从普通投影修复内容。

这不改变 `.ebpack format_version=1` 或跨端题目字段语义。
