# GitLink API 兼容性验证报告

- 探测时间：由 `tools/probe_gitlink.py` 生成
- 令牌来源：已提供（已脱敏）

## 结果摘要

- **PASS** `GET /api/{owner}/{repo}/releases.json` — HTTP 200
- **PASS** `GET /api/projects.json?category=created` — HTTP 200; 疑似未按用户过滤（需结合 owner 配置）
- **FAIL/UNKNOWN** `GET /api/v1/user.json` — HTTP 200; body_keys≈status,message
- **FAIL/UNKNOWN** `GET /api/v1/repos/{owner}/{repo}/contents/` — HTTP 200; body_head=<!doctype html><html lang="zh-hans-CN" class="notranslate translated-ltr" translate="no"><head><title>GitLink | 新一代开源创新服

## 结论与实现策略

1. Release 列表接口 `/api/{owner}/{repo}/releases.json` 可用（已验证公开仓库）。
2. `/api/v1/user.json` 当前返回 `{status,message}`，不能可靠识别当前用户；`/api/v1/repos/.../contents/` 返回 HTML 页面而非 JSON。
3. `projects.json` 即使带令牌仍像全站列表；私有库请在网页创建后填写 owner/name。
4. 此探测未验证 `version_id` 的条件更新或等价原子锁，不能作为 GitLink 增量同步的实现依据。
5. 研错库当前仅允许 GitLink 用于完整 `.ebpack` 快照；增量 Operation 通道继续由 LocalFolderProvider 提供。

## 原子锁验证（2026-07-26）

- 私有测试仓库 `signriver/yancuo-sync-test`：两个客户端先读取同一个 Release 的 `version_id`，随后依次用该旧值提交不同 body。
- 第二次旧版本写入被服务器接受，最终 body 为第二个客户端的值。
- 结论：`version_id` 不支持条件更新或等价原子锁。GitLink 增量 Operation 通道不得实现或启用。

## 安全

- 本报告不含令牌明文。
- 若令牌曾出现在聊天记录中，建议在 GitLink 设置中轮换。
