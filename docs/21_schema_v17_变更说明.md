# Schema v17: 题目级 AI 对话

v17 新增独立的 `problem_conversations` 与 `problem_messages` 表，用于把 AI 讨论持久挂载到具体题目。

- 每个对话保存题目修订版本、固定上下文快照、Provider、文本模型和是否授权发送原图。
- 每条消息保存角色、顺序、状态、错误、token 与费用元数据。
- 用户消息在请求模型前先保存为 `pending`；请求失败后保留为 `failed`，不会丢失问题。
- 对话依赖题目外键，题目永久删除时级联清理；不会写入题目 `notes` 字段。
