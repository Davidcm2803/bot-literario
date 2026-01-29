import React, { useState, useEffect } from "react";
import { Bot, Search, Moon, Sun, LogIn, UserPlus, Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";

export const SideBar = () => {
  const [isDarkMode, setIsDarkMode] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  useEffect(() => {
    const storedTheme = localStorage.getItem("theme");
    if (storedTheme === "dark") {
      setIsDarkMode(true);
      document.documentElement.classList.add("dark");
    } else {
      setIsDarkMode(false);
      document.documentElement.classList.remove("dark");
    }
  }, []);

  useEffect(() => {
    // no deja hacer scroll si esta en responsive
    if (isSidebarOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isSidebarOpen]);

  const toggleTheme = () => {
    if (isDarkMode) {
      document.documentElement.classList.remove("dark");
      localStorage.setItem("theme", "light");
      setIsDarkMode(false);
    } else {
      document.documentElement.classList.add("dark");
      localStorage.setItem("theme", "dark");
      setIsDarkMode(true);
    }
  };

  const handleSearch = (e) => {
    e.preventDefault();
    console.log("Buscando:", searchQuery);
  };

  return (
    <>
      {/* Menu MCdonald */}
      <button
        onClick={() => setIsSidebarOpen(!isSidebarOpen)}
        className={cn(
          "fixed top-4 left-4 z-50 p-2 rounded-lg lg:hidden",
          "bg-card border border-border shadow-lg",
          "text-foreground hover:bg-background transition-colors"
        )}
        aria-label="Toggle menu"
      >
        {isSidebarOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
      </button>

      {/* Overlay*/}
      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed left-0 top-0 h-screen w-64 bg-card border-r border-border flex flex-col shadow-lg z-50",
          "transition-transform duration-300 ease-in-out",
          "lg:translate-x-0",
          isSidebarOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        )}
      >
        {/* Header  */}
        <div className="p-6 border-b border-border">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-primary/10 rounded-lg">
              <Bot className="w-8 h-8 text-primary" />
            </div>
            <h1 className="text-xl font-bold text-foreground">Bot Literario</h1>
          </div>
        </div>

        {/* Buscador */}
        <div className="p-4 border-b border-border">
          <form onSubmit={handleSearch} className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-foreground/50" />
            <input
              type="text"
              placeholder="Buscar libros..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
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
        <div className="flex-1">
          {/* ACA DEBERIA IR LOS PROMPT DE LOS CHATS RECIENTES */}
        </div>

        {/* Toggle de tema */}
        <div className="p-4 border-t border-border">
          <button
            onClick={toggleTheme}
            className={cn(
              "w-full flex items-center gap-3 p-3 rounded-lg",
              "transition-all duration-200",
              "hover:bg-background",
              "text-foreground/80 hover:text-foreground"
            )}
          >
            {isDarkMode ? (
              <>
                <Sun className="w-5 h-5 text-yellow-400" />
                <span className="text-sm font-medium">Modo Claro</span>
              </>
            ) : (
              <>
                <Moon className="w-5 h-5" />
                <span className="text-sm font-medium">Modo Oscuro</span>
              </>
            )}
          </button>
        </div>

        {/* Sección de Login in */}
        <div className="p-4 border-t border-border space-y-2">
          <button
            className={cn(
              "w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg",
              "bg-primary text-primary-foreground font-medium",
              "hover:opacity-90 transition-all duration-200",
              "focus:outline-none focus:ring-2 focus:ring-primary/50"
            )}
          >
            <LogIn className="w-4 h-4" />
            <span>Iniciar Sesión</span>
          </button>

          <button
            className={cn(
              "w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg",
              "bg-background border border-border text-foreground font-medium",
              "hover:bg-foreground/5 transition-all duration-200",
              "focus:outline-none focus:ring-2 focus:ring-primary/50"
            )}
          >
            <UserPlus className="w-4 h-4" />
            <span>Registrarse</span>
          </button>
        </div>
      </aside>
    </>
  );
};
