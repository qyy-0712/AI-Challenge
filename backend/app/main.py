from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from fastapi.responses import FileResponse

from .config import Settings, get_settings
from .github_client import GitHubClient
from .graph import run_review
from .report_store import find_report_file
from .schemas import (
    OAuthURL,
    PullRequestInfo,
    RepoInfo,
    ReviewRequest,
    ReviewResponse,
    OAuthExchangeRequest,
    OAuthToken,
)


def get_app_settings() -> Settings:
    return get_settings()


app = FastAPI(title="PR AI Reviewer", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/auth/login-url", response_model=OAuthURL)
async def get_login_url(settings: Settings = Depends(get_app_settings)) -> OAuthURL:
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={settings.github_oauth_redirect}"
        "&scope=repo"
    )
    return OAuthURL(url=url)


@app.get("/repos", response_model=list[RepoInfo])
async def list_repositories(
    github_token: str | None = Header(default=None, alias="X-Github-Token"),
    settings: Settings = Depends(get_app_settings),
):
    client = GitHubClient(token=github_token or settings.github_token)
    return await client.list_repos()


@app.get("/repos/{repo_full_name}/prs", response_model=list[PullRequestInfo])
async def list_pull_requests(
    repo_full_name: str,
    github_token: str | None = Header(default=None, alias="X-Github-Token"),
    settings: Settings = Depends(get_app_settings),
):
    client = GitHubClient(token=github_token or settings.github_token)
    return await client.list_open_prs(repo_full_name)


@app.post("/review", response_model=ReviewResponse)
async def review_pr(
    payload: ReviewRequest,
    github_token: str | None = Header(default=None, alias="X-Github-Token"),
    settings: Settings = Depends(get_app_settings),
):
    if not (github_token or settings.github_token):
        raise HTTPException(status_code=400, detail="GitHub token missing")
    return await run_review(payload, settings=settings, token=github_token or settings.github_token)


@app.get("/review/{review_id}/export")
async def export_review(review_id: str):
    p = find_report_file(review_id)
    if not p:
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(
        path=str(p),
        media_type="text/markdown; charset=utf-8",
        filename=p.name,
    )


@app.post("/auth/exchange", response_model=OAuthToken)
async def exchange_code(payload: OAuthExchangeRequest, settings: Settings = Depends(get_app_settings)) -> OAuthToken:
    """
    前端 OAuth 回调后，用 code 交换 access_token。
    """
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(status_code=500, detail="GitHub OAuth 未配置")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": payload.code,
                "redirect_uri": settings.github_oauth_redirect,
            },
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=400, detail="code 交换失败")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise HTTPException(status_code=400, detail="未获取到 access_token")
        return OAuthToken(access_token=token, token_type=data.get("token_type", "bearer"))


def create_app() -> FastAPI:
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

