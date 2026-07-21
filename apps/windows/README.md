# 研错库 Windows 客户端

当前进度：**阶段 C（AI 导入与审核）**。

## 环境

```powershell
cd apps/windows
pip install -e ".[dev]"
python -m yancuo_win
```

## 阶段 C 用法

1. 导入带原图的题目  
2. 选中题目 → **AI 识别**（默认 mock，后台线程）  
3. **AI 审核** → 查看字段差异 → 接受 / 拒绝  
4. 选中题目 → **撤销 AI**（恢复接受前内容）  
5. **AI 任务** 查看进度与费用粗统计  

真实模型：在配置中将 `default_provider` 设为 `openai_compatible`，并设置环境变量 `YANCUO_AI_API_KEY`（禁止写入仓库）。

## Schema

启动自动迁移至 **schema_version=2**。说明见 `docs/05_schema_v2_变更说明.md`。
