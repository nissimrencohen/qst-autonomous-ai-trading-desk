/**
 * ChatSidebar — persistent, globally-accessible conversational assistant drawer.
 *
 * Design: phosphor-amber terminal aesthetic matching QST. Glass-morphism
 * panel slides in from the right. Streaming SSE responses displayed token-by-token.
 *
 * Features:
 *  • Streaming chat responses via SSE (token-by-token rendering)
 *  • Session persistence via backend SQLite (session_id stored in localStorage)
 *  • "New Conversation" button to clear and start fresh
 *  • Typing indicator while streaming
 *  • Strict domain-grounding via backend system prompt (quant trading only)
 *  • Accessible via fixed floating button from any view
 */
import { useEffect, useRef, useState, useCallback } from "react";
import type { ChatMessage, ChatSession } from "../types";
import { sendChatMessage, loadChatHistory, listChatSessions } from "../api";

const SESSION_KEY = "desk01_chat_session_id";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  hasNewMsg: boolean;
  clearNewMsg: () => void;
}

function formatTime(iso?: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function ChatSidebar({ isOpen, onClose, clearNewMsg }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(
    () => localStorage.getItem(SESSION_KEY)
  );
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");
  const [modelUsed, setModelUsed] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, streamingContent]);

  // Fetch session history when sidebar opens
  useEffect(() => {
    if (isOpen) {
      listChatSessions().then(setSessions).catch(() => {});
    }
  }, [isOpen]);

  // Focus input when drawer opens
  useEffect(() => {
    if (isOpen) {
      clearNewMsg();
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [isOpen, clearNewMsg]);

  // Load history on mount (if we have a session)
  useEffect(() => {
    if (sessionId) {
      loadChatHistory(sessionId).then((history) => {
        if (history.length > 0) {
          setMessages(history.filter((m) => m.role !== "system"));
        }
      });
    }
  }, [sessionId]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    setError(null);
    const userMsg: ChatMessage = {
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setInput("");
    setIsStreaming(true);
    setStreamingContent("");

    await sendChatMessage({
      messages: nextMessages.map((m) => ({ role: m.role, content: m.content })),
      sessionId,
      onDelta: (chunk) => {
        setStreamingContent((prev) => prev + chunk);
      },
      onDone: (sid, model, finalContent) => {
        setIsStreaming(false);
        setModelUsed(model);
        if (sid) {
          setSessionId(sid);
          localStorage.setItem(SESSION_KEY, sid);
        }
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant" as const,
            content: finalContent || streamingContentRef.current,
            created_at: new Date().toISOString(),
            model_used: model,
          },
        ]);
        setStreamingContent("");
      },
      onError: (msg) => {
        setIsStreaming(false);
        setStreamingContent("");
        setError(msg);
      },
    });
  }, [input, isStreaming, messages, sessionId]);

  // Ref to capture latest streamingContent in the onDone closure
  const streamingContentRef = useRef("");
  useEffect(() => {
    streamingContentRef.current = streamingContent;
  }, [streamingContent]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleNewConversation = () => {
    setMessages([]);
    setSessionId(null);
    setStreamingContent("");
    setError(null);
    setModelUsed("");
    localStorage.removeItem(SESSION_KEY);
    setTimeout(() => inputRef.current?.focus(), 100);
  };

  return (
    <>
      {/* Backdrop */}
      {isOpen && (
        <div
          className="chat-backdrop"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      {/* Drawer */}
      <aside
        id="chat-sidebar"
        className={`chat-sidebar ${isOpen ? "chat-sidebar--open" : ""}`}
        aria-label="QST Intelligence Assistant"
        role="complementary"
      >
        {/* Header */}
        <div className="chat-sidebar__header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
          <div className="chat-sidebar__header-left" style={{ flex: 1, display: "flex", justifyContent: "flex-start" }}>
            {sessions.length > 0 && (
              <select
                className="chat-sidebar__session-select"
                value={sessionId || ""}
                onChange={(e) => {
                  const sid = e.target.value;
                  if (!sid) return;
                  setSessionId(sid);
                  localStorage.setItem(SESSION_KEY, sid);
                }}
                aria-label="Chat History"
              >
                <option value="" disabled>History</option>
                {sessions.map((s) => (
                  <option key={s.session_id} value={s.session_id}>
                    {s.title || `Session ${s.created_at.substring(5, 10)}`}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="chat-sidebar__header-center" style={{ flex: 1, display: "flex", justifyContent: "center" }}>
            {modelUsed && (
              <span className="chat-sidebar__model mono">
                {modelUsed.split("/").pop()}
              </span>
            )}
          </div>
          <div className="chat-sidebar__header-right" style={{ flex: 1, display: "flex", justifyContent: "flex-end", gap: "8px" }}>
            <button
              className="chat-sidebar__new-btn"
              onClick={handleNewConversation}
              title="New conversation"
              id="chat-new-btn"
            >
              ＋ NEW
            </button>
            <button
              className="chat-sidebar__close"
              onClick={onClose}
              title="Close assistant"
              id="chat-close-btn"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Domain label */}
        <div className="chat-sidebar__domain mono">
          Quantitative Trading Domain · Cloud-Routed via LiteLLM
        </div>

        {/* Messages */}
        <div className="chat-sidebar__messages" ref={scrollRef}>
          {messages.length === 0 && !isStreaming && (
            <div className="chat-empty">
              <div className="chat-empty__glyph">◈</div>
              <p className="chat-empty__text">
                Ask about market signals, risk management, portfolio strategy,
                VIX regimes, or any of the 10 desk instruments.
              </p>
              <div className="chat-empty__hints">
                <button className="chat-hint" onClick={() => setInput("What is the current VIX regime telling us?")}>
                  What is the current VIX regime telling us?
                </button>
                <button className="chat-hint" onClick={() => setInput("Compare NVDA and AAPL technical setups")}>
                  Compare NVDA and AAPL technical setups
                </button>
                <button className="chat-hint" onClick={() => setInput("Explain contango in volatility ETFs like VIXY")}>
                  Explain contango in VIXY/SVXY
                </button>
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div
              key={i}
              className={`chat-msg chat-msg--${msg.role}`}
            >
              <div className="chat-msg__meta">
                <span className="chat-msg__role mono">
                  {msg.role === "user" ? "YOU" : "QST"}
                </span>
                {msg.created_at && (
                  <span className="chat-msg__time mono">
                    {formatTime(msg.created_at)}
                  </span>
                )}
              </div>
              <div className="chat-msg__content">
                {msg.content.split("\n").map((line, j) => (
                  <span key={j}>
                    {line}
                    {j < msg.content.split("\n").length - 1 && <br />}
                  </span>
                ))}
              </div>
            </div>
          ))}

          {/* Streaming in-progress */}
          {isStreaming && (
            <div className="chat-msg chat-msg--assistant">
              <div className="chat-msg__meta">
                <span className="chat-msg__role mono">QST</span>
              </div>
              <div className="chat-msg__content">
                {streamingContent || (
                  <span className="chat-typing mono">
                    ● analyzing<span className="chat-typing__dots" />
                  </span>
                )}
                {streamingContent && (
                  <span className="chat-cursor" aria-hidden="true">▌</span>
                )}
              </div>
            </div>
          )}

          {error && (
            <div className="chat-error mono">
              ✕ {error}
            </div>
          )}
        </div>

        {/* Input area */}
        <div className="chat-sidebar__input-area">
          <textarea
            ref={inputRef}
            id="chat-input"
            className="chat-input mono"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about markets, signals, risk… (Enter to send)"
            rows={3}
            disabled={isStreaming}
            aria-label="Chat message input"
          />
          <div className="chat-sidebar__input-footer">
            <span className="chat-sidebar__hint mono">
              Shift+Enter for newline
            </span>
            <button
              id="chat-send-btn"
              className="chat-send"
              onClick={handleSend}
              disabled={isStreaming || !input.trim()}
            >
              SEND ▶
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
