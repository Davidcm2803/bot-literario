import { useState } from "react";
import { createPortal } from "react-dom";
import { X, BookOpen, Mail, Lock, User, ArrowRight, Loader2 } from "lucide-react";
import { cn } from "../../lib/utils";
import {
  signInWithGoogle,
  signInWithMicrosoft,
  signInWithEmail,
  registerWithEmail,
} from "../../lib/firebase";

const GoogleIcon = () => (
  <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none">
    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
  </svg>
);

const MicrosoftIcon = () => (
  <svg viewBox="0 0 24 24" className="w-5 h-5">
    <path d="M11.4 2H2v9.4h9.4V2z" fill="#F25022"/>
    <path d="M22 2h-9.4v9.4H22V2z" fill="#7FBA00"/>
    <path d="M11.4 12.6H2V22h9.4v-9.4z" fill="#00A4EF"/>
    <path d="M22 12.6h-9.4V22H22v-9.4z" fill="#FFB900"/>
  </svg>
);


const Field = ({ icon: Icon, ...props }) => (
  <div className="relative">
    <Icon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-foreground/30" />
    <input
      className={cn(
        "w-full pl-10 pr-4 py-2.5 rounded-lg text-sm",
        "bg-background border border-border",
        "text-foreground placeholder:text-foreground/30",
        "focus:outline-none focus:border-primary transition-colors",
      )}
      {...props}
    />
  </div>
);

const ProviderBtn = ({ icon, label, onClick, loading }) => (
  <button
    onClick={onClick}
    disabled={loading}
    className={cn(
      "w-full flex items-center justify-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium",
      "border border-border bg-card hover:bg-background",
      "text-foreground transition-colors",
      "disabled:opacity-50 disabled:cursor-not-allowed",
    )}
  >
    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : icon}
    {label}
  </button>
);

const Divider = () => (
  <div className="flex items-center gap-3 my-4">
    <div className="flex-1 h-px bg-border" />
    <span className="text-xs text-foreground/30">o continúa con</span>
    <div className="flex-1 h-px bg-border" />
  </div>
);


export const AuthModal = ({ onClose, onSuccess }) => {
  const [mode, setMode]       = useState("login");
  const [form, setForm]       = useState({ username: "", email: "", password: "" });
  const [error, setError]     = useState("");
  const [loading, setLoading] = useState(null);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const handleProvider = async (provider) => {
    setError("");
    setLoading(provider);
    try {
      const fn = provider === "google" ? signInWithGoogle : signInWithMicrosoft;
      const user = await fn();
      onSuccess?.(user);
      onClose?.();
    } catch (e) {
      setError(e.message || "Error al iniciar sesión.");
    } finally {
      setLoading(null);
    }
  };

  const handleEmail = async () => {
    setError("");
    setLoading("email");
    try {
      const fn   = mode === "login" ? signInWithEmail : registerWithEmail;
      const args = mode === "login"
        ? [form.email, form.password]
        : [form.username, form.email, form.password];
      const user = await fn(...args);
      onSuccess?.(user);
      onClose?.();
    } catch (e) {
      setError(e.message || "Error.");
    } finally {
      setLoading(null);
    }
  };

  const switchMode = () => {
    setMode((m) => (m === "login" ? "register" : "login"));
    setError("");
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={(e) => e.target === e.currentTarget && onClose?.()}
    >
      <div className="relative w-full max-w-sm bg-card border border-border rounded-2xl shadow-2xl overflow-hidden">
        <div className="h-1 w-full bg-primary" />

        <div className="p-6">
          <button
            onClick={onClose}
            className="absolute top-4 right-4 p-1.5 rounded-lg text-foreground/40 hover:text-foreground hover:bg-background transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
          <div className="flex items-center gap-2.5 mb-6">
            <div className="p-2 bg-primary/10 rounded-lg">
              <BookOpen className="w-5 h-5 text-primary" />
            </div>
            <div>
              <h2 className="font-bold text-foreground leading-tight">
                {mode === "login" ? "Bienvenido de vuelta" : "Crear cuenta"}
              </h2>
              <p className="text-xs text-foreground/40">Scriptia · Bot Literario</p>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <ProviderBtn
              icon={<GoogleIcon />}
              label="Continuar con Google"
              onClick={() => handleProvider("google")}
              loading={loading === "google"}
            />
            <ProviderBtn
              icon={<MicrosoftIcon />}
              label="Continuar con Microsoft"
              onClick={() => handleProvider("microsoft")}
              loading={loading === "microsoft"}
            />
          </div>
          <Divider />
          <div className="flex flex-col gap-3">
            {mode === "register" && (
              <Field
                icon={User}
                type="text"
                placeholder="Nombre de usuario"
                value={form.username}
                onChange={set("username")}
              />
            )}
            <Field
              icon={Mail}
              type="email"
              placeholder="Correo electrónico"
              value={form.email}
              onChange={set("email")}
            />
            <Field
              icon={Lock}
              type="password"
              placeholder="Contraseña"
              value={form.password}
              onChange={set("password")}
              onKeyDown={(e) => e.key === "Enter" && handleEmail()}
            />

            {error && (
              <p className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            <button
              onClick={handleEmail}
              disabled={!!loading}
              className={cn(
                "w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium",
                "bg-primary text-primary-foreground",
                "hover:opacity-90 transition-opacity",
                "disabled:opacity-50 disabled:cursor-not-allowed",
              )}
            >
              {loading === "email" ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  {mode === "login" ? "Iniciar sesión" : "Registrarme"}
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </div>
          <p className="text-center text-xs text-foreground/40 mt-4">
            {mode === "login" ? "¿No tienes cuenta?" : "¿Ya tienes cuenta?"}{" "}
            <button
              onClick={switchMode}
              className="text-primary hover:underline font-medium"
            >
              {mode === "login" ? "Regístrate" : "Inicia sesión"}
            </button>
          </p>
        </div>
      </div>
    </div>,
    document.body
  );
};