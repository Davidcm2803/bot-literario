import { useState } from "react";
import { LogIn, LogOut, User } from "lucide-react";
import { cn } from "../../lib/utils";
import { logout } from "../../lib/firebase";
import { useAuth } from "../../hooks/useAuth";
import { AuthModal } from "./AuthModal";

export const AuthButtons = ({ isCollapsed }) => {
  const { user, setUser } = useAuth();
  const [showModal, setShowModal] = useState(false);

  const handleLogout = async () => {
    await logout();
    setUser(null);
  };

  if (user) {
    return (
      <div className={cn(
        "border-t border-border p-3 flex items-center gap-2",
        isCollapsed && "justify-center",
      )}>
        {user.photo ? (
          <img
            src={user.photo}
            alt={user.username}
            className="w-8 h-8 rounded-full flex-shrink-0 object-cover border border-border"
          />
        ) : (
          <div className="w-8 h-8 rounded-full flex-shrink-0 bg-primary/20 flex items-center justify-center">
            <User className="w-4 h-4 text-primary" />
          </div>
        )}

        {!isCollapsed && (
          <>
            <span className="text-sm text-foreground truncate flex-1 min-w-0">
              {user.username}
            </span>
            <button
              onClick={handleLogout}
              className="p-1.5 rounded-lg text-foreground/40 hover:text-foreground hover:bg-background transition-colors flex-shrink-0"
              title="Cerrar sesión"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </>
        )}
      </div>
    );
  }

  return (
    <>
      <div className={cn(
        "border-t border-border p-3",
        isCollapsed ? "flex justify-center" : "",
      )}>
        <button
          onClick={() => setShowModal(true)}
          className={cn(
            "flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium w-full",
            "text-foreground/60 hover:text-foreground hover:bg-background transition-colors",
            isCollapsed && "justify-center",
          )}
        >
          <LogIn className="w-4 h-4 flex-shrink-0" />
          {!isCollapsed && <span>Iniciar sesión</span>}
        </button>
      </div>

      {showModal && (
        <AuthModal
          onClose={() => setShowModal(false)}
          onSuccess={(user) => setUser(user)}
        />
      )}
    </>
  );
};