#!/usr/bin/env python3
"""github.py — GitHub CLI 操作公共类

从 common.py / issue_dispatch.py / task1~task4 中提取所有 gh 命令操作，
封装为统一的 GitHub 类，供后续重构使用。

原始代码不做修改，本模块可作为统一的 GitHub 操作入口。

封装的操作：
  - 底层命令执行 (run_gh)
  - Issue 查询 (query_issues_with_status)
  - Issue 信息获取 (get_issue_info)
  - Issue 关闭 (close_issue)
  - PR 状态查询 (get_pr_state)
  - PR 评审评论获取 (get_pr_comments)
  - PR 创建 (create_pr)
  - PR 是否需要重跑 (pr_should_rerun)
  - 项目板状态更新 (update_project_status)
  - 项目板 Item ID 获取 (get_project_item_id)
"""

import json
import subprocess
import sys
from typing import Any, Dict, List, Optional


class GitHub:
    """GitHub CLI (gh) 操作封装类。"""

    def __init__(self, repo: Optional[str] = None, timeout: int = 60):
        """初始化 GitHub 操作类。

        Args:
            repo: 默认仓库 (owner/name)，执行命令时作为 --repo 参数
            timeout: 命令超时秒数
        """
        self.repo = repo
        self.timeout = timeout

    # ============================================================
    # 底层命令执行
    # ============================================================

    def run_gh(self, args: list, repo: Optional[str] = None) -> str:
        """执行 gh 命令，返回 stdout。

        Args:
            args: gh 子命令及参数列表（不含 "gh" 本身）
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
        """
        cmd = ["gh"] + args
        effective_repo = repo or self.repo
        if effective_repo:
            cmd += ["--repo", effective_repo]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                encoding="utf-8", errors="replace",
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"WARN: gh command failed: {e}", file=sys.stderr)
            return ""

    def run_gh_raw(self, args: list, repo: Optional[str] = None) -> subprocess.CompletedProcess:
        """执行 gh 命令，返回原始 CompletedProcess 对象。

        Args:
            args: gh 子命令及参数列表（不含 "gh" 本身）
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
        """
        cmd = ["gh"] + args
        effective_repo = repo or self.repo
        if effective_repo:
            cmd += ["--repo", effective_repo]
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                encoding="utf-8", errors="replace",
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"WARN: gh command failed: {e}", file=sys.stderr)
            # 返回一个模拟的失败结果
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr=str(e)
            )

    # ============================================================
    # Issue 操作
    # ============================================================

    def get_issue_info(self, repo: Optional[str], issue_number: int) -> Dict[str, Any]:
        """从 GitHub 获取 issue 的标题、标签和正文。

        Args:
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
            issue_number: Issue 编号
        """
        output = self.run_gh([
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

    def query_issues_with_status(
        self,
        repo: Optional[str],
        assignee: str,
        valid_labels: Optional[set] = None,
    ) -> List[Dict]:
        """查询已分配 issue 及其项目看板状态，按 label 筛选。

        Args:
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
            assignee: 被分配人用户名
            valid_labels: 合法标签集合；为 None 时不筛选
        """
        output = self.run_gh([
            "issue", "list",
            "--assignee", assignee,
            "--state", "open",
            "--json", "number,title,assignees,labels,state,url,projectItems",
            "--limit", "100",
        ], repo)
        if not output:
            return []
        try:
            issues = json.loads(output)
        except json.JSONDecodeError:
            print("WARN: Failed to parse issues JSON", file=sys.stderr)
            return []

        # 按 label 筛选
        if valid_labels:
            issues = [
                issue for issue in issues
                if {l.get("name", "") for l in issue.get("labels", [])} & valid_labels
            ]

        return issues

    def close_issue(self, issue_number: int, repo: Optional[str] = None) -> bool:
        """关闭指定 Issue。

        Args:
            issue_number: Issue 编号
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
        """
        result = self.run_gh_raw([
            "issue", "close", str(issue_number),
        ], repo)
        if result.returncode == 0:
            print(f"  [OK] Issue #{issue_number} closed")
            return True
        else:
            print(f"  WARN: 关闭 Issue #{issue_number} 失败: {result.stderr.strip()}", file=sys.stderr)
            return False

    # ============================================================
    # PR 操作
    # ============================================================

    def get_pr_state(self, repo: Optional[str], pr_number: int) -> str:
        """获取 PR 状态。返回 merged / closed / open / ""。

        Args:
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
            pr_number: PR 编号
        """
        output = self.run_gh([
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

    def get_pr_comments(self, repo: Optional[str], pr_number: int) -> str:
        """获取 PR 的评审评论。

        Args:
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
            pr_number: PR 编号
        """
        output = self.run_gh([
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

    def create_pr(
        self,
        repo: Optional[str],
        branch_name: str,
        title: str,
        body: str,
        base_branch: str,
    ) -> str:
        """创建 PR 并返回原始输出。

        Args:
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
            branch_name: 头部分支名
            title: PR 标题
            body: PR 正文
            base_branch: 基准分支
        """
        cmd = [
            "gh", "pr", "create",
            "--repo", repo or self.repo or "",
            "--head", branch_name,
            "--base", base_branch,
            "--title", title,
            "--body", body,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                encoding="utf-8", errors="replace",
            )
            return result.stdout.strip() + result.stderr.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"ERROR: gh pr create failed: {e}", file=sys.stderr)
            return ""

    def pr_should_rerun(self, repo: Optional[str], pr_number: Optional[int]) -> str:
        """判断 PR 是否需要重跑。返回 DONE / WAIT / RERUN / ABORT / FIRST。

        Args:
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
            pr_number: PR 编号，为 None 时返回 "FIRST"
        """
        if not pr_number:
            return "FIRST"
        state = self.get_pr_state(repo, pr_number)
        if state == "merged":
            return "DONE"
        if state == "open":
            return "WAIT"
        if state == "closed":
            return "RERUN"
        return "FIRST"

    # ============================================================
    # 项目板操作
    # ============================================================

    def update_project_status(
        self,
        issue_number: int,
        task_type: str,
        config: Dict[str, Any],
        issue_docs_prefix: str,
        meta: Dict[str, Any],
    ) -> bool:
        """更新项目板中 issue 的看板状态。

        Args:
            issue_number: Issue 编号
            task_type: 任务类型 (Task1/Task2/Task3/Task4)
            config: 配置字典，需包含 GITHUB_BOARD_ID / GITHUB_BOARD_STATUS_FIELD_ID / GITHUB_BOARD_STATUS_{TASK}
            issue_docs_prefix: Issue 文档路径前缀（保留兼容，当前未使用）
            meta: flow_meta 字典，需包含 project_item_id
        """
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
                cmd, capture_output=True, text=True, timeout=self.timeout,
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

    def get_project_item_id(
        self, repo: Optional[str], issue_number: int
    ) -> Optional[str]:
        """获取项目板中 issue 的 project_item_id。

        Args:
            repo: 仓库 (owner/name)，为 None 时使用实例默认值
            issue_number: Issue 编号
        """
        effective_repo = repo or self.repo or ""
        if "/" in effective_repo:
            owner, name = effective_repo.split("/", 1)
        else:
            owner, name = effective_repo, effective_repo

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
        output = self.run_gh([
            "api", "graphql",
            "-F", f"owner={owner}",
            "-F", f"repo={name}",
            "-F", f"issueNum={issue_number}",
            "-f", f"query={gql_query}",
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
        return None
