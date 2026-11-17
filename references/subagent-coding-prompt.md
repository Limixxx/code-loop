# 子代理提示词：编码 + 测试 + 服务验证（Task3）

## 输出约束

1. 按照解决方案修改点清单逐一实现业务代码，覆盖所有修改点
2. 每个代码变更必须配套对应验收测试，测试必须全部通过
3. 启动本地服务验证无报错，核心 API 端点返回数据正常
4. 测试报告和服务验证日志写入 `${ISSUE_DOCS_PREFIX}/{issueNum}/` 目录
5. 若存在上一轮代码 PR 驳回意见，必须修复最新一条代码问题，同步更新测试用例

## 测试报告模板

写入路径：`${ISSUE_DOCS_PREFIX}/{issueNum}/test_report.md`

```markdown
---
issue_number: {N}
issue_type: Bug | Feature
test_date: YYYY-MM-DD
test_result: pass | fail
---

# Issue #{N} 测试报告

## 验收测试结果
| 用例编号 | 测试描述 | 结果 |
|----------|---------|------|
| 1 | ... | ✅ pass / ❌ fail |
| 2 | ... | ✅ pass / ❌ fail |

## 类型检查
- 结果: pass | fail

## 失败用例详情（如有）
...

## 边界条件覆盖
- ...
```

## 服务验证日志模板

写入路径：`${ISSUE_DOCS_PREFIX}/{issueNum}/service_verify.md`

```markdown
---
issue_number: {N}
verify_date: YYYY-MM-DD
service_status: ok | error
---

# Issue #{N} 服务验证日志

## 服务启动
- HTTP 服务: ✅ ok / ❌ error
- MCP 服务: ✅ ok / ❌ error

## API 端点验证
| 端点 | 方法 | 状态 | 响应摘要 |
|------|------|------|---------|
| /api/... | GET | ✅ 200 | ... |

## 异常信息（如有）
...
```

## 硬性限制
- **禁止跳过测试**
- **禁止忽略服务启动报错**
- **所有代码变更必须配套对应验收测试**
- **禁止在主分支创建任何临时文件**
