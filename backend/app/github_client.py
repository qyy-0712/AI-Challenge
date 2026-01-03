from __future__ import annotations

import httpx
from typing import List, Optional

from .config import get_settings
from .schemas import PullRequestInfo, RepoInfo


class GitHubClient:
    def __init__(self, token: Optional[str] = None):
        settings = get_settings()
        self.token = token or settings.github_token
        self.base = "https://api.github.com"

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def list_repos(self) -> List[RepoInfo]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base}/user/repos", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            return [
                RepoInfo(full_name=item["full_name"], default_branch=item["default_branch"])
                for item in data
            ]

    async def list_open_prs(self, repo_full_name: str) -> List[PullRequestInfo]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base}/repos/{repo_full_name}/pulls?state=open",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                PullRequestInfo(number=item["number"], title=item["title"], url=item["html_url"])
                for item in data
            ]

    async def fetch_repo_default_branch(self, repo_full_name: str) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base}/repos/{repo_full_name}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("default_branch") or "main")

    async def fetch_diff(self, repo_full_name: str, pr_number: int) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base}/repos/{repo_full_name}/pulls/{pr_number}",
                headers={**self._headers(), "Accept": "application/vnd.github.v3.diff"},
            )
            resp.raise_for_status()
            return resp.text

    async def fetch_files(self, repo_full_name: str, pr_number: int) -> List[str]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base}/repos/{repo_full_name}/pulls/{pr_number}/files",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return [item["filename"] for item in data]

    async def fetch_pr_files_meta(self, repo_full_name: str, pr_number: int) -> List[dict]:
        """
        返回 PR files 元数据（不拉取 raw 内容），用于快速构建上下文/编译守卫。
        结构: {path,status,patch,raw_url}
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base}/repos/{repo_full_name}/pulls/{pr_number}/files",
                headers=self._headers(),
            )
            resp.raise_for_status()
            items = resp.json()
            results = []
            for item in items:
                results.append(
                    {
                        "path": item.get("filename"),
                        "status": item.get("status"),
                        "patch": item.get("patch"),
                        "raw_url": item.get("raw_url"),
                        "content": "",
                    }
                )
            return results

    async def fetch_raw_text(self, raw_url: str) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(raw_url, headers=self._headers())
            resp.raise_for_status()
            return resp.text

    async def fetch_issue_comments(self, repo_full_name: str, pr_number: int) -> List[dict]:
        """
        PR 的 issue comments（/issues/{n}/comments）。很多机器人（含 Greptile）会把总审查贴在这里。
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base}/repos/{repo_full_name}/issues/{pr_number}/comments",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []

    async def fetch_review_comments(self, repo_full_name: str, pr_number: int) -> List[dict]:
        """
        PR 的 review comments（行内评论）。
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base}/repos/{repo_full_name}/pulls/{pr_number}/comments",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []

    async def fetch_greptile_reference_text(self, repo_full_name: str, pr_number: int) -> str:
        """
        尝试从 PR 评论里提取 Greptile 的审查报告（强参考）。
        这是“无 Greptile HTTP API/文档情况下”的稳妥方案：只要 Greptile 机器人已经向 PR 发过评论即可。
        """
        try:
            issue_comments = await self.fetch_issue_comments(repo_full_name, pr_number)
        except Exception:
            issue_comments = []
        try:
            review_comments = await self.fetch_review_comments(repo_full_name, pr_number)
        except Exception:
            review_comments = []

        candidates: list[str] = []
        for c in (issue_comments or []) + (review_comments or []):
            user = (c.get("user") or {}).get("login") or ""
            body = c.get("body") or ""
            if not body:
                continue
            u = user.lower()
            b = body.lower()
            if "greptile" in u or "greptile" in b:
                candidates.append(body)
        # Prefer the longest (likely the summary report)
        candidates.sort(key=lambda s: len(s), reverse=True)
        return candidates[0] if candidates else ""

    async def fetch_pr_files_with_content(self, repo_full_name: str, pr_number: int) -> List[dict]:
        """
        返回包含文件路径与内容的列表，便于本地 MCP 工具执行。
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base}/repos/{repo_full_name}/pulls/{pr_number}/files",
                headers=self._headers(),
            )
            resp.raise_for_status()
            items = resp.json()
            results = []
            for item in items:
                raw_url = item.get("raw_url")
                content = ""
                if raw_url:
                    try:
                        raw_resp = await client.get(raw_url, headers=self._headers())
                        raw_resp.raise_for_status()
                        content = raw_resp.text
                    except Exception:
                        content = ""
                results.append(
                    {
                        "path": item.get("filename"),
                        "status": item.get("status"),
                        "patch": item.get("patch"),
                        "content": content,
                    }
                )
            return results

