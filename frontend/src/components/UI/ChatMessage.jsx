import { BookOpen } from "lucide-react";

export const ChatMessage = ({ role, content, error }) => {
  const isUser = role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-card border border-border flex items-center justify-center mr-2 shrink-0 mt-1">
          <BookOpen className="w-4 h-4 text-primary" />
        </div>
      )}
      <div
        className={`max-w-[75%] px-4 py-3 rounded-2xl text-sm leading-relaxed break-words ${
          isUser
            ? "bg-primary text-primary-foreground rounded-br-sm"
            : error
            ? "bg-red-500/10 border border-red-400/30 text-foreground rounded-bl-sm"
            : "bg-card border border-border text-foreground rounded-bl-sm"
        }`}
      >
        {content}
      </div>
    </div>
  );
};