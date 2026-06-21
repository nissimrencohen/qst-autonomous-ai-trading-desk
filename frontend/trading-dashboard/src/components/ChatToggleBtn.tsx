/**
 * ChatToggleBtn — fixed floating button to open/close the assistant sidebar.
 *
 * Sits in the bottom-right corner of the viewport, visible on ALL views.
 * Pulses with amber glow when the drawer is closed and a new message arrives.
 */
interface Props {
  isOpen: boolean;
  hasNewMsg: boolean;
  onClick: () => void;
}

export function ChatToggleBtn({ isOpen, hasNewMsg, onClick }: Props) {
  if (isOpen) return null;
  return (
    <button
      id="chat-toggle-btn"
      className={`chat-toggle-btn ${isOpen ? "chat-toggle-btn--open" : ""} ${
        hasNewMsg && !isOpen ? "chat-toggle-btn--pulse" : ""
      }`}
      onClick={onClick}
      aria-label={isOpen ? "Close assistant" : "Open QST Intelligence Assistant"}
      title={isOpen ? "Close assistant" : "Open assistant (QST Intelligence)"}
    >
      {isOpen ? (
        <span className="chat-toggle-btn__icon">✕</span>
      ) : (
        <>
          <span className="chat-toggle-btn__icon">◈</span>
          <span className="chat-toggle-btn__label">ASSISTANT</span>
          {hasNewMsg && <span className="chat-toggle-btn__badge" aria-label="New message" />}
        </>
      )}
    </button>
  );
}
