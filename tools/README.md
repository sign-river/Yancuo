# 开发工具

本目录放置开发期探测脚本和其输出报告，不属于 Windows/Android 运行时。当前工具用于验证 GitLink 的 Release/Attachment API 兼容性；它不会执行研错库备份，也不会实现增量同步。

## `probe_gitlink.py`

运行：

```powershell
python tools/probe_gitlink.py
```

令牌按以下顺序读取：

1. 环境变量 `YANCUO_GITLINK_TOKEN`；
2. 环境变量 `GITLINK_TOKEN`；
3. 系统凭据 `Yancuo / yancuo_gitlink_token`（需要安装 `keyring`）。

脚本只输出脱敏状态和 HTTP 结果，不打印令牌明文。成功运行会覆盖生成 [`gitlink_compat_report.md`](gitlink_compat_report.md)；未配置令牌时返回非零退出码并写出提示。探测需要网络，建议在 API 或配置变更后手动运行。

## 与运行时云备份的边界

Windows 运行时的 `GitLinkProvider` 位于 `apps/windows/src/yancuo_win/cloud/`，使用 Release + Attachment 完整 `.ebpack` 快照。探测脚本的结果只能说明接口兼容性，不能替代备份恢复验收；GitLink 旧 Release 删除仍需在网页手动清理，远端增量 Operation 通道尚未实现。
