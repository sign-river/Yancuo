# 开发工具

本目录放置开发期探测与验收脚本及其输出报告，不属于 Windows/Android 运行时。

## `performance_baseline.py`

从仓库根目录运行：

```powershell
python tools/performance_baseline.py --samples 5
```

脚本在系统临时目录创建带隔离标记的资料，生成至少 1200 道题目和 300 篇笔记，
采样冷/热启动、主窗口构建、题库刷新、本地搜索、题目/笔记列表查询和列表滚动，
最后自动清理整份隔离资料。它不会读取或写入正式 `YANCUO_DATA_ROOT`。

结果默认只输出到终端；需要留存发布节点报告时可增加
`--output <仓库外路径.json>`。报告包含设备、Python/Qt/SQLAlchemy 版本、数据规模、
样本数、中位数、范围与 Tukey 异常值。冷启动表示新解释器内的第一次
`bootstrap_runtime`，不会尝试清空操作系统文件缓存。

## `sync_release_matrix.py`

发布前从仓库根目录运行：

```powershell
python tools/sync_release_matrix.py
```

该入口只使用临时数据目录和测试内的 LocalFolder/GitHub 模拟实现，不读取真实用户资料，也不访问远端仓库。它覆盖完整 `.ebpack` 快照的发布失败、校验与恢复回滚，以及 LocalFolder/GitHub Operation 的重复拉取、附件篡改、配置索引异常和跨资料档案隔离。

## `probe_gitlink.py`

运行：

```powershell
python tools/probe_gitlink.py
```

令牌按以下顺序读取：

1. 环境变量 `YANCUO_GITLINK_TOKEN`；
2. 环境变量 `GITLINK_TOKEN`；
3. 系统凭据 `Yancuo / yancuo_gitlink_token`（需要安装 `keyring`）。

脚本只输出脱敏状态和 HTTP 结果，不输出令牌明文或长度。成功运行会覆盖生成 [`gitlink_compat_report.md`](gitlink_compat_report.md)；未配置令牌时返回非零退出码并写出提示。探测需要网络，建议在 API 或配置变更后手动运行。

## 与运行时云备份的边界

Windows 运行时的 `GitLinkProvider` 位于 `apps/windows/src/yancuo_win/cloud/`，使用 Release + Attachment 完整 `.ebpack` 快照。探测脚本的结果只能说明接口兼容性，不能替代备份恢复验收；GitLink 旧 Release 删除仍需在网页手动清理，远端增量 Operation 通道尚未实现。
