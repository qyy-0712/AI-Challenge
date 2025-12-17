import React, { useState } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const gradients = [
  "linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%)",
  "linear-gradient(135deg, #0ea5e9 0%, #6366f1 50%, #22c55e 100%)",
  "linear-gradient(135deg, #111827 0%, #1f2937 50%, #0f172a 100%)",
];

export default function App() {
  const [prUrl, setPrUrl] = useState("");
  const [requirements, setRequirements] = useState("");
  const [report, setReport] = useState("");
  const [reviewId, setReviewId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [bgIdx, setBgIdx] = useState(0);

  const client = axios.create({
    baseURL: API_BASE,
    // 使用后端内置 Token
  });

  const parsePrUrl = (url) => {
    // 支持 https://github.com/owner/repo/pull/123 或带 query 的形式
    const match = url.match(/github\.com\/(.+?)\/(.+?)\/pull\/(\d+)/);
    if (!match) return null;
    return { repo_full_name: `${match[1]}/${match[2]}`, pr_number: Number(match[3]) };
  };

  const runReview = async () => {
    setError("");
    setReport("");
    setReviewId("");
    const parsed = parsePrUrl(prUrl.trim());
    if (!parsed) {
      setError("PR 链接格式不正确，请输入类似：https://github.com/owner/repo/pull/123");
      return;
    }
    setLoading(true);
    try {
      const res = await client.post("/review", {
        ...parsed,
        requirements,
      });
      setReport(res.data.report_markdown);
      setReviewId(res.data.review_id || "");
    } catch (e) {
      setError(e.response?.data?.detail || e.message || "审查失败");
    } finally {
      setLoading(false);
    }
  };

  const downloadReport = () => {
    if (!reviewId) return;
    window.open(`${API_BASE}/review/${reviewId}/export`, "_blank", "noreferrer");
  };

  const switchTheme = () => {
    setBgIdx((prev) => (prev + 1) % gradients.length);
  };

  return (
    <div className="page" style={{ backgroundImage: gradients[bgIdx] }}>
      <div className="aura aura-1" />
      <div className="aura aura-2" />
      <div className="glass">
        <header>
          <div>
            <h1>GitHub PR AI 审查</h1>
            <p className="sub">输入 PR 链接，生成分层、可执行的低噪音审查报告。</p>
          </div>
          <div className="actions">
            <button className="ghost" onClick={switchTheme}>
              切换主题
            </button>
          </div>
        </header>

        <div className="grid">
          <div className="panel">
            <div className="card">
              <label>PR 链接</label>
              <input
                placeholder="例如：https://github.com/owner/repo/pull/123"
                value={prUrl}
                onChange={(e) => setPrUrl(e.target.value)}
              />
            </div>

            <div className="card">
              <label>可选需求 / 验收要点</label>
              <textarea
                placeholder="补充业务需求、验收标准、边界条件（可选）"
                value={requirements}
                onChange={(e) => setRequirements(e.target.value)}
              />
            </div>

            <div className="card row">
              <button className="primary" onClick={runReview} disabled={loading}>
                {loading ? "分析中..." : "开始审查"}
              </button>
              {error && <div className="error">{error}</div>}
            </div>
          </div>

          <div className="panel report-panel">
            <div className="report-header">
              <h3>审查报告</h3>
              <span className="pill">AI + MCP</span>
            </div>
            <span className="hint">Compile · Static · Risk · Architecture · Security · Maintainability</span>
            <div style={{ marginTop: 10, marginBottom: 10 }}>
              <button className="ghost" onClick={downloadReport} disabled={!reviewId}>
                导出 Markdown
              </button>
              {reviewId && (
                <span className="hint" style={{ marginLeft: 10 }}>
                  ID: {reviewId}
                </span>
              )}
            </div>
            <div className="report-shell">
              <pre className="report">{report || "暂无结果"}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

