#!/usr/bin/env python3
"""config.py — 配置管理类

将分散在 common.py / issue_dispatch.py / task1~task4 中
关于 config 的所有读取与更新操作提炼为统一的 Config 类。

现有代码不做修改，本模块可作为后续重构的统一配置入口。
"""

import os
import sys
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class Config:
    """统一配置管理类，封装 .env 加载、键值读取、派生路径计算与 flow_meta 更新。"""

    # ============================================================
    # 参数定义基础集合（各脚本可在此基础上扩展）
    # ============================================================

    _param_defs: Dict[str, tuple] = {
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

    def __init__(
        self,
        env_path: Optional[str] = None,
    ):
        """初始化并加载配置。

        Args:
            env_path: .env 文件路径，默认为 .worktree/.env
        """

        self.load_config_from_env(os.path.join(os.getcwd(), ".worktree", ".env") if env_path is None else env_path)


    def load_config_from_env(self, env_path: Optional[str] = None) -> Dict[str, Any]:
        # 默认 当前目录/.worktree/.env
        if env_path is None:
            env_path = os.path.join(os.getcwd(), ".worktree", ".env")

        # 加载 .env 文件
        env: Dict[str, str] = {}
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

        # 合并环境变量、.env 文件与默认值
        config: Dict[str, Any] = {}
        for key, (default, _req, _desc) in self._param_defs.items():
            if key in os.environ:
                config[key] = os.environ[key]
            elif key in file_env:
                config[key] = file_env[key]
            else:
                config[key] = default
                
        # 校验必填项
        missing = [k for k, (_, req, _) in self._param_defs.items() if req and not config.get(k)]
        if missing:
            raise ValueError(
                f"Required config missing (set in {env_path} or environment): {', '.join(missing)}"
            )
        return config

    # ============================================================
    # 基础键值访问
    # ============================================================

    def get(self, key: str, default: Any = None) -> Any:
        """读取配置值，不存在则返回 default。"""
        return self._param_defs.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """以 config["KEY"] 方式读取配置值。"""
        return self._param_defs[key]

    def __contains__(self, key: str) -> bool:
        return key in self._param_defs

    def keys(self):
        return self._param_defs.keys()

    def items(self):
        return self._param_defs.items()

    def as_dict(self) -> Dict[str, Any]:
        """返回配置的完整字典副本。"""
        return self._param_defs.copy()

    # ============================================================
    # 常用配置快捷属性
    # ============================================================

    @property
    def repo(self) -> str:
        return self._param_defs["REPO"]

    @property
    def base_branch(self) -> str:
        return self._param_defs["BASE_BRANCH"]

    @property
    def assignee(self) -> str:
        return self._param_defs["ASSIGNEE"]

    @property
    def branch_prefix(self) -> str:
        return self._param_defs["BRANCH_PREFIX"]

    @property
    def worktree_prefix(self) -> str:
        return self._param_defs["WORKTREE_PREFIX"]

    @property
    def issue_docs_prefix(self) -> str:
        return self._param_defs["ISSUE_DOCS_PREFIX"]

    @property
    def github_board_name(self) -> str:
        return self._param_defs["GITHUB_BOARD_NAME"]

    # ============================================================
    # 派生路径计算
    # ============================================================

    def get_worktree_name(self, issue_number: int) -> str:
        """根据 issue 编号生成分支名。"""
        return f"{self.branch_prefix}{issue_number}"

    def get_worktree_path(self, cwd: str, issue_number: int) -> str:
        """根据 issue 编号计算 worktree 完整路径。"""
        return os.path.join(
            cwd,
            self.worktree_prefix,
            self.get_worktree_name(issue_number),
        )

    def get_issue_path(self, cwd: str, issue_number: int) -> str:
        """根据 issue 编号计算 issue 文档目录路径。"""
        return os.path.join(
            self.get_worktree_path(cwd, issue_number),
            self.issue_docs_prefix,
            str(issue_number),
        )

    def get_flow_meta_path(self, cwd: str, issue_number: int) -> str:
        """根据 issue 编号计算 flow_meta.json 完整路径。"""
        return os.path.join(
            self.get_issue_path(cwd, issue_number),
            self.issue_docs_prefix,
            str(issue_number),
            "flow_meta.json",
        )

    # ============================================================
    # flow_meta.json 读写
    # ============================================================
    def create_flow_meta(
        self, 
        cwd: str, 
        issue_number: int, 
        issue_title: str, 
        item_id: str) -> bool:
        meta = {
            "issue_number": issue_number,
            "issue_title": issue_title,
            "worktree_path": self.get_worktree_path(cwd, issue_number),
            "solution_pr_url": None,
            "solution_pr_number": None,
            "code_pr_url": None,
            "code_pr_number": None,
            "project_item_id": item_id,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return self.update_flow_meta(cwd, issue_number, meta)

    def read_flow_meta(self, cwd: str, issue_number: int) -> Dict[str, Any]:
        try:
            with open(self.get_flow_meta_path(cwd, issue_number), "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARN: Cannot read flow_meta.json: {e}", file=sys.stderr)
            return {}

    def update_flow_meta(self, cwd: str, issue_number: int, updates: Dict[str, Any]) -> bool:
        meta = self.read_flow_meta(cwd, issue_number)
        meta.update(updates)
        try:
            with open(self.get_flow_meta_path(cwd, issue_number), "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
            return True
        except OSError as e:
            print(f"ERROR: Cannot update flow_meta.json: {e}", file=sys.stderr)
            return False

    # ============================================================
    # run.lock 操作（委托 common）
    # ============================================================

    def lock_exists(self, issue_number: int, cwd: Optional[str] = None) -> bool:
        """检查 run.lock 是否存在。"""
        from common import lock_exists
        return lock_exists(issue_number, self.issue_docs_prefix, cwd=cwd)

    def lock_create(self, issue_number: int, phase: str, cwd: Optional[str] = None) -> bool:
        """创建 run.lock。"""
        from common import lock_create
        return lock_create(issue_number, phase, self.issue_docs_prefix, cwd=cwd)

    def lock_delete(self, issue_number: int, cwd: Optional[str] = None) -> bool:
        """删除 run.lock。"""
        from common import lock_delete
        return lock_delete(issue_number, self.issue_docs_prefix, cwd=cwd)
