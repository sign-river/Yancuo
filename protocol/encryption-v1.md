# 加密格式 v1（未实现）

> 状态：设计占位，尚未定稿或实现。阶段 G 只交付了未加密的完整 `.ebpack` 云备份；没有可互操作的加密包格式。

当前约束：`.ebpack` 的 `manifest.encrypted` 必须为 `false`；Windows 与 Android 对 `encrypted=true` 的包均拒绝导入。实现加密前，必须先补齐口令派生、AEAD 参数、恢复密钥、错误处理和跨端测试向量，再同步更新本文件、包格式规范与代码。
