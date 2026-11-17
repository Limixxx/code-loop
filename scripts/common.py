#!/usr/bin/env python3
"""common.py — code-loop 脚本公共模块

抽取自 issue_dispatch / task1~task4 中完全一致的公共函数，
供所有脚本通过 `from common import ...` 引用，消除重复代码。

模块包含：
  - 参数定义基础集合 (_PARAM_DEFS_BASE)
  - 配置加载 (_load_env_file, load_config_from_env)
  - 命令封装 (_run_gh, _run_git, _run_git_ok)
  - Lock 管理 (_lock_path, lock_exists, lock_create, lock_delete, lock_ensure_gitignore)
  - Flow Meta 读写 (_meta_path, flow_meta_read, flow_meta_update)
  - 看板状态更新 (gh_update_project_status)
  - PR 操作 (gh_get_pr_state, gh_get_pr_comments, gh_create_pr, pr_should_rerun)
  - Issue 信息获取 (gh_get_issue_info)
  - task1 特有版本 (gh_update_project_status_task1)
"""

import os
import sys
import json
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# ============================================================
# 参数定义基础集合（各脚本可在此基础上扩展）
# ============================================================

_PARAM_DEFS_BASE: Dict[str, tuple] = {
    "REPO":                          ("",              True,  "GitHub 仓库 (owner/name)"),
    "BASE_BRANCH":                   ("",              True,  "基准分支"),
    "ASSIGNEE":                      ("",              True,  "GitHub 用户名（被分配人）"),
    "BRANCH_PREFIX":                 ("issue-",        False, "分支名前缀"),
    "WORKTREE_PREFIX":               (".worktree",     False, "Worktree 根目录"),
    "ISSUE_DOCS_PREFIX":             (".issue",        False, "Issue 文档路径前缀"),
    "STATUS_TASK1":                  ("Backlog",       False, "Task1 对应的看板状态名"),
    "STATUS_TASK2":                  ("Ready",         False, "Task2 对应的看板状态名"),
    "STATUS_TASK3":                  ("In progress",   False, "Task3 对应的看板状态名"),
    "STATUS_TASK4":                  ("Done",          False, "Task4 对应的看板状态名"),
    "GITHUB_BOARD_NAME":             ("",              True,  "GitHub Project Board 名称"),
    "GITHUB_BOARD_NUMBER":           ("",              True,  "GitHub Project Board 编号"),
    "GITHUB_BOARD_ID":               ("",              True,  "GitHub Project Board ID (PVT_...)"),
    "GITHUB_BOARD_STATUS_FIELD_ID":  ("",              True,  "看板状态字段 ID (PVTSSF_...)"),
    "GITHUB_BOARD_STATUS_TASK1":     ("",              True, "Task1 看板状态选项 ID"),
    "GITHUB_BOARD_STATUS_TASK2":     ("",              True, "Task2 看板状态选项 ID"),
    "GITHUB_BOARD_STATUS_TASK3":     ("",              True, "Task3 看板状态选项 ID"),
    "GITHUB_BOARD_STATUS_TASK4":     ("",              True, "Task4 看板状态选项 ID"),
}


# ============================================================
# 配置加载
# ============================================================

def _load_env_file(env_path: str) -> Dict[str, str]:
    """加载 .env 文件为键值字典。格式：KEY=VALUE，支持 # 注释。"""
    env: Dict[str, str] = {}
    if not os.path.isfile(env_path):
        return env
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not (value[:1] in ('"', "'")):
                if " #" in value:
                    value = value[:value.index(" #")].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and value:
                env[key] = value
    return env


def load_config_from_env(env_path: Optional[str] = None,
                         param_defs: Optional[Dict[str, tuple]] = None) -> Dict[str, Any]:
    """从 .env 文件加载配置。优先级：环境变量 > .env 文件 > 默认值。

    Args:
        env_path: .env 文件路径，默认为 .worktree/.env
        param_defs: 参数定义字典，默认使用 _PARAM_DEFS_BASE
    """
    if env_path is None:
        env_path = os.path.join(os.getcwd(), ".worktree", ".env")
    if param_defs is None:
        param_defs = _PARAM_DEFS_BASE
    file_env = _load_env_file(env_path)
    config: Dict[str, Any] = {}
    for key, (default, _req, _desc) in param_defs.items():
        if key in os.environ:
            config[key] = os.environ[key]
        elif key in file_env:
            config[key] = file_env[key]
        else:
            config[key] = default
    # 校验必填
    missing = [k for k, (_, req, _) in param_defs.items() if req and not config.get(k)]
    if missing:
        raise ValueError(
            f"Required config missing (set in {env_path} or environment): {', '.join(missing)}"
        )
    return config


# ============================================================
# 底层命令封装
# ============================================================

def _run_gh(args: list, repo: Optional[str] = None) -> str:
    """执行 gh 命令，返回 stdout。"""
    cmd = ["gh"] + args
    if repo:
        cmd += ["--repo", repo]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"WARN: gh command failed: {e}", file=sys.stderr)
        return ""


def _run_git(args: list, cwd: Optional[str] = None, timeout: int = 60) -> (bool, str):
    """执行 git 命令，返回 stdout。"""
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        else:
            return True, result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"WARN: git command failed: {e}", file=sys.stderr)
        return False, ""


# ============================================================
# Lock 管理
# ============================================================

def _lock_path(issue_number: int, issue_docs_prefix: str) -> str:
    """获取 run.lock 文件路径。"""
    return os.path.join(issue_docs_prefix, str(issue_number), "run.lock")


def lock_exists(issue_number: int, issue_docs_prefix: str,
                cwd: Optional[str] = None) -> bool:
    """检查 run.lock 是否存在。"""
    lock_file = _lock_path(issue_number, issue_docs_prefix)
    if cwd:
        lock_file = os.path.join(cwd, lock_file)
    print(f"lock_exists: {lock_file}")
    return os.path.isfile(lock_file)


def lock_create(issue_number: int, phase: str, issue_docs_prefix: str,
                cwd: Optional[str] = None) -> bool:
    """创建 run.lock（原子操作）。"""
    lock_file = _lock_path(issue_number, issue_docs_prefix)
    if cwd:
        lock_file = os.path.join(cwd, lock_file)
    print(f"lock_create: {lock_file}")
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)
    lock_data = {
        "issue_number": issue_number,
        "phase": phase,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "locked_by": "Code-Loop",
    }
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, indent=2, ensure_ascii=False)
        return True
    except FileExistsError:
        return False
    except OSError as e:
        print(f"ERROR: Cannot create lock file: {e}", file=sys.stderr)
        return False


def lock_delete(issue_number: int, issue_docs_prefix: str,
                cwd: Optional[str] = None) -> bool:
    """删除 run.lock（始终安全调用）。"""
    lock_file = _lock_path(issue_number, issue_docs_prefix)
    if cwd:
        lock_file = os.path.join(cwd, lock_file)
    print(f"lock_delete: {lock_file}")
    try:
        if os.path.isfile(lock_file):
            os.unlink(lock_file)
        return True
    except OSError as e:
        print(f"WARN: Cannot delete lock file: {e}", file=sys.stderr)
        return False


# def lock_ensure_gitignore(cwd: Optional[str] = None) -> bool:
#     """确保 .gitignore 包含 run.lock 排除规则。"""
#     gitignore_path = ".gitignore"
#     if cwd:
#         gitignore_path = os.path.join(cwd, gitignore_path)
#     rule = "**/.issue/*/run.lock"
#     try:
#         if os.path.isfile(gitignore_path):
#             with open(gitignore_path, "r", encoding="utf-8") as f:
#                 content = f.read()
#             if rule in content:
#                 return True
#             with open(gitignore_path, "a", encoding="utf-8") as f:
#                 f.write(f"\n{rule}\n")
#         else:
#             with open(gitignore_path, "w", encoding="utf-8") as f:
#                 f.write(f"{rule}\n")
#         return True
#     except OSError as e:
#         print(f"WARN: Cannot update .gitignore: {e}", file=sys.stderr)
#         return False


# ============================================================
# Flow Meta 读写
# ============================================================

def _meta_path(issue_number: int, issue_docs_prefix: str) -> str:
    """获取 flow_meta.json 文件路径。"""
    return os.path.join(issue_docs_prefix, str(issue_number), "flow_meta.json")


def flow_meta_read(issue_number: int, issue_docs_prefix: str,
                   cwd: Optional[str] = None) -> Dict[str, Any]:
    """读取 flow_meta.json。"""
    filepath = _meta_path(issue_number, issue_docs_prefix)
    if cwd:
        filepath = os.path.join(cwd, filepath)
    print(f"flow_meta_read: {filepath}")
    if not os.path.isfile(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARN: Cannot read flow_meta.json: {e}", file=sys.stderr)
        return {}


def flow_meta_update(issue_number: int, issue_docs_prefix: str,
                     updates: Dict[str, Any], cwd: Optional[str] = None) -> bool:
    """更新 flow_meta.json 中的指定字段。"""
    filepath = _meta_path(issue_number, issue_docs_prefix)
    if cwd:
        filepath = os.path.join(cwd, filepath)
    print(f"flow_meta_update: {filepath}")
    meta = flow_meta_read(issue_number, issue_docs_prefix, cwd)
    if not meta:
        print(f"WARN: flow_meta.json not found for issue #{issue_number}", file=sys.stderr)
        return False
    meta.update(updates)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        return True
    except OSError as e:
        print(f"ERROR: Cannot update flow_meta.json: {e}", file=sys.stderr)
        return False


# ============================================================
# 看板状态更新
# ============================================================

def gh_update_project_status(
    issue_number: int, task_type: str, config: Dict[str, Any], issue_docs_prefix: str,
    cwd: Optional[str] = None,
) -> bool:
    """更新项目板中 issue 的看板状态。"""
    meta = flow_meta_read(issue_number, issue_docs_prefix, cwd)
    item_id = meta.get("project_item_id")
    if not item_id:
        print(f"ERROR: Issue #{issue_number} not found in project board", file=sys.stderr)
        return False

    option_id = config.get(f"GITHUB_BOARD_STATUS_{task_type.upper()}", "")
    if not option_id:
        print(f"ERROR: GITHUB_BOARD_STATUS_{task_type} not configured", file=sys.stderr)
        return False

    project_id = config.get("GITHUB_BOARD_ID", "")
    field_id = config.get("GITHUB_BOARD_STATUS_FIELD_ID", "")
    if not project_id or not field_id:
        print("ERROR: GITHUB_BOARD_ID or GITHUB_BOARD_STATUS_FIELD_ID not configured", file=sys.stderr)
        return False

    cmd = [
        "gh", "project", "item-edit",
        "--id", item_id,
        "--field-id", field_id,
        "--project-id", project_id,
        "--single-select-option-id", option_id,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            print(f"ERROR: gh project item-edit failed: {err}", file=sys.stderr)
            return False
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"ERROR: gh project item-edit failed: {e}", file=sys.stderr)
        return False


# ============================================================
# PR 操作
# ============================================================

def gh_get_pr_state(repo: str, pr_number: int) -> str:
    """获取 PR 状态。返回 merged / closed / open / ""。"""
    output = _run_gh([
        "pr", "view", str(pr_number),
        "--json", "state",
    ], repo)
    if not output:
        return ""
    try:
        data = json.loads(output)
        return data.get("state", "").lower()
    except json.JSONDecodeError:
        return ""


def gh_get_pr_comments(repo: str, pr_number: int) -> str:
    """获取 PR 的评审评论。"""
    output = _run_gh([
        "pr", "view", str(pr_number),
        "--comments", "--json", "comments",
    ], repo)
    if not output:
        return ""
    try:
        data = json.loads(output)
        comments = data.get("comments", [])
        if not comments:
            return ""
        parts = []
        for c in comments:
            author = c.get("author", {}).get("login", "unknown")
            body = c.get("body", "")
            parts.append(f"[{author}]: {body}")
        return "\n---\n".join(parts)
    except json.JSONDecodeError:
        return ""


def gh_create_pr(repo: str, branch_name: str, title: str, body: str,
                 base_branch: str) -> str:
    """创建 PR 并返回原始输出。"""
    cmd = [
        "gh", "pr", "create",
        "--repo", repo,
        "--head", branch_name,
        "--base", base_branch,
        "--title", title,
        "--body", body,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        return result.stdout.strip() + result.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"ERROR: gh pr create failed: {e}", file=sys.stderr)
        return ""


def pr_should_rerun(repo: str, pr_number: Optional[int]) -> str:
    """判断 PR 是否需要重跑。返回 DONE / WAIT / RERUN / ABORT / FIRST。"""
    if not pr_number:
        return "FIRST"
    state = gh_get_pr_state(repo, pr_number)
    if state == "merged":
        return "DONE"
    if state == "open":
        return "WAIT"
    if state == "closed":
        return "RERUN"
    return "FIRST"


# ============================================================
# Issue 信息获取
# ============================================================

def gh_get_issue_info(repo: str, issue_number: int) -> Dict[str, Any]:
    """从 GitHub 获取 issue 的标题、标签和正文。"""
    output = _run_gh([
        "issue", "view", str(issue_number),
        "--json", "title,labels,body",
    ], repo)
    if not output:
        print(f"WARN: Cannot fetch issue #{issue_number} info", file=sys.stderr)
        return {}
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        print(f"WARN: Failed to parse issue #{issue_number} JSON", file=sys.stderr)
        return {}