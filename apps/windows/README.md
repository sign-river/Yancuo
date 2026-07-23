# 研错库 Windows 客户端

当前进度：路线图 A–K 的代码与协议验收范围已落地，并完成第一轮同步、归档恢复、云锁和 wheel/CI 加固。主界面现已加入“工作台 + 录题”任务式流程，手动与 AI 图片录题可在同一页面连续完成；AI 上传支持即时预览，Faro API 已作为默认真实识图提供商，回收站重复录入与永久删除阻断已修复。AI 确认、题目详情与今日复习已统一支持离线 LaTeX 渲染，题库可进入独立详情阅读页。加密、PDF、远端增量和正式安装包仍未实现。
完成登记见仓库根目录 [`docs/08_完成更新记录.md`](../../docs/08_完成更新记录.md)。

## 运行

```powershell
cd apps/windows
pip install -e ".[dev]"
python -m yancuo_win
```

### 首次启用 Faro AI

1. 打开“设置”，在“AI（Faro / OpenAI 兼容）”中粘贴 Faro `sk-...` Key，点击“保存 AI 密钥”。
2. 从 Faro 模型广场复制一个支持图片输入的模型 ID，填入“图片模型 ID”。
3. 点击“测试 Faro 连接”；通过后点击“保存并应用 AI 设置”。
4. 返回“录题 → AI 录题”上传图片。底部应显示“Faro API（真实识图）”，结果中不再出现 `(Mock)`。

密钥只存入 Windows 凭据管理器，不写入 TOML 或 `preferences.json`。也可在启动程序的 PowerShell 中临时设置：

```powershell
$env:FARO_API_KEY = "你的 Faro Key"
python -m yancuo_win
```

## 界面结构（2026-07）

- 左侧：工作台 / 录题 / 题库 / 复习 / 数据与同步 / 设置
- 录题：手动表单，或 AI 图片上传、后台处理、公式阅读预览、字段编辑和确认入库
- 题库：状态与分类筛选、题目列表；双击题目或点击“打开详情”进入专用阅读页
- 复习：题干、答案和解析复用与详情页一致的离线公式渲染
- 密钥：设置中保存到系统凭据（不进 TOML）

## ebpack

- **导出 ebpack** / **导入 ebpack**（数据页）
- 权威数据：`database/snapshot.sqlite` + `assets/objects`
- 恢复前校验 `checksums.sha256`；损坏或 schema 过高会拒绝
- 协议：`protocol/ebpack-format-v1.md`

旧版 zip 备份仍可用，新迁移请优先 `.ebpack`。

## 干净安装与构建烟测

项目使用 `src/` 布局，wheel 内置默认 TOML 与工作区 schema。源码 checkout
优先读取仓库根的 `config/` 与 `protocol/`；普通 wheel 安装读取包内资源，
不再依赖固定层级的 `__file__.parents`。

```powershell
cd apps/windows
py -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install ".[dev]"
.\.venv\Scripts\python -m ruff check src ../../tests
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m pip wheel --no-deps . -w dist
```

需要便携数据目录时可设置 `YANCUO_DATA_ROOT`。未设置时，源码 checkout 使用
`apps/windows/.yancuo_data`；安装态使用用户数据目录（Windows 为
`%LOCALAPPDATA%\Yancuo`，Linux/CI 为 `$XDG_DATA_HOME/Yancuo`）。
