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
  const [showInputs, setShowInputs] = useState(true);

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
    if (loading) return;
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
      setShowInputs(false);
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

  const renderReportRich = (text) => {
    const lines = String(text || "").split(/\r?\n/);
    return (
      <div className="report-rich">
        {lines.map((line, idx) => {
          const raw = String(line || "");
          const display = raw.trimEnd();
          const m = raw.trim();
          if (!m) return <div key={idx} className="r-blank" />;

          // Section titles like: "一、基本信息"
          const secMatch = m.match(/^(一|二|三|四|五|六|七|八|九|十)、.+$/);
          if (secMatch) {
            return (
              <div key={idx} className="r-section">
                {m}
              </div>
            );
          }

          // Issue title like: "1. 语法错误"
          const issueMatch = m.match(/^(\d+)\.\s+(.+)$/);
          if (issueMatch) {
            const title = issueMatch[2] || "";
            const levelClass =
              title.includes("语法错误") ? "sev-critical" :
              title.includes("类型错误") ? "sev-high" :
              title.includes("编译错误") ? "sev-high" :
              title.includes("缺少依赖") ? "sev-medium" :
              "sev-medium";
            return (
              <div key={idx} className={`r-issue ${levelClass}`}>
                <span className="r-issue-num">{issueMatch[1]}.</span>
                <span className="r-issue-title">{title}</span>
              </div>
            );
          }

          // Sub bullets like "- xxx" or "   - xxx"
          const bulletMatch = m.match(/^-+\s+(.*)$/);
          if (bulletMatch) {
            return (
              <div key={idx} className="r-bullet">
                <span className="r-dot" />
                <span className="r-bullet-text">{bulletMatch[1]}</span>
              </div>
            );
          }

          // Meta lines like "- 位置: xx" (may be indented in raw)
          const metaMatch = m.match(/^-\s*(位置|原始类型|错误信息|原因|建议|风险级别|来源)\s*:\s*(.*)$/);
          if (metaMatch) {
            return (
              <div key={idx} className="r-meta">
                <span className="r-meta-k">{metaMatch[1]}：</span>
                <span className="r-meta-v">{metaMatch[2]}</span>
              </div>
            );
          }

          // Default
          return (
            <div key={idx} className="r-line">
              {display}
            </div>
          );
        })}
      </div>
    );
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
            <button className="ghost" onClick={() => setShowInputs((v) => !v)} disabled={loading}>
              {showInputs ? "隐藏输入" : "显示输入"}
            </button>
            <button className="ghost" onClick={switchTheme} disabled={loading}>
              切换主题
            </button>
          </div>
        </header>

        <div className={`grid ${showInputs ? "" : "grid-focus-report"}`}>
          <div className={`panel ${showInputs ? "" : "panel-hidden"}`}>
            <div className="card">
              <label>PR 链接</label>
              <input
                placeholder="例如：https://github.com/owner/repo/pull/123"
                value={prUrl}
                onChange={(e) => setPrUrl(e.target.value)}
                disabled={loading}
              />
            </div>

            <div className="card">
              <label>可选需求 / 验收要点</label>
              <textarea
                placeholder="补充业务需求、验收标准、边界条件（可选）"
                value={requirements}
                onChange={(e) => setRequirements(e.target.value)}
                disabled={loading}
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
              {reviewId ? <span className="pill">已生成</span> : <span className="pill">等待生成</span>}
            </div>
            <div style={{ marginTop: 10, marginBottom: 10 }}>
              <button className="ghost" onClick={downloadReport} disabled={!reviewId}>
                导出报告
              </button>
              {reviewId && (
                <span className="hint" style={{ marginLeft: 10 }}>
                  ID: {reviewId}
                </span>
              )}
            </div>
            <div className="report-shell">
              {report ? renderReportRich(report) : <div className="report-empty">暂无结果</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

