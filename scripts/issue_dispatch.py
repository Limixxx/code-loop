#!/usr/bin/env python3
"""issue_dispatch.py — Issue 派发清单生成器（依赖感知版）

扫描当前仓库中分配给 ASSIGNEE 的 open issues，按项目看板状态分类，
并根据 GitHub 原生 "blocked by" 依赖关系过滤——仅当 issue 的所有
阻塞方均已 CLOSED 时才纳入派发清单。

工作流程（一键完成）：
  1. 从 .worktree/.env 加载配置（仓库、看板、状态映射等）
  2. 通过 gh CLI 查询分配给当前用户的 open issues（含 blockedBy 关系）
  3. 按看板状态分类（Backlog / Ready / In progress / Done → Task1~Task4）
  4. 依赖过滤：跳过仍有 OPEN 阻塞方的 issue，记录到 skipped_by_dependency
  5. 为通过过滤的 issue 预计算 Task 工具调用参数（query / cwd / subagent_type）

主代理（LLM）只需机械读取 dispatch_plan.items 并调用 Task 工具，
无需再做任何决策——消除调度幻觉窗口。

用法：
  python issue_dispatch.py [--skill-dir DIR] [--env-file PATH]

输出 JSON 结构（关键字段）：
  {
    "current_user": "Limixxx",
    "config": { ... },                          # 从 .env 加载的完整配置
    "classified": { "Backlog": [...], ... },    # 按看板状态分组的 issue 列表
    "dispatch_plan": {
      "items": [ { task_type, issue_number, query, cwd, ... }, ... ],
      "total_tasks": N,
      "summary": { "task1_count": N, ... },
      "skipped_by_dependency": [                # 因依赖未满足而跳过的 issue
        { "number": N, "title": "...", "open_blockers": [N, ...] }
      ]
    }
  }
"""

import os
import sys
import json
from typing import Any, Dict, List, Optional, Set

from common import load_config_from_env, _run_gh, _PARAM_DEFS_BASE


# ============================================================
# 第一部分：配置加载（从 .env 文件读取）
# ============================================================

# 参数定义：基于公共基础集合，本脚本无额外扩展
_PARAM_DEFS = _PARAM_DEFS_BASE.copy()


def get_issue_worktree_path(config: Dict[str, Any], issue_number: int) -> str:
    """根据配置计算指定 issue 的 worktree 完整路径。

    路径格式：{WORKTREE_PREFIX}/{BRANCH_PREFIX}{issue_number}
    例如：.worktree/issue-802
    """
    return os.path.abspath(os.path.join(
        config.get("WORKTREE_PREFIX", ".worktree"),
        config.get("BRANCH_PREFIX", "issue-") + str(issue_number),
    ))


# ============================================================
# 第二部分：GitHub CLI 操作（查询 + 分类 + 依赖检查）
# ============================================================

def gh_query_issues_with_status(
    repo: str,
    assignee: str,
    valid_labels: Optional[Set] = None,
) -> List[Dict]:
    """查询已分配 issue 及其项目看板状态和依赖关系，按 label 筛选。

    通过 gh issue list 一次性获取 issue 的看板状态 (projectItems) 和
    原生依赖关系 (blockedBy)，避免对每个 issue 单独查询。

    Args:
        repo: GitHub 仓库 (owner/name)
        assignee: 被分配人用户名
        valid_labels: 合法标签集合；为 None 时不筛选

    Returns:
        issue 字典列表，每个 issue 包含 number/title/labels/state/
        projectItems/blockedBy 等字段
    """
    output = _run_gh([
        "issue", "list",
        "--assignee", assignee,
        "--state", "open",
        "--json", "number,title,assignees,labels,state,url,projectItems,blockedBy",
        "--limit", "100",
    ], repo)
    if not output:
        return []
    try:
        issues = json.loads(output)
    except json.JSONDecodeError:
        print("WARN: Failed to parse issues JSON", file=sys.stderr)
        return []

    # 按 label 筛选（valid_labels 为 None 时跳过）
    if valid_labels:
        issues = [
            issue for issue in issues
            if {l.get("name", "") for l in issue.get("labels", [])} & valid_labels
        ]

    return issues


def gh_classify_issues_inline(issues: List[Dict]) -> Dict[str, List[Dict]]:
    """将 issue 按看板状态动态分组，保留依赖关系数据。

    不写死状态名，直接用 GitHub 返回的 status.name 作为键。
    在每个 issue 信息中记录 type（Bug/Feature）和 blockedBy 依赖数据，
    供后续依赖过滤使用。

    Returns:
        {状态名: [{number, title, type, status, blockedBy}, ...]}
    """
    result: Dict[str, List[Dict]] = {}

    for issue in issues:
        num = issue.get("number")
        if not num:
            continue

        # 提取看板状态（从 projectItems 的 status 字段）
        status = ""
        project_items = issue.get("projectItems", [])
        if project_items and isinstance(project_items, list):
            status_obj = project_items[0].get("status", {})
            if isinstance(status_obj, dict):
                status = status_obj.get("name", "")

        # 判断 issue 类型：有 enhancement 标签为 Feature，有 bug 标签为 Bug
        labels = {l.get("name", "") for l in issue.get("labels", [])}
        issue_type = "Feature" if labels & _VALID_LABELS - {"bug"} else "Bug"

        # 保留 blockedBy 原始数据，供依赖检查使用
        blocked_by_raw = issue.get("blockedBy", {})
        blocked_by_nodes = blocked_by_raw.get("nodes", []) if isinstance(blocked_by_raw, dict) else []

        info = {
            "number": num,
            "title": issue.get("title", ""),
            "type": issue_type,
            "status": status,
            "blockedBy": blocked_by_nodes,
        }

        # 动态分桶（键为原始状态名，无状态则归入 "Unknown"）
        bucket = status if status else "Unknown"
        result.setdefault(bucket, []).append(info)

    return result


def _check_dependencies_satisfied(issue: Dict[str, Any]) -> tuple:
    """检查 issue 的依赖关系是否全部满足。

    GitHub 原生 "blocked by" 关系：issue A 被 B 阻塞，
    当且仅当 B 的 state 为 CLOSED 时，该依赖视为已满足。

    Args:
        issue: issue 信息字典，需包含 blockedBy 字段

    Returns:
        (satisfied: bool, open_blockers: list[int])
        - satisfied=True 表示所有依赖已满足，可派发
        - open_blockers 为仍处于 OPEN 状态的阻塞方 issue 编号列表
    """
    blocked_by_nodes = issue.get("blockedBy", [])
    if not blocked_by_nodes:
        # 无依赖关系，直接通过
        return True, []

    open_blockers: List[int] = []
    for blocker in blocked_by_nodes:
        blocker_state = blocker.get("state", "").upper()
        if blocker_state != "CLOSED":
            open_blockers.append(blocker.get("number"))

    return len(open_blockers) == 0, open_blockers


# ============================================================
# 第三部分：派发清单生成（本脚本核心）
# ============================================================

# 技能能力定义：仅处理 bug 和 enhancement 标签的 issue
_VALID_LABELS = {"bug", "enhancement"}

# Task 类型 → 工作流文件名（位于 references/ 目录下）
_TASK_WORKFLOW = {
    "Task1": "workflow-task1-worktree-init.md",
    "Task2": "workflow-task2-solution-pr.md",
    "Task3": "workflow-task3-coding-pr.md",
    "Task4": "workflow-task4-cleanup.md",
}

# Task 类型 → 中文描述（用于生成 description 字段）
_TASK_DESC = {
    "Task1": "Worktree初始化",
    "Task2": "方案PR生成",
    "Task3": "编码+测试",
    "Task4": "清理",
}

# 空结果常量（用于 early return）
_EMPTY_RESULT = {
    "dispatch_plan": {
        "items": [], "total_tasks": 0,
        "summary": {"task1_count": 0, "task2_count": 0, "task3_count": 0, "task4_count": 0},
        "skipped_by_dependency": [],
    }
}


def _build_task_item(
    issue: Dict[str, Any],
    skill_dir: str,
    task_type: str,
    cwd: str,
    env_path: str,
) -> Dict[str, Any]:
    """通用 Task 派发项构建器。

    为单个 issue 生成完整的 Task 工具调用参数，包括 query 字符串。
    query 中包含子代理执行所需的所有上下文：issue 编号、状态、
    工作流文件路径、技能目录、配置文件路径。
    """
    num = issue["number"]
    title = issue.get("title", "")
    issue_type = issue.get("type", "Bug")
    status_name = issue.get("status", "")

    # 拼接 query（所有 Task 统一结构）
    lines = [
        f"执行 {task_type} for Issue #{num}。",
        f"  Issue #{num} 处于 {status_name} 状态。",
        f"  ★ 子代理执行目录（cwd）: {cwd}",
        f"  读取工作流: {os.path.join(skill_dir, 'references', _TASK_WORKFLOW[task_type])}",
    ]
    lines.append(f"  技能目录: {skill_dir}")
    lines.append(f"  配置文件: {os.path.abspath(env_path)}")
    lines.append("  执行所有步骤。")

    return {
        "task_type": task_type,
        "issue_number": num,
        "issue_title": title,
        "issue_type": issue_type,
        "description": f"Issue #{num} {_TASK_DESC[task_type]}",
        "query": "\n".join(lines),
        "subagent_type": "general_purpose_task",
        "cwd": cwd,
    }


def generate_dispatch_plan(
    skill_dir: Optional[str] = None,
    env_path: Optional[str] = None,
) -> Dict[str, Any]:
    """一键生成派发清单：加载配置 → 查询分类 → 依赖过滤 → 预计算 Task 参数。

    Phase 1: 加载 .env 配置，获取当前用户和仓库信息
    Phase 2: 查询 issue 并按看板状态分类
    Phase 3: 遍历分类结果，对每个 issue 执行依赖检查——
             仅当所有 blocked-by 的 issue 均已 CLOSED 时才纳入派发清单
    Phase 4: 统计汇总（各 Task 数量 + 被跳过的 issue 列表）
    """
    # ===== Phase 1: 加载配置 + 获取用户 =====
    if env_path is None:
        env_path = os.path.join(os.getcwd(), ".worktree", ".env")
    config = load_config_from_env(env_path, _PARAM_DEFS)

    current_user = config.get("ASSIGNEE", "")
    if not current_user:
        print("ERROR: ASSIGNEE not set in .env or environment", file=sys.stderr)
        return {"current_user": "", "config": config,
                "classified": {}, **_EMPTY_RESULT}

    actual_repo = config.get("REPO", "")
    if not actual_repo:
        print("ERROR: REPO not set in .env or environment", file=sys.stderr)
        return {"current_user": current_user, "config": config,
                "classified": {}, **_EMPTY_RESULT}

    board_name = config.get("GITHUB_BOARD_NAME", actual_repo)
    if not board_name:
        print("ERROR: GITHUB_BOARD_NAME not set in .env or environment", file=sys.stderr)
        return {"current_user": current_user, "config": config,
                "classified": {}, **_EMPTY_RESULT}

    # ===== Phase 2: 查询 issue 并分类 =====
    issues = gh_query_issues_with_status(board_name, current_user, _VALID_LABELS)
    classified = gh_classify_issues_inline(issues)

    # 构建 status→task 反向映射（env 存 task→status，反转后用于按状态查 task）
    status_to_task = {v: "Task" + k[len("STATUS_TASK"):]
                      for k, v in config.items() if k.startswith("STATUS_TASK")}

    actual_skill_dir = skill_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # ===== Phase 3: 依赖过滤 + 生成派发项 =====
    dispatch_items: List[Dict[str, Any]] = []
    skipped_by_dependency: List[Dict[str, Any]] = []

    for status_name, issues_in_status in classified.items():
        task_type = status_to_task.get(status_name)
        if not task_type:
            # 无对应 Task 类型的状态（如 "In review"），跳过派发
            for issue in issues_in_status:
                print(f"Issue #{issue.get('number')} status='{status_name}' "
                      f"No subagent need to be dispatched.", file=sys.stderr)
            continue

        for issue in issues_in_status:
            num = issue.get("number")
            if not num:
                continue

            # 依赖检查：仅当所有 blocked-by issue 均已 CLOSED 时才派发
            satisfied, open_blockers = _check_dependencies_satisfied(issue)
            if not satisfied:
                skipped_by_dependency.append({
                    "number": num,
                    "title": issue.get("title", ""),
                    "task_type": task_type,
                    "open_blockers": open_blockers,
                })
                #print(f"Issue #{num} skipped: blocked by OPEN issue(s) "
                #      f"{open_blockers}", file=sys.stderr)
                continue

            # 通过依赖检查，生成 Task 派发项
            cwd = os.getcwd() if task_type == "Task1" else get_issue_worktree_path(config, num)
            dispatch_items.append(_build_task_item(issue, actual_skill_dir, task_type, cwd, env_path))

    # ===== Phase 4: 统计汇总 =====
    summary = {f"task{n}_count": sum(1 for d in dispatch_items if d["task_type"] == f"Task{n}")
               for n in range(1, 5)}

    return {
        "current_user": current_user,
        "config": config,
        "classified": classified,
        "dispatch_plan": {
            "items": dispatch_items,
            "total_tasks": len(dispatch_items),
            "summary": summary,
            "skipped_by_dependency": skipped_by_dependency,
        },
    }


# ============================================================
# 第四部分：CLI 接口
# ============================================================

def main():
    """CLI 入口：解析参数并调用 generate_dispatch_plan，输出 JSON。"""
    import argparse
    parser = argparse.ArgumentParser(description="Issue 派发清单生成器（依赖感知版）")
    parser.add_argument("--skill-dir", default=None, help="技能目录路径")
    parser.add_argument("--env-file", default=None, help=".env 配置文件路径")
    args = parser.parse_args()

    try:
        result = generate_dispatch_plan(skill_dir=args.skill_dir, env_path=args.env_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
