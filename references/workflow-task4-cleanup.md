# Workflow Task4: Done → Worktree 清理回收

> 本工作流由主代理通过 Task 工具派发。
> 轻量任务，Task4 子代理内部禁止再启动子代理，无 LLM 生成环节。

## 约束

- 仅清理当前 Issue 的分支/worktree/docs，禁止操作其他资源
- 配置文件路径由主代理传入（.env 文件），禁止硬编码配置值
- 工作流文件已定义脚本路径和参数提取方式，禁止自行推断或修改
- 脚本失败后的重试策略由工作流文件规定，禁止自行决定
- 技能代码仅供读取参考，禁止修改任何技能源码和文件
- 禁止在subagent中再调用code-loop技能，只能通过主代理调用

## 执行命令

```bash
python {skill_dir}/scripts/task4_cleanup.py --issue {issue_number} --env-file {env_file_path} --cwd "{cwd}"
```

## 参数提取

从 prompt 中提取以下参数：

| 参数 | 提取方式 |
|------|----------|
| `skill_dir` | prompt 中「技能目录: 」行冒号后的路径 |
| `--issue` | prompt 中「执行 Task4 for Issue #N」行的数字 N |
| `--env-file` | prompt 中「配置文件: 」行冒号后的路径 |
| `--cwd` | prompt 中「★ 子代理执行目录（cwd）: 」行冒号后的路径 |

## 错误处理

- **脚本失败后最多重试 3 次**
- **禁止自行分析错误**
- **禁止修改或编写新的代码**
- 3 次仍失败则将脚本的 stderr 输出原样报告
