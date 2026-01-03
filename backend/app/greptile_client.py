from __future__ import annotations

import asyncio
import os
import time
from typing import Any, List, Optional

import httpx
import json

from .config import Settings


class GreptileMCPClient:
    """
    Greptile 官方 HTTP 集成是 MCP(JSON-RPC)：
    POST https://api.greptile.com/mcp
    body: { jsonrpc:'2.0', id, method:'tools/list'|'tools/call', params:{...} }
    """

    def __init__(self, settings: Settings, *, github_token: str = ""):
        self.api_key = (settings.greptile_api_key or "").strip()
        self.url = (settings.greptile_mcp_url or "").strip()
        # Best-effort: pass GitHub token through to Greptile MCP for PR access.
        # Some deployments require an explicit GitHub token to fetch PR data, especially for private repos.
        # IMPORTANT: prefer per-request token if provided (so users can review private repos without
        # baking tokens into server env).
        self.github_token = (github_token or getattr(settings, "github_token", "") or "").strip()

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if self.github_token:
            # Try common header spellings (safe; servers ignore unknown headers).
            headers["X-Github-Token"] = self.github_token
            headers["X-GitHub-Token"] = self.github_token
        return headers

    async def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        # Never hit external services during pytest (keeps unit tests hermetic even if .env exists).
        if os.getenv("PYTEST_CURRENT_TEST"):
            raise RuntimeError("Greptile MCP disabled under pytest")
        if not self.api_key:
            raise ValueError("Missing GREPTILE_API_KEY")
        if not self.url:
            raise ValueError("Missing GREPTILE_MCP_URL")
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method,
            "params": params or {},
        }
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(self.url, headers=self._headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = (exc.response.text or "")[:800]
            except Exception:
                body = ""
            raise RuntimeError(f"Greptile MCP HTTP {exc.response.status_code}: {body}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Greptile MCP network error: {type(exc).__name__}") from exc

        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            err = data["error"]
            code = err.get("code")
            msg = err.get("message")
            raise RuntimeError(f"Greptile MCP error code={code} message={msg}")
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Greptile MCP error: {data.get('error')}")
        return data if isinstance(data, dict) else {}

    async def list_tools(self) -> List[dict]:
        data = await self._rpc("tools/list", {})
        result = data.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            return result["tools"]
        if isinstance(result, list):
            return result
        return []

    async def call_tool(self, name: str, arguments: dict) -> Any:
        data = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        result = data.get("result")
        # Greptile MCP HTTP wraps tool results in MCP-style content blocks:
        # { "result": { "content": [ { "type": "text", "text": "{...json...}" } ] } }
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            texts: list[str] = []
            for blk in result.get("content") or []:
                if isinstance(blk, dict) and isinstance(blk.get("text"), str):
                    texts.append(blk["text"])
            joined = "\n".join(texts).strip()
            if joined:
                # many tools return JSON serialized as text
                try:
                    return json.loads(joined)
                except Exception:
                    return joined
            return {}
        return result

    async def trigger_code_review(self, *, name: str, default_branch: str, pr_number: int, remote: str = "github") -> bool:
        # https://www.greptile.com/docs/mcp/tool-reference#code-reviews
        res = await self.call_tool(
            "trigger_code_review",
            {"name": name, "remote": remote, "defaultBranch": default_branch, "prNumber": pr_number},
        )
        if isinstance(res, dict):
            return bool(res.get("success", True))
        return True

    async def list_code_reviews(
        self,
        *,
        name: str,
        default_branch: str,
        pr_number: int,
        remote: str = "github",
        status: str = "COMPLETED",
        limit: int = 20,
        offset: int = 0,
    ) -> List[dict]:
        # https://www.greptile.com/docs/mcp/tool-reference#code-reviews
        res = await self.call_tool(
            "list_code_reviews",
            {
                "name": name,
                "remote": remote,
                "defaultBranch": default_branch,
                "prNumber": pr_number,
                "status": status,
                "limit": limit,
                "offset": offset,
            },
        )
        if isinstance(res, dict) and isinstance(res.get("codeReviews"), list):
            return res["codeReviews"]
        if isinstance(res, list):
            return res
        return []

    async def get_code_review(self, code_review_id: str) -> dict:
        # https://www.greptile.com/docs/mcp/tool-reference#code-reviews
        res = await self.call_tool("get_code_review", {"codeReviewId": code_review_id})
        if isinstance(res, dict) and isinstance(res.get("codeReview"), dict):
            return res["codeReview"]
        return res if isinstance(res, dict) else {}

    async def list_merge_request_comments(
        self,
        *,
        name: str,
        default_branch: str,
        pr_number: int,
        remote: str = "github",
        greptile_generated: bool = True,
        addressed: Optional[bool] = None,
    ) -> List[dict]:
        # https://www.greptile.com/docs/mcp/tool-reference#pull-requests
        args = {
            "name": name,
            "remote": remote,
            "defaultBranch": default_branch,
            "prNumber": pr_number,
            "greptileGenerated": greptile_generated,
        }
        if addressed is not None:
            args["addressed"] = addressed
        res = await self.call_tool("list_merge_request_comments", args)
        if isinstance(res, dict) and isinstance(res.get("comments"), list):
            return res["comments"]
        if isinstance(res, list):
            return res
        return []

    async def get_pr_review_bundle(
        self,
        *,
        name: str,
        default_branch: str,
        pr_number: int,
        remote: str = "github",
        poll_timeout_s: float = 25.0,
    ) -> tuple[str, List[dict]]:
        """
        触发代码审查 -> 轮询拿到 COMPLETED 的 code review body -> 同时拉取 Greptile 生成的评论。
        """
        def _is_auth_error(exc: Exception) -> bool:
            msg = (str(exc) or "").lower()
            # Greptile MCP typical messages:
            # - "Unauthorized: Repository does not belong to your organization"
            # - "401" or "unauthorized"
            return ("unauthorized" in msg) or ("does not belong to your organization" in msg) or ("401" in msg)

        last_exc: Optional[Exception] = None
        # 0) fast path: if greptile comments already exist, return quickly (no trigger/poll).
        try:
            existing_comments = await self.list_merge_request_comments(
                name=name, default_branch=default_branch, pr_number=pr_number, remote=remote, greptile_generated=True, addressed=False
            )
            if existing_comments:
                return "", existing_comments
        except Exception as exc:
            last_exc = exc
            if _is_auth_error(exc):
                raise

        # 1) see if a completed review already exists
        try:
            reviews = await self.list_code_reviews(
                name=name, default_branch=default_branch, pr_number=pr_number, remote=remote, status="COMPLETED", limit=10
            )
            if reviews:
                rid = None
                for r in reviews:
                    if isinstance(r, dict) and r.get("id"):
                        rid = r["id"]
                        break
                if rid:
                    cr = await self.get_code_review(str(rid))
                    body = cr.get("body") if isinstance(cr, dict) else ""
                    if isinstance(body, str) and body.strip():
                        try:
                            comments = await self.list_merge_request_comments(
                                name=name,
                                default_branch=default_branch,
                                pr_number=pr_number,
                                remote=remote,
                                greptile_generated=True,
                                addressed=False,
                            )
                        except Exception as exc:
                            last_exc = exc
                            if _is_auth_error(exc):
                                raise
                            comments = []
                        return body.strip(), comments
        except Exception as exc:
            last_exc = exc
            if _is_auth_error(exc):
                raise

        # 2) trigger (best-effort)
        try:
            await self.trigger_code_review(name=name, default_branch=default_branch, pr_number=pr_number, remote=remote)
        except Exception as exc:
            # If already triggered or not allowed, continue polling anyway.
            last_exc = exc
            if _is_auth_error(exc):
                raise

        # 3) poll for completed review (bounded)
        deadline = time.time() + max(5.0, float(poll_timeout_s))
        code_review_body = ""
        while time.time() < deadline:
            try:
                reviews = await self.list_code_reviews(
                    name=name, default_branch=default_branch, pr_number=pr_number, remote=remote, status="COMPLETED", limit=10
                )
            except Exception as exc:
                last_exc = exc
                if _is_auth_error(exc):
                    raise
                reviews = []
            if reviews:
                rid = None
                for r in reviews:
                    if isinstance(r, dict) and r.get("id"):
                        rid = r["id"]
                        break
                if rid:
                    try:
                        cr = await self.get_code_review(str(rid))
                    except Exception as exc:
                        last_exc = exc
                        if _is_auth_error(exc):
                            raise
                        cr = {}
                    body = cr.get("body") if isinstance(cr, dict) else ""
                    if isinstance(body, str) and body.strip():
                        code_review_body = body.strip()
                        break
            await asyncio.sleep(1.5)

        # 4) comments (file/line evidence)
        comments: List[dict] = []
        try:
            comments = await self.list_merge_request_comments(
                name=name, default_branch=default_branch, pr_number=pr_number, remote=remote, greptile_generated=True, addressed=False
            )
        except Exception as exc:
            last_exc = exc
            if _is_auth_error(exc):
                raise
            comments = []

        # If we got nothing and there was an upstream error, surface it to the caller
        # so the app can display a useful diagnostic instead of "no issues".
        if not code_review_body and not comments and last_exc:
            raise last_exc
        return code_review_body, comments


