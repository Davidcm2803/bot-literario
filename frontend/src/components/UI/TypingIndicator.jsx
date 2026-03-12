import { BookOpen } from "lucide-react";

export const TypingIndicator = () => {
  return (
    <div className="flex justify-start">
      <div className="w-8 h-8 rounded-full bg-card border border-border flex items-center justify-center mr-2 shrink-0">
        <BookOpen className="w-4 h-4 text-primary" />
      </div>
      <div className="bg-card border border-border px-4 py-3 rounded-2xl rounded-bl-sm">
        <div className="flex gap-1 items-center h-4">
          <span className="w-2 h-2 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
          <span className="w-2 h-2 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
          <span className="w-2 h-2 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
        </div>
      </div>
    </div>
  );
};