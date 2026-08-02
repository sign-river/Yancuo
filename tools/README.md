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

该入口只使用临时数据目录和测试内的 LocalFolder/CloudBase 模拟实现，不读取真实用户资料，也不访问远端服务。它覆盖完整 `.ebpack` 快照的发布失败、校验与恢复回滚，以及 LocalFolder/CloudBase Operation 的重复拉取、附件篡改、配置索引异常和跨资料档案隔离。

## 与运行时云备份的边界

Windows 正式远端通道统一为 CloudBase 网关；LocalFolder 仅用于离线和局域测试。模拟矩阵只能验证客户端协议与失败语义，不能替代部署后的真实小包上传、下载、锁竞争和恢复验收。
