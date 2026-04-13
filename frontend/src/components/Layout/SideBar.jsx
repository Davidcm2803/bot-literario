import { useState, useEffect } from "react";
import {
  Bot,
  Menu,
  X,
  ChevronLeft,
  ChevronRight,
  Plus,
  MessageSquare,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { SearchBar } from "./SearchBar";
import { ThemeToggle } from "../UI/ThemeToggle";
import { AuthButtons } from "../UI/AuthButtons";

export const SideBar = ({
  conversations = [],
  activeConversation,
  onSelectConversation,
  onNewChat,
  onDeleteConversation,
  isCollapsed,
  onCollapse,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [hoveredId, setHoveredId] = useState(null);

  useEffect(() => {
    document.body.style.overflow = isSidebarOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [isSidebarOpen]);

  const filtered = conversations.filter((c) =>
    c.title.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  const handleDelete = (e, conv) => {
    e.stopPropagation();
    onDeleteConversation?.(conv);
  };

  return (
    <>
      <button
        onClick={() => setIsSidebarOpen(!isSidebarOpen)}
        className="fixed top-4 left-4 z-50 p-2 rounded-lg lg:hidden bg-card border border-border shadow-lg text-foreground hover:bg-background transition-colors"
      >
        {isSidebarOpen ? (
          <X className="w-6 h-6" />
        ) : (
          <Menu className="w-6 h-6" />
        )}
      </button>

      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <aside
        className={cn(
          "fixed left-0 top-0 h-screen bg-card border-r border-border flex flex-col shadow-lg z-50 overflow-hidden",
          "transition-all duration-300 ease-in-out lg:translate-x-0",
          isSidebarOpen
            ? "translate-x-0"
            : "-translate-x-full lg:translate-x-0",
          isCollapsed ? "w-16" : "w-64",
        )}
      >
        {/* Header */}
        <div className={cn(
          "border-b border-border flex items-center",
          isCollapsed ? "h-[100px] p-2" : "h-[73px] p-4",
        )}>
          {isCollapsed ? (
            <div className="flex flex-col items-center justify-center gap-1 w-full">
              <div className="p-2 bg-primary/10 rounded-lg">
                <Bot className="w-6 h-6 text-primary" />
              </div>
              <button
                onClick={() => onCollapse(!isCollapsed)}
                className="hidden lg:flex p-1 rounded-lg hover:bg-background transition-colors text-foreground/60 hover:text-foreground"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between w-full">
              <div className="flex items-center gap-2">
                <div className="p-2 bg-primary/10 rounded-lg">
                  <Bot className="w-6 h-6 text-primary" />
                </div>
                <h1 className="text-lg font-bold text-foreground truncate">
                  Bot Literario
                </h1>
              </div>
              <button
                onClick={() => onCollapse(!isCollapsed)}
                className="hidden lg:flex p-1.5 rounded-lg hover:bg-background transition-colors text-foreground/60 hover:text-foreground"
              >
                <ChevronLeft className="w-5 h-5" />
              </button>
            </div>
          )}
        </div>

        {/* Botón nuevo chat */}
        <div className="p-3 border-b border-border">
          <button
            onClick={onNewChat}
            className={cn(
              "w-full flex items-center gap-2 px-3 py-2 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary transition-colors font-medium",
              isCollapsed && "justify-center",
            )}
          >
            <Plus className="w-4 h-4 flex-shrink-0" />
            {!isCollapsed && <span>Nuevo chat</span>}
          </button>
        </div>

        <SearchBar
          query={searchQuery}
          onChange={setSearchQuery}
          onSubmit={(e) => e.preventDefault()}
          isCollapsed={isCollapsed}
        />

        {/* Lista de conversaciones */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden py-2 px-1">
          {filtered.length === 0 && !isCollapsed && (
            <p className="text-xs text-foreground/40 text-center mt-4 px-4">
              No hay conversaciones aún
            </p>
          )}
          {filtered.map((conv) => (
            <div
              key={conv.id}
              className="relative"
              onMouseEnter={() => setHoveredId(conv.id)}
              onMouseLeave={() => setHoveredId(null)}
            >
              <button
                onClick={() => onSelectConversation(conv)}
                className={cn(
                  "w-full flex items-center gap-2 px-3 py-2 rounded-lg transition-colors text-left overflow-hidden max-w-full",
                  activeConversation?.id === conv.id
                    ? "bg-primary/20 text-primary"
                    : "hover:bg-background text-foreground/70 hover:text-foreground",
                )}
              >
                <MessageSquare className="w-4 h-4 flex-shrink-0" />
                {!isCollapsed && (
                  <span
                    className={cn(
                      "text-sm truncate min-w-0 flex-1 transition-all",
                      hoveredId === conv.id ? "pr-6" : "",
                    )}
                  >
                    {conv.title}
                  </span>
                )}
              </button>
              {!isCollapsed && hoveredId === conv.id && (
                <button
                  onClick={(e) => handleDelete(e, conv)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-md text-foreground/40 hover:text-destructive hover:bg-destructive/10 transition-colors"
                  title="Eliminar conversación"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          ))}
        </div>

        <ThemeToggle isCollapsed={isCollapsed} inline />
        <AuthButtons isCollapsed={isCollapsed} />
      </aside>
    </>
  );
};