# 安全策略

## 漏洞报告

如果你发现 nanobot 的安全漏洞，请不要公开提交 Issue。建议：

1. 在 GitHub 提交私密安全通告（Security Advisory）
2. 或直接联系维护者：`xubinrencs@gmail.com`
3. 报告中尽量包含：
   - 漏洞描述
   - 复现步骤
   - 潜在影响
   - 修复建议（可选）

## 安全最佳实践

- 不要将 API Key 提交到仓库
- 配置文件权限尽量收紧（如仅当前用户可读写）
- 生产环境务必配置 `allowFrom`
- 避免以 root 身份运行
- 定期更新依赖并关注安全公告

## 关键配置建议

- 建议启用工作区限制：`tools.restrictToWorkspace`
- Linux 生产环境建议启用执行沙箱：`tools.exec.sandbox = "bwrap"`
- 对外接入渠道应启用访问白名单

## 生产部署建议

- 使用独立运行用户
- 监控日志并定期审计
- 为 API 设置额度与速率限制
- 定期轮换密钥与凭据

## 安全检查清单

- [ ] API 密钥未硬编码在代码中
- [ ] 配置文件权限已收紧
- [ ] 所有渠道均设置 `allowFrom`
- [ ] 未使用 root 运行
- [ ] 依赖已升级到安全版本
- [ ] 已建立日志监控与告警

## 参考

- GitHub 安全公告：<https://github.com/HKUDS/nanobot/security/advisories>
- 发布说明：<https://github.com/HKUDS/nanobot/releases>
