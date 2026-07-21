# 研错库 Windows 客户端

当前进度：**阶段 D（外部工作区）**。

## 运行

```powershell
cd apps/windows
pip install -e ".[dev]"
python -m yancuo_win
```

## 工作区

1. 选中题目 → **导出工作区**
2. 用外部编辑器只改 `problems/*/problem.md`（勿改 SQLite）
3. **导入工作区** → 在 **AI 审核** 中查看 diff（冲突须强制采用外部或保留内部）

格式说明：`protocol/workspace-format-v1.md`
