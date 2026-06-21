import { useState, useRef } from "react";
import { ingestOmnibus } from "../api";
import { TICKERS } from "../types";

const ALL_TICKERS = [...TICKERS] as string[];

function StatusBanner({
  status,
}: {
  status: { type: "success" | "error" | "info"; msg: string } | null;
}) {
  if (!status) return null;
  const colors: Record<string, string> = {
    success: "var(--bull)",
    error: "var(--bear)",
    info: "var(--amber)",
  };
  const icons: Record<string, string> = { success: "✓", error: "✕", info: "●" };
  return (
    <div
      style={{
        border: `1px solid ${colors[status.type]}`,
        borderLeft: `3px solid ${colors[status.type]}`,
        padding: "10px 14px",
        margin: "12px 0",
        fontSize: 12,
        color: colors[status.type],
        fontFamily: "var(--font-mono)",
        animation: "rise 0.3s ease both",
      }}
    >
      {icons[status.type]} {status.msg}
    </div>
  );
}

export function ManualUploadPanel() {
  const [ticker, setTicker] = useState(ALL_TICKERS[0]);
  const [source, setSource] = useState("");
  const [pubDate, setPubDate] = useState(new Date().toISOString().slice(0, 10));
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<{ type: "success" | "error" | "info"; msg: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setFiles(Array.from(e.target.files));
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (e.dataTransfer.files) {
      setFiles(Array.from(e.dataTransfer.files));
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() && files.length === 0) {
      setStatus({ type: "error", msg: "Please provide either files or text content." });
      return;
    }
    setLoading(true);
    setStatus({ type: "info", msg: "Ingesting to OMNI-UPLOAD Pipeline…" });
    
    const formData = new FormData();
    formData.append("ticker", ticker);
    formData.append("source", source || "Manual upload");
    formData.append("published_at", pubDate);
    if (text.trim()) {
      formData.append("text", text);
    }
    for (const f of files) {
      formData.append("files", f);
    }

    const res = await ingestOmnibus(formData);
    setLoading(false);
    
    if (res.success) {
      setStatus({
        type: "success",
        msg: `Ingested. Corpus now holds ${res.total_documents ?? "?"} documents.`,
      });
      setText("");
      setSource("");
      setFiles([]);
      if (fileRef.current) fileRef.current.value = "";
    } else {
      setStatus({ type: "error", msg: `Ingest failed: ${res.error}` });
    }
  };

  return (
    <div style={{ padding: "4px 0" }}>
      <div style={{
        fontFamily: "var(--font-display)",
        fontSize: 14,
        fontWeight: 600,
        letterSpacing: "0.1em",
        color: "var(--amber)",
        marginBottom: 16,
        borderBottom: "1px solid var(--line-bright)",
        paddingBottom: 8,
      }}>
        OMNI-UPLOAD PIPELINE
      </div>

      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <StatusBanner status={status} />

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
          <label className="field">
            <span className="field__label">Ticker</span>
            <select
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              style={{
                width: "100%", background: "#0b1016", color: "var(--ink)",
                border: "1px solid var(--line-bright)", fontFamily: "var(--font-mono)",
                fontSize: 13, padding: "8px 10px",
              }}
            >
              {ALL_TICKERS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Source</span>
            <input
              type="text"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="e.g. Q1-2026 PDF"
              style={{
                width: "100%", background: "#0b1016", color: "var(--ink)",
                border: "1px solid var(--line-bright)", fontFamily: "var(--font-mono)",
                fontSize: 13, padding: "8px 10px",
              }}
            />
          </label>
          <label className="field">
            <span className="field__label">Published</span>
            <input
              type="date"
              value={pubDate}
              onChange={(e) => setPubDate(e.target.value)}
              style={{
                width: "100%", background: "#0b1016", color: "var(--ink)",
                border: "1px solid var(--line-bright)", fontFamily: "var(--font-mono)",
                fontSize: 13, padding: "8px 10px",
              }}
            />
          </label>
        </div>

        {/* Drop zone */}
        <div className="field">
          <span className="field__label">Documents & Charts</span>
          <div
            onClick={() => fileRef.current?.click()}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            style={{
              border: "1px dashed var(--line-bright)",
              borderRadius: 3,
              padding: "32px 20px",
              textAlign: "center",
              cursor: "pointer",
              transition: "border-color 0.15s",
              background: "rgba(255,180,84,0.02)",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--amber-deep)")}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--line-bright)")}
          >
            <div style={{ fontSize: 24, color: "var(--amber-deep)", marginBottom: 8 }}>⊕</div>
            <div style={{ fontSize: 12, color: "var(--ink)" }}>
              {files.length > 0 
                ? `${files.length} file(s) selected` 
                : "Drag & drop files or click to browse (.pdf, .txt, .csv, .png, .jpg)"}
            </div>
            {files.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--bull)", marginTop: 6 }}>
                {files.map(f => f.name).join(", ")}
              </div>
            )}
            <input
              ref={fileRef}
              type="file"
              multiple
              accept=".pdf,.txt,.csv,.md,.json,.png,.jpg,.jpeg,.webp"
              onChange={handleFileChange}
              onClick={(e) => e.stopPropagation()}
              style={{ display: "none" }}
            />
          </div>
        </div>

        <label className="field">
          <span className="field__label">Fallback Text Snippet</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste supplementary text here if needed…"
            rows={4}
            style={{
              width: "100%", background: "#0b1016", color: "var(--ink)",
              border: "1px solid var(--line-bright)", fontFamily: "var(--font-mono)",
              fontSize: 12, padding: "10px", resize: "vertical",
            }}
          />
        </label>

        <button
          type="submit"
          disabled={loading}
          className="submit"
          style={{ fontSize: 14, padding: "10px", marginTop: "10px" }}
        >
          {loading ? "INGESTING…" : "🚀 INGEST TO RAG"}
        </button>
      </form>
    </div>
  );
}
