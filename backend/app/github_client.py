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

