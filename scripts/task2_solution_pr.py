#!/usr/bin/env python3
"""task2_solution_pr.py — Task2: 方案生成 & 方案PR提交

双阶段脚本：
  --phase pre  : 执行 Step0~3（前置检查、防重、读驳回意见），输出 JSON 供 LLM 使用
  --phase post : 执行 Step5~8（git commit/push、创建 PR、更新 flow_meta、清理 lock）

公共函数从 common 模块导入。
配置由主代理通过 --env-file 参数传入（.env 文件路径）。

用法：
  python task2_solution_pr.py --issue 683 --env-file /path/to/.env --phase pre  --cwd /path/to/repo
  python task2_solution_pr.py --issue 683 --env-file /path/to/.env --phase post --cwd /path/to/repo
"""

import os
import sys
import json
import re
import argparse
from typing import Any, Dict

from common import (
    _PARAM_DEFS_BASE, load_config_from_env,
    _run_git,
    lock_exists, lock_create, lock_delete,
    flow_meta_read, flow_meta_update,
    gh_update_project_status, gh_get_pr_comments,
    gh_create_pr, pr_should_rerun, gh_get_issue_info,
)


_PARAM_DEFS = _PARAM_DEFS_BASE.copy()


# ============================================================
# Phase Pre 主逻辑
# ============================================================

def phase_pre(issue_number: int, config: Dict[str, Any], cwd: str) -> int:
    """执行前置检查（Step0~3），输出 JSON 供 LLM 决策。"""
    REPO = config["REPO"]
    GITHUB_BOARD_NAME = config.get("GITHUB_BOARD_NAME", REPO)
    BRANCH_PREFIX = config.get("BRANCH_PREFIX", "issue-")
    ISSUE_DOCS_PREFIX = config.get("ISSUE_DOCS_PREFIX", ".issue")
    STATUS_TASK2 = config.get("STATUS_TASK2", "Ready")
    STATUS_TASK3 = config.get("STATUS_TASK3", "In progress")

    branch_name = f"{BRANCH_PREFIX}{issue_number}"

    print("=" * 60)
    print(f"Task2 (pre): 方案生成前置检查 — Issue #{issue_number}")
    print("=" * 60)

    # ===== Step 0a: 检测 run.lock =====
    print("\n[Step0a] 检测 run.lock...")
    if lock_exists(issue_number, ISSUE_DOCS_PREFIX, cwd=cwd):
        print("  SKIP: run.lock exists")
        result = {"action": "SKIP", "reason": "run.lock exists"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0

    # ===== Step 0b: 检查方案 PR 状态 =====
    print("\n[Step0b] 检查方案 PR 状态...")
    meta = flow_meta_read(issue_number, ISSUE_DOCS_PREFIX, cwd=cwd)
    solution_pr_number = meta.get("solution_pr_number")
    pr_status = pr_should_rerun(REPO, solution_pr_number)
    print(f"  方案 PR #{solution_pr_number}: status={pr_status}")

    if pr_status == "DONE":
        # 方案已合并 → 自动推进看板状态 Ready → In Process
        print("  方案 PR 已合并，更新看板状态 Ready → In Process...")
        if config.get("GITHUB_BOARD_NUMBER"):
            status_updated = gh_update_project_status(
                issue_number=issue_number, task_type="Task3",
                config=config, issue_docs_prefix=ISSUE_DOCS_PREFIX, cwd=cwd,
            )
            if status_updated:
                print(f"  [OK] 看板状态已更新: {STATUS_TASK2} → {STATUS_TASK3}")
                # 同步更新 flow_meta.json
                flow_meta_update(issue_number, ISSUE_DOCS_PREFIX,
                                 {"status": STATUS_TASK3}, cwd=cwd)
            else:
                print("  [WARN] 看板状态更新失败（非致命）")
        result = {"action": "SKIP", "reason": "solution PR already merged"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0

    if pr_status == "WAIT":
        result = {"action": "SKIP", "reason": "solution PR under review"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0

    if pr_status == "ABORT":
        result = {"action": "ABORT", "reason": "solution PR closed without merge"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 1

    # RERUN 或 FIRST → 继续后续步骤

    # ===== Step 2: 创建 run.lock =====
    print("\n[Step2] 创建 run.lock...")
    lock_ok = lock_create(issue_number, "solution-pr", ISSUE_DOCS_PREFIX, cwd=cwd)
    if not lock_ok:
        print("  FAIL: 无法创建 run.lock")
        result = {"action": "SKIP", "reason": "run.lock already exists"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0
    print("  [OK] run.lock 已创建")

    # ===== Step 3: 读取驳回意见 =====
    print("\n[Step3] 读取驳回意见...")
    rejection_comments = ""
    if solution_pr_number:
        rejection_comments = gh_get_pr_comments(REPO, solution_pr_number)
        if rejection_comments:
            print(f"  [OK] 获取到评审评论 ({len(rejection_comments)} chars)")
        else:
            print("  [OK] 无评审评论")
    else:
        print("  [OK] 首次执行，无历史 PR")

    # ===== 获取 Issue 信息 =====
    print("\n[补充] 获取 Issue 详细信息...")
    issue_info = gh_get_issue_info(GITHUB_BOARD_NAME, issue_number)
    issue_title = issue_info.get("title", meta.get("issue_title", ""))
    issue_body = issue_info.get("body", "")
    issue_labels = [l.get("name", "") for l in issue_info.get("labels", [])]
    issue_type = "Feature" if set(issue_labels) & {"enhancement"} else "Bug"

    # ===== 输出 JSON =====
    result = {
        "action": "GENERATE",
        "pr_status": pr_status,
        "issue_number": issue_number,
        "issue_title": issue_title,
        "issue_type": issue_type,
        "issue_body": issue_body,
        "issue_labels": issue_labels,
        "rejection_comments": rejection_comments,
        "branch_name": branch_name,
        "worktree_path": cwd,
        "solution_path": os.path.join(cwd, ISSUE_DOCS_PREFIX, str(issue_number), "solution.md"),
    }
    print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
    return 0


# ============================================================
# Phase Post 主逻辑
# ============================================================

def phase_post(issue_number: int, config: Dict[str, Any], cwd: str) -> int:
    """执行后置操作（Step5~8）：git commit/push、创建 PR、更新 flow_meta、清理 lock。"""
    REPO = config["REPO"]
    BASE_BRANCH = config["BASE_BRANCH"]
    ISSUE_DOCS_PREFIX = config.get("ISSUE_DOCS_PREFIX", ".issue")
    BRANCH_PREFIX = config.get("BRANCH_PREFIX", "issue-")

    branch_name = f"{BRANCH_PREFIX}{issue_number}"

    print("=" * 60)
    print(f"Task2 (post): 提交方案 PR — Issue #{issue_number}")
    print("=" * 60)

    try:
        # ===== Step 5: 提交方案文件并创建 Document PR =====
        print("\n[Step5] 提交方案文件并创建 Document PR...")
        solution_path = os.path.join(cwd, ISSUE_DOCS_PREFIX, str(issue_number), "solution.md")

        if not os.path.isfile(solution_path):
            print(f"  FAIL: solution.md not found: {solution_path}", file=sys.stderr)
            return 1

        use_empty_commit = False
        result = _run_git(["add", solution_path], cwd=cwd)
        if not result[0]:
            # 方案文件路径被 .gitignore 忽略（如 docs/issue）时，不使用 -f 强制添加，
            # 而是回退为 --allow-empty 空提交：0 文件修改但分支仍有 commit，PR 可正常创建。
            print("  [WARN] git add failed (路径可能被 .gitignore 忽略):", result[1])
            print("  [INFO] 回退为 --allow-empty 空提交创建 PR（不使用 -f 强制添加）")
            use_empty_commit = True

        # 方案内容与上一轮完全一致时无 diff，普通 commit 会失败，且 0 diff 的
        # 分支无法创建 PR（GitHub 拦截 No changes）。此时用空 commit 造出
        # 有 commit 的分支，PR 可正常创建并走评审。
        if use_empty_commit:
            commit_cmd = ["commit", "--allow-empty", "-m", f"docs({branch_name}): add solution document"]
        else:
            status_ok, status_out = _run_git(["status", "--porcelain"], cwd=cwd)
            commit_cmd = ["commit", "-m", f"docs({branch_name}): add solution document"]
            if status_ok and not status_out.strip():
                print("  [INFO] 无文件变更，使用 --allow-empty 空提交创建 PR")
                commit_cmd.insert(1, "--allow-empty")
        result = _run_git(commit_cmd, cwd=cwd)
        if not result[0]: 
            print("  FAIL: git commit failed: ", result[1])
            return 1
        result = _run_git(["push", "origin", branch_name], cwd=cwd)
        if not result[0]:
            print("  FAIL: git push failed:", result[1])
            return 1
        print("  [OK] git push 完成")

        # 创建 PR
        pr_title = f"docs({branch_name}): solution for Issue #{issue_number}"
        # 读取 solution.md 内容
        with open(solution_path, 'r', encoding='utf-8') as f:
            solution_content = f.read()
        # pr_body = solution内容 + 尾部引用信息
        pr_body = f"{solution_content}\n\nRelated #{issue_number}"
        pr_output = gh_create_pr(REPO, branch_name, pr_title, pr_body, BASE_BRANCH)

        pr_url = ""
        pr_num = None
        url_match = re.search(r'https://github\.com/\S+', pr_output)
        num_match = re.search(r'pull/(\d+)', pr_output)
        if url_match:
            pr_url = url_match.group(0)
        if num_match:
            pr_num = int(num_match.group(1))

        if not pr_url or not pr_num:
            print(f"  FAIL: Cannot parse PR from output: {pr_output}", file=sys.stderr)
            return 1
        print(f"  [OK] PR 已创建: #{pr_num} ({pr_url})")

        # ===== Step 6: 将 PR 链接写入 flow_meta.json =====
        print("\n[Step6] 更新 flow_meta.json...")
        meta_ok = flow_meta_update(issue_number, ISSUE_DOCS_PREFIX, {
            "solution_pr_url": pr_url,
            "solution_pr_number": pr_num,
        }, cwd=cwd)
        if meta_ok:
            print("  [OK] flow_meta.json 已更新")
        else:
            print("  [WARN] flow_meta.json 更新失败（非致命）")

        # ===== Step 7: 删除 run.lock =====
        print("\n[Step7] 删除 run.lock...")
        lock_delete(issue_number, ISSUE_DOCS_PREFIX, cwd=cwd)
        print("  [OK] run.lock 已删除")

        # ===== Step 8: 输出结果 =====
        print(f"\n===== 执行成功 =====")
        print(f"Issue #{issue_number} → solution PR: #{pr_num} ({pr_url})")
        print("状态: 方案已提交，等待人工评审")

        return 0

    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1

    finally:
        # 安全清理 run.lock
        lock_delete(issue_number, ISSUE_DOCS_PREFIX, cwd=cwd)


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Task2: 方案生成 & 方案PR提交")
    parser.add_argument("--issue", type=int, required=True, help="Issue 编号")
    parser.add_argument("--env-file", required=True, help=".env 配置文件路径")
    parser.add_argument("--phase", required=True, choices=["pre", "post"], help="执行阶段: pre(前置检查) / post(提交PR)")
    parser.add_argument("--cwd", default=None, help="worktree 目录")
    args = parser.parse_args()

    config = load_config_from_env(args.env_file, _PARAM_DEFS)
    cwd = args.cwd
    
    if args.phase == "pre":
        sys.exit(phase_pre(args.issue, config, cwd))
    else:
        sys.exit(phase_post(args.issue, config, cwd))


if __name__ == "__main__":
    main()
