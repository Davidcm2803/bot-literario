import { Search } from "lucide-react";
import { cn } from "@/lib/utils";

export const SearchBar = ({ query, onChange, onSubmit, isCollapsed }) => {
  if (isCollapsed) {
    return (
      <div className="p-4 border-b border-border flex justify-center">
        <button
          className="p-2 rounded-lg hover:bg-background transition-colors text-foreground/60 hover:text-foreground"
          aria-label="Buscar"
        >
          <Search className="w-5 h-5" />
        </button>
      </div>
    );
  }

  return (
    <div className="p-4 border-b border-border">
      <form onSubmit={onSubmit} className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-foreground/50" />
        <input
          type="text"
          placeholder="Buscar libros..."
          value={query}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            "w-full pl-10 pr-4 py-2 rounded-lg",
            "bg-background border border-border",
            "text-foreground placeholder:text-foreground/50",
            "focus:outline-none focus:ring-2 focus:ring-primary/50",
            "transition-all duration-200"
          )}
        />
      </form>
    </div>
  );
};