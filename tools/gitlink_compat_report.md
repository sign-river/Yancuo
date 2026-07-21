# GitLink API 兼容性验证报告

- 探测时间：由 `tools/probe_gitlink.py` 生成
- 令牌来源：已提供（已脱敏）
- 令牌长度：40

## 结果摘要

- **PASS** `GET /api/{owner}/{repo}/releases.json` — HTTP 200
- **PASS** `GET /api/projects.json?category=created` — HTTP 200; 疑似未按用户过滤（需结合 owner 配置）
- **FAIL/UNKNOWN** `GET /api/v1/user.json` — HTTP 200; body_keys≈status,message
- **FAIL/UNKNOWN** `GET /api/v1/repos/{owner}/{repo}/contents/` — HTTP 200; body_head=<!doctype html><html lang="zh-hans-CN" class="notranslate translated-ltr" translate="no"><head><title>GitLink | 新一代开源创新服

## 结论与实现策略

1. Release 列表接口 `/api/{owner}/{repo}/releases.json` 可用（已验证公开仓库）。
2. `/api/v1/user.json` 当前返回 `{status,message}`，不能可靠识别当前用户；`/api/v1/repos/.../contents/` 返回 HTML 页面而非 JSON。
3. `projects.json` 即使带令牌仍像全站列表；私有库请在网页创建后填写 owner/name。
4. 研错库阶段 G：**LocalFolderProvider 作为完整可测后端**；GitLinkProvider 实现同一接口，按 capabilities 降级（附件上传默认关闭），令牌仅存系统凭据。
5. 上传顺序仍遵守：先上传完整 `.ebpack` 并校验，再更新 `latest.json`。

## 安全

- 本报告不含令牌明文。
- 若令牌曾出现在聊天记录中，建议在 GitLink 设置中轮换。

