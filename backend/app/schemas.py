from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class OAuthURL(BaseModel):
    url: str


class OAuthExchangeRequest(BaseModel):
    code: str


class OAuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RepoInfo(BaseModel):
    full_name: str
    default_branch: str


class PullRequestInfo(BaseModel):
    number: int
    title: str
    url: str


class ReviewRequest(BaseModel):
    repo_full_name: str = Field(..., description="owner/name")
    pr_number: int
    requirements: Optional[str] = None


class Finding(BaseModel):
    file: Optional[str] = None
    line: Optional[int] = None
    level: Literal["critical", "high", "medium", "low"] = "medium"
    category: str
    title: str
    detail: str
    suggestion: str


class ReviewResponse(BaseModel):
    review_id: str
    report_markdown: str
    findings: List[Finding]

