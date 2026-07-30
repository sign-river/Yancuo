# Windows Qt 启动与切页闪烁故障复盘

> 本文属于故障复盘资料，不维护后续任务状态；需要继续处理的问题应进入 [`../问题收集箱.md`](../问题收集箱.md)。

> 日期：2026-07-28  
> 状态：已解决  
> 影响范围：Windows PySide6 客户端启动、页面首次构造和页面切换

## 1. 故障现象

- 程序启动前出现一个或多个快速显示后消失的小窗口。
- UI 重构后，进入录题、笔记等页面时也可能发生短暂闪烁。
- 程序功能正常，没有崩溃或数据损坏，但视觉体验明显异常。
- 问题只在新版未提交改动中出现；Git 提交 `541b94e`（“优化复习界面”）无此问题。

## 2. 最终根因

全局 UI 重构新增了 `PageHeader` 组件。其构造函数包含：

```python
self.description.setVisible(bool(description))
```

当说明文字非空时，这行代码会在页面及其父级尚未完整挂入主窗口前，主动触发子控件的显示流程。在 Windows Qt 中，构造期的显式显示请求可能短暂激活尚未稳定归属的窗口层级。

录题页一次构造多个带说明的 `PageHeader`，笔记、复习、设置等页面也会构造 Header，因此多个显示请求叠加后表现为连续小窗口闪烁。

正确实现位于 `apps/windows/src/yancuo_win/ui/widgets.py`：

```python
self.description = QLabel(description)
self.description.setObjectName("PageHint")
self.description.setWordWrap(True)
if not description:
    self.description.hide()
```

非空说明不需要调用 `show()` 或 `setVisible(True)`。Qt 会在父页面正常显示时自动显示它；只有空说明需要显式隐藏。

## 3. 为什么最初判断错误

### 3.1 误判为 QWebEngineView 创建问题

题目详情、笔记和复习页面都包含网页渲染控件，因此最初将闪烁归因于 Chromium 原生窗口。曾尝试：

- 延迟创建整个业务页面；
- 页面可见后再创建网页控件；
- 使用后台 `QWebEnginePage` 和 `QPdfView`；
- 恢复原生 `QStackedWidget`。

这些改动会改变闪烁次数或出现时机，但没有消除真正的构造期显示请求，因此属于治标不治本。

### 3.2 测试版本混淆

主目录、stash 和多个调试目录都能通过已安装的 Python 入口运行，而窗口标题相同。一度将旧提交运行结果误认为新版结果。

后续 A/B 测试采用以下约束：

- 每个 worktree 使用独立绝对 `PYTHONPATH`；
- schema 18 和 schema 19 使用独立数据目录；
- 每个变体使用唯一窗口标题，例如 `A 基线`、`B 完整 stash`；
- 每次只运行一个实例；
- 测试窗口必须主动置前，否则“未看到闪烁”不算有效结果。

## 4. 有效定位过程

| 变体 | 内容 | 结果 |
| --- | --- | --- |
| A | `541b94e` 原始代码 + schema 18 | 无闪烁 |
| B | 完整 stash + schema 19 | 多次闪烁 |
| C | B + 旧主题 | 仍闪烁，排除主题 |
| D | C + 旧阅读器 | 仍闪烁，排除公式渲染器 |
| E | D + 旧主窗口 | 仍闪烁，排除主窗口外壳 |
| F | 旧 UI 页面 + 新业务/数据库 | 无闪烁，锁定业务页面 UI 改动 |
| G | 仅恢复新版录题、笔记、详情页 | 闪烁 |
| H | 仅新版题目详情页 | 无闪烁 |
| I | 仅新版录题页 | 小闪一次 |
| L | 仅新版笔记页 | 少量闪烁 |
| M | 新版笔记页 + Header 修复 | 无闪烁 |
| N | 完整 stash + Header 修复 | 无闪烁 |

录题页和笔记页的共同新增行为是构造带非空说明的 `PageHeader`。题目详情页初始说明为空，因此没有触发同样的问题。这一差异最终定位到 `setVisible(True)`。

## 5. 回归保护

`tests/unit/test_theme.py` 包含回归测试：

```python
def test_page_header_does_not_show_children_during_construction(...):
    ...
    assert True not in visibility_requests
```

测试保证 `PageHeader` 构造期间不会主动请求显示任何标签。

修复完成后的验证结果：

- 完整单元测试通过：`247 passed`；
- 新增 Header 回归测试通过；
- Ruff 静态检查通过；
- 使用正式 schema 19 数据多次冷启动，无闪烁；
- 页面切换无闪烁。

## 6. 后续排查清单

遇到类似问题时按以下顺序处理：

1. 使用唯一窗口标题和绝对 `PYTHONPATH`，先确认实际运行版本。
2. 使用独立数据目录解决 schema 不兼容，不要直接修改数据库版本号。
3. 建立已知正常的 Git 基线和完整问题版本，进行文件组二分。
4. 优先检查构造函数中的 `show()`、`setVisible(True)`、`exec()` 和顶层窗口标志。
5. 检查 QWidget 是否在父子层级稳定前被显式显示。
6. 区分“顶层小窗口闪现”和“页面内容空白后加载”，不要仅凭视觉猜测 WebEngine。
7. 每次只改变一个变量，并重复冷启动至少两次。
8. 修复后用完整版本和正式数据复测，而不是只测试最小复现版本。

## 7. Qt UI 编码约束

- 构造 QWidget 时只建立结构和初始数据，不主动显示控件。
- 非空控件依赖父级正常显示流程，不调用 `setVisible(True)`。
- 仅对默认需要隐藏的控件调用 `hide()` 或 `setVisible(False)`。
- 需要运行期切换可见性时，应在页面已挂入稳定父级后执行。
- 新增可复用容器组件时，必须增加“构造期不主动显示子控件”的回归测试。
- UI 问题的 A/B 版本必须具有明确的构建标识，禁止仅靠工作目录或肉眼推断版本。
