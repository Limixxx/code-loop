#!/usr/bin/env python3
"""task1_worktree_init.py — Task1: WorkTree 初始化（自包含版）

按 workflow-task1-worktree-init.md 执行所有步骤。
支持重复执行时的防重检测。

本脚本自包含，不引用原技能的任何脚本或模块，所有逻辑在本文件内实现。
配置由主代理通过 --env-file 参数传入（.env 文件路径）。

用法：
  python task1_worktree_init.py --issue 683 --env-file /path/to/.env --cwd /path/to/repo
"""

import os
import sys
import json
import argparse
import shutil
from datetime import datetime, timezone
from typing import Optional

from common import (
    _PARAM_DEFS_BASE, load_config_from_env,
    _run_gh, _run_git,
    lock_exists, lock_create, lock_delete,
    _meta_path, flow_meta_read,
    gh_get_issue_info, gh_update_project_status,
)

_PARAM_DEFS = _PARAM_DEFS_BASE.copy()


# ============================================================
# WorkTree 操作
# ============================================================

def _worktree_expected_dir(branch_name: str, worktree_prefix: str,
                           cwd: Optional[str] = None) -> str:
    """构建 worktree 预期目录的绝对路径。"""
    rel_dir = os.path.join(worktree_prefix, branch_name)
    if cwd:
        return os.path.join(cwd, rel_dir)
    return rel_dir


def worktree_find(branch_name: str, cwd: Optional[str] = None) -> str:
    """按分支名查找 worktree 路径。"""
    output = _run_git(["worktree", "list", "--porcelain"], cwd=cwd)
    if not output[0]:
        return ""
    lines = output[1].splitlines()
    for i, line in enumerate(lines):
        if line.startswith("branch ") and line.endswith(f"/{branch_name}"):
            if i > 0 and lines[i - 1].startswith("worktree "):
                return lines[i - 1][len("worktree "):]
    return ""


def worktree_dir_exists(branch_name: str, worktree_prefix: str,
                        cwd: Optional[str] = None) -> bool:
    """检查 worktree 预期目录是否存在于文件系统。"""
    return os.path.isdir(_worktree_expected_dir(branch_name, worktree_prefix, cwd))


def worktree_exists(branch_name: str, worktree_prefix: str,
                    cwd: Optional[str] = None) -> bool:
    """检查 worktree 是否存在（双重检测：git 识别 或 文件系统目录）。"""
    wt_path = worktree_find(branch_name, cwd=cwd)
    if wt_path and os.path.isdir(wt_path):
        return True
    return worktree_dir_exists(branch_name, worktree_prefix, cwd)


def worktree_is_valid(branch_name: str, worktree_prefix: str,
                      cwd: Optional[str] = None) -> bool:
    """验证 worktree 是否有效。"""
    # 检测 1: git worktree list 识别 + 远端分支存在
    wt_path = worktree_find(branch_name, cwd=cwd)
    if wt_path and os.path.isdir(wt_path):
        output = _run_git(["ls-remote", "--heads", "origin", branch_name], cwd=cwd)
        return output[0]
    # 检测 2: 文件系统目录存在且含 .git 标识
    expected_dir = _worktree_expected_dir(branch_name, worktree_prefix, cwd)
    if os.path.isdir(expected_dir):
        git_marker = os.path.join(expected_dir, ".git")
        if os.path.exists(git_marker):
            return True
    return False


def worktree_create(branch_name: str, base_branch: str,
                    worktree_prefix: str, cwd: Optional[str] = None) -> str:
    """创建新 worktree（新分支基于 base）。"""
    wt_dir = os.path.join(worktree_prefix, branch_name)
    output = _run_git(["worktree", "prune"], cwd=cwd)
    if not output[0]:
        return ""
    output = _run_git(["fetch", "origin"], cwd=cwd)
    if not output[0]:
        return ""
    output = _run_git(["fetch", "origin"], cwd=cwd)
    if not output[0]:
        return ""
    output = _run_git(["reset", "--hard", f"origin/{base_branch}"], cwd=cwd)
    if not output[0]:
        return ""
    output = _run_git(["clean", "-fd"], cwd=cwd)
    if not output[0]:
        return ""
    output = _run_git(["worktree", "add", wt_dir, "-b", branch_name, base_branch], cwd=cwd)
    if not output[0]:
        return ""
        
    return wt_dir


def worktree_remove_force(branch_name: str, worktree_prefix: str,
                          cwd: Optional[str] = None) -> bool:
    """强制删除 worktree。"""
    wt_dir = os.path.join(worktree_prefix, branch_name)
    abs_wt_dir = os.path.join(cwd, wt_dir) if cwd else wt_dir
    _run_git_ok(["worktree", "prune"], cwd=cwd)
    _run_git_ok(["worktree", "remove", "--force", wt_dir], cwd=cwd)
    if os.path.isdir(abs_wt_dir):
        try:
            shutil.rmtree(abs_wt_dir, ignore_errors=True)
        except Exception:
            pass
    if os.path.isdir(abs_wt_dir):
        print(f"WARN: worktree directory still exists: {abs_wt_dir}", file=sys.stderr)
        return False
    return True


# ============================================================
# Flow Meta 初始化
# ============================================================

def flow_meta_init(issue_number: int, issue_title: str, labels: list,
                   worktree_path: str, branch_prefix: str, base_branch: str,
                   issue_docs_prefix: str, status_task1: str, item_id: str,
                   cwd: Optional[str] = None) -> bool:
    """初始化 flow_meta.json。"""
    filepath = _meta_path(issue_number, issue_docs_prefix)
    if cwd:
        filepath = os.path.join(cwd, filepath)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "issue_number": issue_number,
        "issue_title": issue_title,
        "labels": labels,
        "branch": f"{branch_prefix}{issue_number}",
        "base_branch": base_branch,
        "worktree_path": worktree_path,
        "solution_pr_url": None,
        "solution_pr_number": None,
        "code_pr_url": None,
        "code_pr_number": None,
        "project_item_id": item_id,
        "created_at": timestamp,
        "status": status_task1,
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        return True
    except OSError as e:
        print(f"ERROR: Cannot write flow_meta.json: {e}", file=sys.stderr)
        return False


# ============================================================
# 项目板操作
# ============================================================

def gh_project_item_id(
    repo: str, issue_number: int
) -> Optional[str]:
    """获取项目板中 issue 的 project_item_id。"""
    owner, name = repo.split("/", 1) if "/" in repo else (repo, repo)

    # GraphQL 查询语句
    gql_query = """
    query($owner: String!, $repo: String!, $issueNum: Int!) {
        repository(owner:$owner, name:$repo) {
            issue(number:$issueNum) {
                projectItems(first: 20) {
                    nodes {
                        id
                        project {
                            number
                            title
                        }
                    }
                }
            }
        }
    }
    """
    output = _run_gh([
        "api", "graphql",
        "-F", f"owner={owner}",
        "-F", f"repo={name}",
        "-F", f"issueNum={issue_number}",
        "-f", f"query={gql_query}"
    ])
    if not output:
        print(f"ERROR: Issue #{issue_number} not found in project board", file=sys.stderr)
        return None

    try:
        data = json.loads(output)
        nodes = data.get("data", {}).get("repository", {}).get("issue", {}).get("projectItems", {}).get("nodes", [])
        if nodes:
            return nodes[0].get("id")
    except json.JSONDecodeError:
        pass
    print(f"ERROR: Cannot parse project item ID for Issue #{issue_number}", file=sys.stderr)
    return output


# ============================================================
# 主流程
# ============================================================


def main():
    parser = argparse.ArgumentParser(description="Task1: 初始化（自包含版）")
    parser.add_argument("--issue", type=int, required=True, help="Issue 编号")
    parser.add_argument("--env-file", required=True, help=".env 配置文件路径")
    parser.add_argument("--cwd", default=None, help="项目仓库根目录")
    args = parser.parse_args()

    # ===== Step 1: 解析配置 =====
    config = load_config_from_env(args.env_file, _PARAM_DEFS)

    REPO = config["REPO"]
    GITHUB_BOARD_NAME = config.get("GITHUB_BOARD_NAME", REPO)
    BASE_BRANCH = config["BASE_BRANCH"]
    BRANCH_PREFIX = config.get("BRANCH_PREFIX", "issue-")
    WORKTREE_PREFIX = config.get("WORKTREE_PREFIX", ".worktree")
    ISSUE_DOCS_PREFIX = config.get("ISSUE_DOCS_PREFIX", ".issue")
    STATUS_TASK1 = config.get("STATUS_TASK1", "Backlog")
    STATUS_TASK2 = config.get("STATUS_TASK2", "Ready")

    issue_number = args.issue
    CWD = args.cwd
    branch_name = f"{BRANCH_PREFIX}{issue_number}"

    print("=" * 60)
    print(f"Task1: 初始化 — Issue #{issue_number}")
    print("=" * 60)
    print(f"  配置: repo={REPO}, base_branch={BASE_BRANCH}")
    print(f"  分支: {branch_name}")
    print(f"  工作目录: {CWD}")

    # ===== Step 2: 获取 Issue 信息 =====
    print("\n[Step2] 获取 Issue 信息...")
    issue_info = gh_get_issue_info(GITHUB_BOARD_NAME, issue_number)
    if not issue_info:
        print(f"  FAIL: Cannot fetch issue #{issue_number} info", file=sys.stderr)
        sys.exit(1)

    issue_title = issue_info.get("title", "")
    labels = [l.get("name", "") for l in issue_info.get("labels", [])]
    print(f"  [OK] 标题: {issue_title}")
    print(f"  [OK] 标签: {labels}")

    # ===== Step 3: 防重检查 =====
    print("\n[Step3] 防重检查...")
    expected_worktree_dir = _worktree_expected_dir(branch_name, WORKTREE_PREFIX, CWD)

    # 检查 1: worktree 已存在且有效
    if worktree_exists(branch_name, WORKTREE_PREFIX, cwd=CWD):
        if worktree_is_valid(branch_name, WORKTREE_PREFIX, cwd=CWD):
            print(f"  SKIP: worktree already initialized for Issue #{issue_number}")
            sys.exit(0)
        else:
            print(f"  [WARN] worktree 存在但无效，清理残留...")
            worktree_remove_force(branch_name, WORKTREE_PREFIX, cwd=CWD)
    else:
        print("  [OK] worktree 不存在")

    # 检查 2: flow_meta.json 已存在
    if os.path.isdir(expected_worktree_dir):
        meta_path = os.path.join(expected_worktree_dir, ISSUE_DOCS_PREFIX, str(issue_number), "flow_meta.json")
        if os.path.isfile(meta_path):
            print(f"  SKIP: flow_meta.json already exists for Issue #{issue_number}")
            sys.exit(0)
        print("  [OK] flow_meta.json 不存在（worktree 目录已存在但无 meta）")

        # 检查 3: run.lock 已存在
        if lock_exists(issue_number, ISSUE_DOCS_PREFIX, cwd=expected_worktree_dir):
            print(f"  SKIP: run.lock exists in worktree for Issue #{issue_number}")
            sys.exit(0)
        print("  [OK] run.lock 不存在（worktree 目录内）")
    else:
        print("  [OK] flow_meta.json / run.lock 检查跳过（worktree 目录不存在）")

    print("  [OK] 防重检查通过")

    try:
        # ===== Step 4: 创建 run.lock =====
        print("\n[Step4] 创建 run.lock...")
        lock_ok = lock_create(issue_number, "worktree-init", ISSUE_DOCS_PREFIX, cwd=CWD)
        if not lock_ok:
            print("  FAIL: 无法创建 run.lock")
            sys.exit(1)
        print("  [OK] run.lock 已创建")

        # ===== Step 5: git fetch + 创建 worktree =====
        print("\n[Step5] 初始化 WorkTree...")
        print(f"  创建 worktree: {branch_name} 基于 {BASE_BRANCH}...")
        worktree_path = worktree_create(branch_name, BASE_BRANCH, WORKTREE_PREFIX, cwd=CWD)
        if not worktree_path:
            print(f"  FAIL: Cannot create worktree for Issue #{issue_number}")
            sys.exit(1)

        abs_worktree_path = os.path.join(CWD, worktree_path) if not os.path.isabs(worktree_path) else worktree_path
        print(f"  [OK] worktree 已创建: {abs_worktree_path}")

        # ===== Step 7: 写入 flow_meta.json =====
        print("\n[Step7] 写入 flow_meta.json...")
        item_id = gh_project_item_id(GITHUB_BOARD_NAME, issue_number)
        meta_ok = flow_meta_init(
            issue_number, issue_title, labels, worktree_path,
            BRANCH_PREFIX, BASE_BRANCH, ISSUE_DOCS_PREFIX,
            STATUS_TASK1, item_id, cwd=abs_worktree_path,
        )
        if not meta_ok:
            print("  FAIL: 无法写入 flow_meta.json")
            sys.exit(1)

        meta = flow_meta_read(issue_number, ISSUE_DOCS_PREFIX, cwd=abs_worktree_path)
        print("  [OK] flow_meta.json 已写入:")
        print(json.dumps(meta, indent=2, ensure_ascii=False))

        # ===== Step 8: 确保 .gitignore 包含 run.lock 排除规则 =====
        # print("\n[Step8] 确保 .gitignore 包含 run.lock 排除规则...")
        # gitignore_ok = lock_ensure_gitignore(cwd=abs_worktree_path)
        # if gitignore_ok:
        #     print("  [OK] .gitignore 已更新（在 worktree 内）")
        # else:
        #     print("  [WARN] .gitignore 更新失败（非致命）")

        # gitignore_ok_main = lock_ensure_gitignore(cwd=CWD)
        # if gitignore_ok_main:
        #     print("  [OK] .gitignore 已更新（在主仓库内）")

        # ===== Step 9: 更新看板状态 =====
        print("\n[Step9] 检查并更新看板状态...")
        if config.get("GITHUB_BOARD_NUMBER"):
            status_updated = gh_update_project_status(
                issue_number=issue_number,
                task_type="Task2",
                config=config,
                issue_docs_prefix=ISSUE_DOCS_PREFIX,
                cwd=abs_worktree_path,
            )
            if status_updated:
                print(f"  [OK] 看板状态已更新: {STATUS_TASK1} → {STATUS_TASK2}")
                # 同步更新 flow_meta.json 的 status 字段
                meta = flow_meta_read(issue_number, ISSUE_DOCS_PREFIX, cwd=abs_worktree_path)
                if meta:
                    meta["status"] = STATUS_TASK2
                    meta_filepath = os.path.join(abs_worktree_path, _meta_path(issue_number, ISSUE_DOCS_PREFIX))
                    try:
                        with open(meta_filepath, "w", encoding="utf-8") as f:
                            json.dump(meta, f, indent=2, ensure_ascii=False)
                        print(f"  [OK] flow_meta.json status 已同步更新: {STATUS_TASK2}")
                    except OSError as e:
                        print(f"  [WARN] flow_meta.json status 同步更新失败: {e}", file=sys.stderr)
            else:
                print("  [WARN] 看板状态更新失败（非致命，可手动更新）")
        else:
            print("  [SKIP] GITHUB_BOARD_NUMBER 未配置，跳过看板状态更新")

        print("\n===== 执行成功 =====")
        print(f"Issue #{issue_number} [{', '.join(labels)}] → branch: {branch_name}, worktree: {WORKTREE_PREFIX}/{branch_name}")

    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        print("\n异常发生，确保清理...")
        sys.exit(1)

    finally:
        # ===== 清理 run.lock =====
        print("\n[Finally] 删除 run.lock...")
        # 优先从 worktree 删除
        wt_path = locals().get("abs_worktree_path")
        if wt_path and os.path.isdir(wt_path):
            lock_delete(issue_number, ISSUE_DOCS_PREFIX, cwd=wt_path)
        # 也从主仓库删除（Step4 在主仓库创建的）
        lock_delete(issue_number, ISSUE_DOCS_PREFIX, cwd=CWD)
        print("  [OK] run.lock 已删除")


if __name__ == "__main__":
    main()
