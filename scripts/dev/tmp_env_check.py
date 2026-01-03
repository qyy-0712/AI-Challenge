from app.config import Settings
s = Settings()
print("GITHUB_TOKEN_set=", bool(s.github_token), "len=", len(s.github_token or ""))
print("GLM_set=", bool(s.llm_api_key), "len=", len(s.llm_api_key or ""), "model=", s.llm_model)
print("DEEPSEEK_set=", bool(s.deepseek_api_key), "len=", len(s.deepseek_api_key or ""), "model=", s.deepseek_model)
print("GREPTILE_set=", bool(s.greptile_api_key), "len=", len(s.greptile_api_key or ""), "mcp_url=", s.greptile_mcp_url)
