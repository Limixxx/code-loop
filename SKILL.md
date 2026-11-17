---
name: code-loop
description: "GitHub Issue 状态驱动的自动化编码代理：4 阶段工作流（Worktree 初始化 → 提交方案PR → 提交代码PR → 环境清理）"
version: 3.4.0
author: Limix
license: MIT
platforms: [linux, macos, windows]
---

# code-loop

GitHub Issue 状态驱动的自动化调度技能。扫描指定仓库中分配给当前用户的 open issues，按项目看板状态分类，并生成子代理派发计划。

## 入口

在项目目录中运行技能中的 `scripts/issue_dispatch.py` 脚本：

```bash
python {技能目录}/scripts/issue_dispatch.py
```

## 前置依赖

- Python 3
- GitHub CLI (`gh`) 已安装并登录

## 输出格式

```json
{
  "current_user": "<用户名>",
  "config": { ... },
  "classified": {
    "<状态名>": [
      { "number": <issue编号>, "title": "...", "type": "Bug|Feature", "status": "..." }
    ]
  },
  "dispatch_plan": {
    "items": [
      {
        "task_type": "Task1|Task2|Task3|Task4",
        "issue_number": <编号>,
        "issue_title": "...",
        "issue_type": "Bug|Feature",
        "description": "...",
        "query": "...",
        "subagent_type": "general_purpose_task",
        "cwd": "<执行目录>"
      }
    ],
    "total_tasks": <总数>,
    "summary": { "task1_count": N, "task2_count": N, "task3_count": N, "task4_count": N }
  }
}
```

## 调度工作流

`dispatch_plan.items` 中每一项已包含子代理所需的全部参数，主调度器直接透传即可。
主调度器在收到 `dispatch_plan` 后，按 `items` 并行调用 `Task` 工具，最多 3 个。不得合并、省略或修改 `query` 内容。若 `total_tasks` 为 0，则无需启动任何子代理，直接结束。

1. 更新 TODO 列表标记各阶段进度
2. 按照如下格式汇报汇总结果：

** 各任务执行统计 **
| 任务编号 | 任务名称         | 成功 | 跳过 | 失败 |
|--------|----------------|------|------|------|
| Task1  | Worktree 初始化 | N    | N    | N    |
| Task2  | 方案PR          | N    | N    | N    |
| Task3  | 代码PR          | N    | N    | N    |
| Task4  | 清理空间        | N    | N    | N    |

## 注意事项

主调度器（本技能调用方）的职责边界：

- **仅执行脚本**：运行 `scripts/issue_dispatch.py` 获取 JSON 输出。
- **机械派发**：读取 `dispatch_plan.items`，对每一项调用 `Task` 工具启动子代理。
- **禁止越权**：主调度器不解析 issue 内容、不生成代码、不修改仓库状态、不读取工作流内容、不执行工作流脚本。所有实际工作必须由子代理完成。
- **并行限制** — 最多 3 个并行子代理；
- **禁止猜测** — 主调度严格按照流程执行1次流程即可，禁止自行猜测流程；
- **禁止gh** — 主调度禁止调用gh或git命令，禁止使用gh或git相关技能；
- **禁止调用code-loop技能** — 主调度禁止在subagent中再调用code-loop技能，只能通过主代理调用；