import { Send } from "lucide-react";
import { useEffect, useRef } from "react";

export const ChatInput = ({ message, onChange, onSubmit, loading }) => {
  const textareaRef = useRef(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = "auto";
      textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
    }
  }, [message]);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit(message);
    }
  };

  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(message); }}>
      <div className="bg-card backdrop-blur-md rounded-2xl shadow-2xl border-2 border-border overflow-hidden">
        <textarea
          ref={textareaRef}
          value={message}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Pregúntame sobre cualquier obra literaria..."
          className="w-full px-6 py-5 placeholder-foreground/40 bg-transparent resize-none focus:outline-none text-base overflow-y-auto"
          rows="1"
        />
        <div className="flex items-center justify-between px-6 py-3 bg-background border-t border-border">
          <div className="flex items-center gap-2 text-xs text-foreground/40">
            <kbd className="px-2 py-1 bg-card rounded border border-border font-mono">Enter</kbd>
            <span>para enviar</span>
          </div>
          <button
            type="submit"
            disabled={!message.trim() || loading}
            className="bg-primary text-primary-foreground px-5 py-2.5 rounded-xl font-medium hover:opacity-90 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {loading ? "Enviando..." : "Enviar"}
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </form>
  );
};