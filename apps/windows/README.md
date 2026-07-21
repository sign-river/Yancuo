# 研错库 Windows 客户端

当前进度：**阶段 B 本地 MVP**。

## 环境

- Python 3.11+

```powershell
cd apps/windows
pip install -e ".[dev]"
python -m yancuo_win
```

## 阶段 B 已具备

- 科目 / 章节 / 标签
- 错题 CRUD、收件箱 ↔ 正式库、回收站
- 图片导入（去重、原图不可变）
- Markdown 编辑（文本框级撤销）
- 搜索筛选
- 本地 zip 备份与恢复
- Word 导出（`python-docx`）

## 已知降级

- **PDF 导出**：本阶段未接入 WeasyPrint，避免 Windows 依赖阻断；打印路径以 Word 为准。

## 迁移

```powershell
python -m yancuo_win.data.migrate_cli
```
