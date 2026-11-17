#!/usr/bin/env python3
"""task3_code_pr.py — Task3: 编码 + 测试 + 代码PR提交

双阶段脚本：
  --phase pre  : 执行 Step0~3（前置检查、防重、读驳回意见），输出 JSON 供 LLM 使用
  --phase post : 执行 Step1~8（确认变更、git commit/push、创建 PR、更新 flow_meta、
                 推送 flow_meta 更新、清理 lock）

公共函数从 common 模块导入，本文件仅包含 Task3 独有的 phase_pre / phase_post 逻辑。
配置由主代理通过 --env-file 参数传入（.env 文件路径）。

用法：
  python task3_code_pr.py --issue 683 --env-file /path/to/.env --phase pre  --cwd /path/to/worktree
  python task3_code_pr.py --issue 683 --env-file /path/to/.env --phase post --cwd /path/to/worktree
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
    STATUS_TASK3 = config.get("STATUS_TASK3", "In progress")
    STATUS_TASK4 = config.get("STATUS_TASK4", "Done")

    branch_name = f"{BRANCH_PREFIX}{issue_number}"

    print("=" * 60)
    print(f"Task3 (pre): 编码前置检查 — Issue #{issue_number}")
    print("=" * 60)

    # ===== Step 0a: 检测 run.lock =====
    print("\n[Step0a] 检测 run.lock...")
    if lock_exists(issue_number, ISSUE_DOCS_PREFIX, cwd=cwd):
        print("  SKIP: run.lock exists")
        result = {"action": "SKIP", "reason": "run.lock exists"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0

    # ===== Step 0b: 检查代码 PR 状态 =====
    print("\n[Step0b] 检查代码 PR 状态...")
    meta = flow_meta_read(issue_number, ISSUE_DOCS_PREFIX, cwd=cwd)
    code_pr_number = meta.get("code_pr_number")
    pr_status = pr_should_rerun(REPO, code_pr_number)
    print(f"  代码 PR #{code_pr_number}: status={pr_status}")

    if pr_status == "DONE":
        # 代码 PR 已合并 → 自动推进看板状态 In progress → Done
        print("  代码 PR 已合并，更新看板状态 In progress → Done...")
        if config.get("GITHUB_BOARD_NUMBER"):
            status_updated = gh_update_project_status(
                issue_number=issue_number, task_type="Task4",
                config=config, issue_docs_prefix=ISSUE_DOCS_PREFIX, cwd=cwd,
            )
            if status_updated:
                print(f"  [OK] 看板状态已更新: {STATUS_TASK3} → {STATUS_TASK4}")
                # 同步更新 flow_meta.json
                flow_meta_update(issue_number, ISSUE_DOCS_PREFIX,
                                 {"status": STATUS_TASK4}, cwd=cwd)
            else:
                print("  [WARN] 看板状态更新失败（非致命）")
        result = {"action": "SKIP", "reason": "code PR already merged"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0

    if pr_status == "WAIT":
        result = {"action": "SKIP", "reason": "code PR under review"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0

    if pr_status == "ABORT":
        result = {"action": "ABORT", "reason": "code PR closed without merge"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 1

    # RERUN 或 FIRST → 继续后续步骤

    # ===== Step 2: 创建 run.lock =====
    print("\n[Step2] 创建 run.lock...")
    lock_ok = lock_create(issue_number, "code-pr", ISSUE_DOCS_PREFIX, cwd=cwd)
    if not lock_ok:
        print("  FAIL: 无法创建 run.lock")
        result = {"action": "SKIP", "reason": "run.lock already exists"}
        print(f"\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0
    print("  [OK] run.lock 已创建")

    # ===== Step 3: 读取驳回意见 =====
    print("\n[Step3] 读取驳回意见...")
    rejection_comments = ""
    if code_pr_number:
        rejection_comments = gh_get_pr_comments(REPO, code_pr_number)
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
    """执行后置操作（Step1~8）：确认变更、git commit/push、创建 PR、
    更新 flow_meta、推送 flow_meta 更新、清理 lock。"""
    REPO = config["REPO"]
    BASE_BRANCH = config["BASE_BRANCH"]
    ISSUE_DOCS_PREFIX = config.get("ISSUE_DOCS_PREFIX", ".issue")
    BRANCH_PREFIX = config.get("BRANCH_PREFIX", "issue-")

    branch_name = f"{BRANCH_PREFIX}{issue_number}"

    print("=" * 60)
    print(f"Task3 (post): 提交代码 PR — Issue #{issue_number}")
    print("=" * 60)

    try:
        # ===== Step 3: git add / commit / push =====
        print("\n[Step3] 提交代码变更...")
        solution_path = os.path.join(cwd, ISSUE_DOCS_PREFIX, str(issue_number), "solution.md")

        # git add 所有代码+测试变更（排除 run.lock）
        ops, err = _run_git(["add", "-A"], cwd=cwd)
        if not ops:
            print("  FAIL: git add failed", err)
            return 1
        ops, err = _run_git(
            ["reset", "HEAD", os.path.join(ISSUE_DOCS_PREFIX, str(issue_number), "run.lock")],
            cwd=cwd,
        )
        if not ops:
            print("  FAIL: git reset failed", err)
            return 1
        ops, err = _run_git(["commit", "-m", f"feat({branch_name}): implement Issue #{issue_number}"], cwd=cwd)
        if not ops:
            print("  [WARN] git commit failed or nothing to commit", err)
            return 1
        ops, err = _run_git(["push", "origin", branch_name], cwd=cwd)
        if not ops:
            print("  FAIL: git push failed", err)
            return 1
        print("  [OK] git push 完成")

        # ===== Step 4: 创建代码 PR =====
        pr_title = f"feat({branch_name}): implement Issue #{issue_number}"
        # 读取 solution.md 内容
        with open(solution_path, 'r', encoding='utf-8') as f:
            solution_content = f.read()
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

        # ===== Step 5: 将 PR 链接写入 flow_meta.json =====
        print("\n[Step5] 更新 flow_meta.json...")
        meta_ok = flow_meta_update(issue_number, ISSUE_DOCS_PREFIX, {
            "code_pr_url": pr_url,
            "code_pr_number": pr_num,
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
        print(f"Issue #{issue_number} → code PR: #{pr_num} ({pr_url})")
        print("状态: 代码已提交，等待人工评审")

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
    parser = argparse.ArgumentParser(description="Task3: 编码 + 测试 + 代码PR提交")
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
