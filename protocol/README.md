# 协议目录

Windows / 安卓 / 云端共享的**唯一规范真相**。

| 文件                            | 状态                                 |
| ------------------------------- | ------------------------------------ |
| `data-format-v1.md`             | 跨端字段语义 v1；当前数据库 schema_version=8、data_format_version=1 |
| `workspace-format-v1.md`        | 阶段 D 定稿                          |
| `ebpack-format-v1.md`           | 阶段 F 定稿；当前仅支持未加密 v1 包  |
| `sync-protocol-v1.md`           | 阶段 J 定稿；Windows LocalFolder 已接入，远端/Android 增量后置 |
| `gmshare-format-v1.md`          | 阶段 K 定稿；Windows 分享与 origin 去重已接入 |
| `encryption-v1.md`              | 未实现设计占位；当前 `encrypted=true` 必须拒绝 |
| `schemas/problem.schema.json`   | 工作区题目元数据 schema             |
| `schemas/operation.schema.json` | 阶段 J Operation schema              |
| `schemas/search-spec.schema.json` | AI 搜索意图白名单 schema；不含 SQL、状态或知识范围 |
| `schemas/search-rerank.schema.json` | AI 候选重排 schema；返回 ID 必须由本地候选再次校验 |
| `test-vectors/`                 | hash-v1 / ebpack-v1 / sync-v1        |

变更流程：先改文档说明原因与兼容性 → 再改实现。

## 当前兼容边界

- `schema_version` 是本地数据库迁移版本，当前目标为 **7**；`data_format_version` 是跨端字段语义版本，当前为 **1**。
- `.ebpack` 使用 `format_version=1`；Windows 可导出/导入，Android 可导入，当前均只接受未加密包。
- LocalFolder 支持 `changes/` 的 Operation 推拉；GitLink/GitHub 当前仍以完整 `.ebpack` Release 备份为主，不宣称远端增量同步。
- Word/PDF、端到端加密和 Android 云下载不由本目录的 v1 协议承诺；实现状态以各协议文档和 [`docs/08_完成更新记录.md`](../docs/08_完成更新记录.md) 为准。
