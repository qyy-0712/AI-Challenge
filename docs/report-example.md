# PR 审查报告示例

## [BLOCKER] Compile / Parse Errors
- backend/app/main.py:12 SyntaxError: 缺少括号

## [BLOCKER] Static Defects
- backend/app/service.py:44 [ResourceLeak] 文件句柄未关闭

## Potential Risks（AI 推理）
- [high] backend/app/api.py:82 返回值未检查，可能导致空指针异常。建议在调用处增加 None 检查并抛出显式错误。
- [medium] frontend/src/hooks/useAuth.ts:37 未处理 token 过期导致静默失败。建议加入 401 拦截器并触发重新登录。

## [ARCH] Architecture / Dependency Issues
- LayerViolation: `api` 层直接依赖 `db` 包，未通过 `repository` 抽象。

## [PERF] Performance / Scalability
- 缓存未命中时全量扫描，建议增加分页与索引。

## [MAINT] Maintainability
- 关键路径缺少单元测试，建议补充针对边界条件的测试。

## [REQ] Requirement Alignment
- 需求要求“仅管理员可删除资源”，当前缺少权限校验。

