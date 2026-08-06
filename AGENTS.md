# AGENTS.md — Yancuo 开发约定（AI 必读）

本文件是仓库对 AI 代理的强制约定。开始任何任务前先读本文件；涉及 UI、测试、
提交时尤其遵守下列规则，避免已知的高成本返工。

## 项目结构
- Windows 桌面客户端：`apps/windows/`（PySide6，源码在 `apps/windows/src/yancuo_win/`）。
- 单元测试：`tests/`；Qt 测试由 `tests/conftest.py` 固定为 offscreen 平台。
- 其他目录：`android/`、`cloudbase/`、`protocol/`、`docs/`。

## 工作树与分支（重要，规则按角色区分）
- 本仓库使用 git worktree：**功能开发**在 `codex/*` 分支工作树进行，改动提交到该分支，
  由主线（main）负责合并。功能分支上的 AI 不要把功能改动直接提交到 main。
- 若你正在 **main 主线**上工作（例如负责整合/合并 `codex/*` 分支、解决冲突、或用户明确要求直接改 main），
  **本规则不禁止你在 main 上提交**——合并、解决冲突、用户点名的主线改动正是主线 AI 的职责。
- 不要在规则里硬编码本机绝对路径（如某台机器上的 `D:\project\Yancuo`）：不同机器/克隆位置不同。
  判断“当前是否 main 工作树”用 `git branch --show-current`，而不是路径。
- 本机（用户机器）上 `yancuo` 命令通过 editable 安装指向 main 工作副本的 `apps/windows/src`，
  因此运行中的应用要等分支合并进 main 后才会带上新改动；这条只影响“何时能看到效果”，不是修改权限约束。

## 测试（最高优先级）
- 统一从仓库根目录运行 `python -m pytest`。
- `tests/conftest.py` 会把仓库本地 `apps/windows/src` 插入 `sys.path` 最前，
  保证测的是当前代码而不是其他 checkout 的 editable 安装。**不要再设置 PYTHONPATH**；
  若怀疑加载了错误副本，先打印 `yancuo_win.__file__` 确认。
- 小改动先跑定向用例（如 `python -m pytest tests/unit/test_theme.py tests/unit/test_dropdown.py tests/unit/test_library_views.py`），
  全量 `tests/unit`（约 5 分钟）只在最后跑一次。
- 调试 Qt 时可用 offscreen 或临时窗口脚本，但不要提交会弹出真实窗口的测试。

## UI 问题排查纪律（防止截图返工）
- **先看代码与近期提交**：`git log --oneline -20 -- <相关文件>` 定位最近改动，
  再结合现象缩小范围；多数 UI 回归来自最近的提交。
- **截图只做一次快速确认**（可用 agent-vision/GLM OCR 一次），不要反复裁剪、
  放大、像素对比去猜控件。
- 无法从截图确认控件时，**直接问用户控件名称/路径**，不要靠猜继续深挖。
- 疑似布局/遮罩/弹层问题，先写最小复现脚本（创建控件→触发状态→检查几何/mask），
  用最小实验确认根因后再改代码。

## 中文与编码（防止乱码返工）
- 所有源码、测试、文档、提交信息统一 UTF-8。
- **不要**在 PowerShell 命令行或 here-string 里直接内联中文再管道给 python/git，
  可能被转成 `?` 或把 `git commit -m` 拆成 pathspec。
- 正确做法（任选）：
  - 管道前执行 `$OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8`；
  - 或在 Python 源码里用 `\uXXXX` 转义写中文；
  - 提交信息先写文件，再 `git commit -F <file>`。

## 提交约定
- 提交到当前 `codex/*` 分支，message 用中文，格式参考仓库历史（如 `fix(ui): ...`）。
- 提交前清理调试临时文件（截图、`*.tmp`、调试测试文件），不要提交进仓库。
