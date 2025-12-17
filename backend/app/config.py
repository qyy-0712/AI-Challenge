from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel, Field


class Settings(BaseModel):
    # GitHub OAuth / Token
    github_client_id: str = Field(default="Ov23liwlO42eYafeibMD")
    github_client_secret: str = Field(default="9fa9fdc466398835f8aa4717a70b3521e202590d")
    github_token: str = Field(default="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")
    github_oauth_redirect: str = Field(default="http://localhost:5173/auth/callback")

    # LLM
    llm_api_key: str = Field(default="f31db8c0ee1f484c8f50eb859421df45.dD5qP8BBgomVB3oz")
    llm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4")
    llm_model: str = Field(default="glm-4.5-flash")

    # MCP endpoints（为空则使用本地降级规则）
    mcp_compile_endpoint: str = Field(default="")
    mcp_static_endpoint: str = Field(default="")
    mcp_dependency_endpoint: str = Field(default="")
    mcp_security_endpoint: str = Field(default="")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

