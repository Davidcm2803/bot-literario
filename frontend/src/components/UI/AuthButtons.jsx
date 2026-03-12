import { LogIn, UserPlus } from "lucide-react";
import { cn } from "@/lib/utils";

export const AuthButtons = ({ isCollapsed }) => {
  return (
    <div className="p-4 border-t border-border space-y-2">
      <button
        className={cn(
          "w-full flex items-center gap-2 px-4 py-3 rounded-lg justify-center",
          "bg-primary text-primary-foreground font-medium",
          "hover:opacity-90 transition-all duration-200",
          "focus:outline-none focus:ring-2 focus:ring-primary/50"
        )}
        title={isCollapsed ? "Iniciar Sesión" : ""}
      >
        <LogIn className="w-4 h-4 flex-shrink-0" />
        {!isCollapsed && <span>Iniciar Sesión</span>}
      </button>

      <button
        className={cn(
          "w-full flex items-center gap-2 px-4 py-3 rounded-lg justify-center",
          "bg-background border border-border text-foreground font-medium",
          "hover:bg-foreground/5 transition-all duration-200",
          "focus:outline-none focus:ring-2 focus:ring-primary/50"
        )}
        title={isCollapsed ? "Registrarse" : ""}
      >
        <UserPlus className="w-4 h-4 flex-shrink-0" />
        {!isCollapsed && <span>Registrarse</span>}
      </button>
    </div>
  );
};