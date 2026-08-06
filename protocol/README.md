# 协议目录

Windows / 安卓 / 云端共享的**唯一规范真相**。

| 文件                            | 状态                                 |
| ------------------------------- | ------------------------------------ |
| `data-format-v1.md`             | 跨端字段语义 v1；当前 Windows schema_version=22、data_format_version=1 |
| `workspace-format-v1.md`        | 阶段 D 定稿                          |
| `ebpack-format-v1.md`           | 阶段 F 定稿；当前仅支持未加密 v1 包  |
| `sync-protocol-v1.md`           | 阶段 J 定稿；Windows LocalFolder 离线通道与 CloudBase 批次已接入 |
| `profile-snapshots-v1.md`       | 本地资料、不可变云快照与资料接管协议 |
| `gmshare-format-v1.md`          | 阶段 K 定稿；Windows 分享与 origin 去重已接入 |
| `encryption-v1.md`              | 未实现设计占位；当前 `encrypted=true` 必须拒绝 |
| `schemas/problem.schema.json`   | 工作区题目元数据 schema             |
| `schemas/operation.schema.json` | 阶段 J Operation schema              |
| `schemas/search-spec.schema.json` | AI 搜索意图白名单 schema；不含 SQL、状态或知识范围 |
| `schemas/search-rerank.schema.json` | AI 候选重排 schema；返回 ID 必须由本地候选再次校验 |
| `test-vectors/`                 | hash-v1 / ebpack-v1 / sync-v1        |

变更流程：先改文档说明原因与兼容性 → 再改实现。

## 当前兼容边界

- `schema_version` 是本地数据库迁移版本：Windows 当前为 **22**；Android 新建本地核心库为 **7**，未加密 `.ebpack` 导入器当前接受到 **9**。`data_format_version` 是跨端字段语义版本，当前为 **1**。
- `.ebpack` 使用 `format_version=1`；Windows 可导出/导入，当前各端只接受未加密包。Android UI 不读写 Windows 笔记、草稿或合集表。
- LocalFolder 支持离线 `changes/` Operation 推拉；正式远端由 CloudBase 网关使用原子锁和不可变 Operation 批次承载。
- Word/PDF、端到端加密和 Android 云下载不由本目录的 v1 协议承诺；实现状态以各协议文档和 [`docs/完成记录.md`](../docs/完成记录.md) 为准。
