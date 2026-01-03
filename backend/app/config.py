from __future__ import annotations

import os
from functools import lru_cache

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, Field


# Load env from nearest .env in parent dirs (works even if cwd is backend/)
_dotenv_path = find_dotenv(usecwd=True)
load_dotenv(dotenv_path=_dotenv_path, override=False)


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v is not None else default


class Settings(BaseModel):
    # GitHub OAuth / Token
    github_client_id: str = Field(default_factory=lambda: _env("GITHUB_CLIENT_ID", ""))
    github_client_secret: str = Field(default_factory=lambda: _env("GITHUB_CLIENT_SECRET", ""))
    github_token: str = Field(default_factory=lambda: _env("GITHUB_TOKEN", ""))
    github_oauth_redirect: str = Field(default_factory=lambda: _env("GITHUB_OAUTH_REDIRECT", "http://localhost:5173/auth/callback"))

    # LLM
    llm_api_key: str = Field(default_factory=lambda: _env("GLM_API_KEY", ""))
    llm_base_url: str = Field(default_factory=lambda: _env("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"))
    llm_model: str = Field(default_factory=lambda: _env("GLM_MODEL", "glm-4.6"))

    # DeepSeek（优先用于编译级错误检查）
    deepseek_api_key: str = Field(default_factory=lambda: _env("DEEPSEEK_API_KEY", ""))
    deepseek_base_url: str = Field(default_factory=lambda: _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))
    deepseek_model: str = Field(default_factory=lambda: _env("DEEPSEEK_MODEL", "deepseek-chat"))

    # Greptile（强参考：优先用于最终报告整理）
    greptile_api_key: str = Field(default_factory=lambda: _env("GREPTILE_API_KEY", ""))
    # Greptile MCP HTTP endpoint (official example): https://api.greptile.com/mcp
    greptile_mcp_url: str = Field(default_factory=lambda: _env("GREPTILE_MCP_URL", "https://api.greptile.com/mcp"))
    # Backward compatibility (older experiments); not used by default.
    greptile_review_url: str = Field(default_factory=lambda: _env("GREPTILE_REVIEW_URL", ""))

    # MCP endpoints（为空则使用本地降级规则）
    mcp_compile_endpoint: str = Field(default_factory=lambda: _env("MCP_COMPILE_ENDPOINT", ""))
    mcp_static_endpoint: str = Field(default_factory=lambda: _env("MCP_STATIC_ENDPOINT", ""))
    mcp_dependency_endpoint: str = Field(default_factory=lambda: _env("MCP_DEPENDENCY_ENDPOINT", ""))
    mcp_security_endpoint: str = Field(default_factory=lambda: _env("MCP_SECURITY_ENDPOINT", ""))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

