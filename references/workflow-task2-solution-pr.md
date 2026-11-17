# Workflow Task2: Ready → 方案生成 & 方案PR提交

> 本工作流由主代理通过 Task 工具派发。

## 约束

- 仅操作当前 Issue，禁止直接调用 gh 或 git 命令（所有 gh/git 操作通过脚本执行）
- 去重/判重：从 `flow_meta.json` 读取 PR 号 + `gh pr view` — 禁止 `gh pr list`
- 项目代码仅供读取参考，禁止修改任何业务源码、测试代码
- 技能代码仅供读取参考，禁止修改任何技能源码和文件
- 配置文件路径由主代理传入（.env 文件），禁止硬编码配置值
- 工作流文件已定义脚本路径和参数提取方式，禁止自行推断或修改
- 脚本失败后的重试策略由工作流文件规定，禁止自行决定
- 禁止在subagent中再调用code-loop技能，只能通过主代理调用

## 执行步骤

### Step 1: 前置检查（脚本 — phase pre）

参数提取：

| 参数 | 提取方式 |
|------|----------|
| `issue_number` | prompt 中「执行 Task2 for Issue #N」行的数字 N |
| `skill_dir` | prompt 中「技能目录: 」行冒号后的路径 |
| `worktree_path` | prompt 中「★ 子代理执行目录（cwd）: 」行冒号后的路径 |
| `env_file_path` | prompt 中「配置文件: 」行冒号后的路径 |

```bash
python {skill_dir}/scripts/task2_solution_pr.py --issue {issue_number} --env-file {env_file_path} --phase pre --cwd "{worktree_path}"
```

脚本输出 JSON 字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| action | string | SKIP / ABORT / FAIL / GENERATE |
| issue_title | string | Issue 标题 |
| issue_body | string | Issue 正文 |
| issue_labels | string[] | 标签列表 |
| issue_type | string | Bug / Feature |
| rejection_comments | string | 上一轮驳回意见（可为空） |
| solution_path | string | 方案文档相对路径 |

根据 `action` 字段决策：

| action | 处理方式 |
|--------|---------|
| SKIP | 终止流程（PR 评审中 / run.lock 存在 / PR 已合并已处理） |
| ABORT | 终止流程（PR 被关闭未合并） |
| FAIL | worktree 不存在，终止流程 |
| GENERATE | ★ 继续执行 Step 2~3 |

### Step 2: 生成解决方案文档

参数提取：

| 参数 | 提取方式 |
|------|----------|
| `issue_title` | Step 1 JSON 中 `issue_title` 字段 |
| `issue_type` | Step 1 JSON 中 `issue_type` 字段 |
| `issue_body` | Step 1 JSON 中 `issue_body` 字段 |
| `issue_labels` | Step 1 JSON 中 `issue_labels` 字段 |
| `rejection_comments` | Step 1 JSON 中 `rejection_comments` 字段（可能为空） |
| `solution_path` | Step 1 JSON 中 `solution_path` 字段 |

生成方案文档，写入 `{worktree_path}/{solution_path}`（具体输出约束和格式由 `{skill_dir}/references/subagent-solution-prompt.md` 定义）

### Step 3: 提交方案 PR（脚本 — phase post）

```bash
python {skill_dir}/scripts/task2_solution_pr.py --issue {issue_number} --env-file {env_file_path} --phase post --cwd "{worktree_path}"
```

## 错误处理

- **脚本失败后最多重试 3 次**
- **禁止自行分析错误**
- **禁止修改或编写新的代码**
- 3 次仍失败则将错误输出原样报告
- 禁止调用gh或git命令，禁止使用gh或git相关技能；
