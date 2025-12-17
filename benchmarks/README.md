# Benchmark 设计与运行说明

目标：在无历史上下文情况下，仅基于 PR diff 发现真实缺陷与风险。

## 数据结构
- `cases.json`：基准用例集合，一例一 bug。
- 分类：`CompileError | StaticDefect | SemanticBug | RegressionRisk | ArchitectureDrift | RequirementMismatch`
- 字段：
  - `id`: 唯一标识
  - `name`: 用例名称
  - `category`: 上述分类之一
  - `repo`: GitHub 仓库（或镜像）
  - `base_ref`: 基线分支/commit
  - `pr_ref`: 引入缺陷的 PR 或分支
  - `diff`: 关键 diff 片段（可选）
  - `expected_findings`: 期望命中的问题要点

## 构造流程
1) 选择真实项目的 bug-introducing PR。
2) 提取引入缺陷的 diff，构造干净 base，再提交该 diff 为新的 PR。
3) 将 PR 信息记录在 `cases.json`。

## 运行方式
```bash
# 1) 启动后端
cd backend && uvicorn app.main:app --reload

# 2) 调用 /review，传入 repo_full_name 与 pr_number
curl -X POST http://localhost:8000/review \
  -H "X-Github-Token: $GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repo_full_name": "owner/repo", "pr_number": 123}'
```

## 评价指标
- 命中率：是否发现预期缺陷（匹配文件/行/类型）
- 噪音：误报数量
- 解释质量：理由是否清晰可执行

