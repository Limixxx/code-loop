#!/usr/bin/env python3
"""task4_cleanup.py — Task4: Done → Worktree 清理回收

单阶段脚本：幂等检测 → 锁定 → PR 合并确认 → Git 清理 → 关闭 Issue → 解锁。
轻量任务，无 LLM 生成环节，无需 pre/post 拆分。

公共函数从 common 模块导入，本文件仅保留主流程逻辑。
配置由主代理通过 --env-file 参数传入（.env 文件路径）。

用法：
  python task4_cleanup.py --issue 683 --env-file /path/to/.env --cwd /path/to/worktree
"""

import os
import sys
import argparse
import shutil
import subprocess
from typing import Any, Dict

from common import (
    _PARAM_DEFS_BASE, load_config_from_env,
    _run_git,
    lock_exists, lock_create, lock_delete,
    flow_meta_read,
    pr_should_rerun,
)


_PARAM_DEFS = _PARAM_DEFS_BASE.copy()


# ============================================================
# 主流程
# ============================================================

def main_flow(issue_number: int, config: Dict[str, Any], cwd: str) -> int:
    """执行 Task4 清理流程。"""
    REPO = config["REPO"]
    GITHUB_BOARD_NAME = config.get("GITHUB_BOARD_NAME", REPO)
    BRANCH_PREFIX = config.get("BRANCH_PREFIX", "issue-")
    WORKTREE_PREFIX = config.get("WORKTREE_PREFIX", ".worktree")
    ISSUE_DOCS_PREFIX = config.get("ISSUE_DOCS_PREFIX", ".issue")

    branch_name = f"{BRANCH_PREFIX}{issue_number}"

    print("=" * 60)
    print(f"Task4: 清理回收 — Issue #{issue_number}")
    print("=" * 60)
    print(f"  分支: {branch_name}")
    print(f"  Worktree 目录: {cwd}")

    # ===== Step 1: 前置幂等检测 =====
    print("\n[Step1] 前置幂等检测...")

    # 1a. 检测 worktree 目录
    worktree_dir_exists = os.path.isdir(cwd)
    print(f"  Worktree 目录: {'EXISTS' if worktree_dir_exists else 'NOT FOUND'}")

    # 1b. 检测远端 feature 分支
    remote_result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch_name],
        capture_output=True, text=True, cwd=cwd,
    )
    remote_exists = bool(remote_result.stdout.strip())
    print(f"  远端分支 '{branch_name}': {'EXISTS' if remote_exists else 'NOT FOUND'}")

    if not remote_exists and not worktree_dir_exists:
        print(f"\nSKIP: Issue #{issue_number} already fully cleaned (no worktree, no remote branch)")
        return 0

    # ===== Step 2: 锁定阶段 =====
    print("\n[Step2] 锁定阶段...")

    if lock_exists(issue_number, ISSUE_DOCS_PREFIX, cwd=cwd):
        print("  SKIP: run.lock exists")
        return 0

    if lock_create(issue_number, "done-cleanup", ISSUE_DOCS_PREFIX, cwd=cwd):
        print("  [OK] run.lock created")
    else:
        print("  WARN: run.lock creation failed or already exists, continuing anyway")

    try:
        # ===== Step 3: 确认 PR 合并状态 =====
        print("\n[Step3] 确认 PR 合并状态...")
        meta = flow_meta_read(issue_number, ISSUE_DOCS_PREFIX, cwd=cwd)
        code_pr_num = meta.get("code_pr_number")
        pr_status = pr_should_rerun(REPO, code_pr_num)
        print(f"  代码 PR #{code_pr_num}: status={pr_status}")

        if pr_status not in ("DONE", "FIRST"):
            # PR 未合并（WAIT/ABORT） → 放弃清理
            print(f"  WAIT/ABORT: 代码 PR 未合并 (state={pr_status})，放弃清理")
            return 0

        if pr_status == "DONE":
            print("  [OK] 代码 PR 已合并，继续清理")
        else:
            print("  [OK] 无代码 PR 记录，视为无需确认合并，继续清理")

        # ===== Step 4: Git 清理逻辑 =====

        # 4a. 删除本地 worktree
        print("\n[Step4a] 删除本地 worktree...")
        if worktree_dir_exists:
            # git worktree prune 清理过期记录
            _run_git(["worktree", "prune"], cwd=cwd)

            # git worktree remove --force
            remove_ok = _run_git(
                ["worktree", "remove", "--force", os.path.join(WORKTREE_PREFIX, branch_name)],
                cwd=cwd,
            )
            if remove_ok[0]:
                print("  [OK] git worktree remove 成功")
            else:
                print(f"  [WARN] git worktree remove 失败，尝试文件系统强制删除:", remove_ok[1])

            # 二次验证：文件系统级强制删除
            if os.path.isdir(cwd):
                print(f"  WARN: worktree 目录仍存在: {cwd}")
                try:
                    shutil.rmtree(cwd, ignore_errors=True)
                    print(f"  [OK] 文件系统强制删除完成: {cwd}")
                except Exception as e:
                    print(f"  ERROR: 无法强制删除目录: {e}", file=sys.stderr)

            # 最终验证
            if os.path.isdir(cwd):
                print(f"  WARN: worktree 目录仍存在: {cwd}", file=sys.stderr)
            else:
                print("  [OK] worktree 目录已删除")
        else:
            print("  Worktree 目录不存在，跳过本地清理")

        # 4b. 删除远端 feature 分支
        print("\n[Step4b] 删除远端 feature 分支...")
        if remote_exists:
            result = subprocess.run(
                ["git", "push", "origin", "--delete", branch_name],
                capture_output=True, text=True, cwd=cwd, timeout=60,
            )
            if result.returncode == 0:
                print(f"  [OK] 远端分支 '{branch_name}' 已删除")
            else:
                err = result.stderr.strip()
                if "remote ref does not exist" in err.lower() or "couldn't find remote ref" in err.lower():
                    print(f"  [OK] 远端分支 '{branch_name}' 已不存在（忽略）")
                else:
                    print(f"  WARN: 删除远端分支失败: {err}")
        else:
            print("  远端分支不存在，跳过删除")

        # ===== Step 5: 删除本地 docs 隔离文件夹 =====
        print("\n[Step5] 删除本地 docs 隔离文件夹...")
        for base_dir in [cwd]:
            issue_docs_dir = os.path.join(base_dir, ISSUE_DOCS_PREFIX, str(issue_number))
            if os.path.isdir(issue_docs_dir):
                shutil.rmtree(issue_docs_dir, ignore_errors=True)
                if os.path.isdir(issue_docs_dir):
                    print(f"  WARN: docs 目录仍存在: {issue_docs_dir}")
                else:
                    print(f"  [OK] docs 目录已删除: {issue_docs_dir}")
            else:
                print(f"  docs 目录不存在: {issue_docs_dir}")

        # ===== Step 6: 关闭 Issue =====
        print("\n[Step6] 关闭 Issue...")
        result = subprocess.run(
            ["gh", "issue", "close", str(issue_number), "--repo", GITHUB_BOARD_NAME],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            print(f"  [OK] Issue #{issue_number} closed")
        else:
            print(f"  WARN: 关闭 Issue #{issue_number} 失败: {result.stderr.strip()}")

        # ===== Step 7: 输出结果 =====
        print(f"\n===== 执行成功 =====")
        print(f"Issue #{issue_number} -> worktree removed, branch {branch_name} deleted, docs cleaned, issue closed")
        print("状态: 清理完成，流程归档")

        return 0

    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1

    finally:
        # ===== 安全清理 run.lock =====
        print("\n[Finally] 删除 run.lock...")
        # 尝试从多个可能的位置删除 lock
        lock_deleted = False
        for lock_cwd_candidate in [cwd]:
            if lock_cwd_candidate and os.path.isdir(lock_cwd_candidate):
                if lock_exists(issue_number, ISSUE_DOCS_PREFIX, cwd=lock_cwd_candidate):
                    lock_delete(issue_number, ISSUE_DOCS_PREFIX, cwd=lock_cwd_candidate)
                    lock_deleted = True
                    print(f"  [OK] run.lock deleted (cwd={lock_cwd_candidate})")
        if not lock_deleted:
            print("  [OK] run.lock not found (may have been removed with worktree)")


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Task4: Done → Worktree 清理回收")
    parser.add_argument("--issue", type=int, required=True, help="Issue 编号")
    parser.add_argument("--env-file", required=True, help=".env 配置文件路径")
    parser.add_argument("--cwd", default=None, help="worktree 目录")
    args = parser.parse_args()

    config = load_config_from_env(args.env_file, _PARAM_DEFS)
    cwd = args.cwd

    sys.exit(main_flow(args.issue, config, cwd))


if __name__ == "__main__":
    main()
