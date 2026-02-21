import React, { useState, useEffect, useRef } from "react";
import { BookOpen, Sparkles, Send } from "lucide-react";

export const Hero = () => {
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [serverStatus, setServerStatus] = useState("checking");
  const [selectedQuestions, setSelectedQuestions] = useState([]);
  const bottomRef = useRef(null);

  const allQuestions = [
    "¿Quién escribió Don Quijote?",
    "¿De qué trata Moby Dick?",
    "¿Qué es el realismo mágico?",
    "¿Quién fue Edgar Allan Poe?",
    "¿Qué obras escribió Shakespeare?",
  ];

  useEffect(() => {
    const shuffled = [...allQuestions].sort(() => Math.random() - 0.5);
    setSelectedQuestions(shuffled.slice(0, 3));
  }, []);

  useEffect(() => {
    const checkServer = async () => {
      try {
        const res = await fetch("http://localhost:8090/");
        setServerStatus(res.ok ? "online" : "offline");
      } catch {
        setServerStatus("offline");
      }
    };
    checkServer();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleQuestionClick = (question) => {
    setMessage(question);
  };

  const sendMessage = async (text) => {
    if (!text.trim()) return;

    const userMessage = { role: "user", content: text };
    setMessages((prev) => [...prev, userMessage]);
    setMessage("");
    setLoading(true);

    try {
      const res = await fetch(
        `http://localhost:8090/ask?q=${encodeURIComponent(text)}`
      );
      const data = await res.json();
      console.log("Respuesta backend:", data);

      // Quemado de momento, reemplazar con respuesta real del RAG
      const botMessage = {
        role: "bot",
        content: "Conexión exitosa con el Backend",
      };
      setMessages((prev) => [...prev, botMessage]);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        { role: "bot", content: "❌ No se pudo conectar con el servidor.", error: true },
      ]);
    }

    setLoading(false);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    sendMessage(message);
  };

  const chatStarted = messages.length > 0;

  return (
    <div className="min-h-screen bg-background flex flex-col">

      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-background/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <div className="bg-card p-2 rounded-full border border-border">
            <BookOpen className="w-5 h-5 text-primary" />
          </div>
          <div>
            <h1 className="font-serif text-xl">Biblio IA</h1>
            <p className="text-xs text-foreground/50">Tu bot literario inteligente</p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-full border border-border bg-card">
          <span className={`w-2 h-2 rounded-full ${
            serverStatus === "checking" ? "bg-yellow-400 animate-pulse"
            : serverStatus === "online" ? "bg-green-400"
            : "bg-red-400"
          }`} />
          <span>
            {serverStatus === "checking" ? "Verificando..."
            : serverStatus === "online" ? "Conectado"
            : "Sin conexión"}
          </span>
        </div>
      </div>

      {/* Área principal */}
      <div className="flex-1 flex flex-col max-w-3xl mx-auto w-full px-4 py-6">

        {/* Estado inicial */}
        {!chatStarted && (
          <div className="flex-1 flex flex-col items-center justify-center gap-8">
            <div className="text-center">
              <div className="flex justify-center mb-6">
                <div className="relative bg-card p-8 rounded-full shadow-2xl border-2 border-border animate-[float_3s_ease-in-out_infinite]">
                  <BookOpen className="w-16 h-16 text-primary" />
                  <Sparkles className="w-6 h-6 text-primary absolute -top-2 -right-2 animate-[sparkle_2s_ease-in-out_infinite]" />
                </div>
              </div>
              <h2 className="text-2xl font-serif mb-2">¿En qué puedo ayudarte?</h2>
              <p className="text-foreground/50 text-sm">Pregúntame sobre libros, autores o movimientos literarios</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 w-full">
              {selectedQuestions.map((question, index) => (
                <button
                  key={index}
                  onClick={() => handleQuestionClick(question)}
                  className="group bg-card p-4 rounded-2xl shadow border border-border hover:border-primary/50 text-left transition-all hover:-translate-y-1"
                >
                  <div className="flex items-start gap-2">
                    <div className="w-2 h-2 rounded-full bg-primary mt-1.5 shrink-0 group-hover:scale-125 transition-transform" />
                    <p className="text-sm leading-relaxed">{question}</p>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Historial del chat */}
        {chatStarted && (
          <div className="flex-1 flex flex-col gap-4 mb-4">
            {messages.map((msg, index) => (
              <div
                key={index}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                {msg.role === "bot" && (
                  <div className="w-8 h-8 rounded-full bg-card border border-border flex items-center justify-center mr-2 shrink-0 mt-1">
                    <BookOpen className="w-4 h-4 text-primary" />
                  </div>
                )}
                <div
                  className={`max-w-[75%] px-4 py-3 rounded-2xl text-sm leading-relaxed ${
                    msg.role === "user"
                      ? "bg-primary text-primary-foreground rounded-br-sm"
                      : msg.error
                      ? "bg-red-500/10 border border-red-400/30 text-foreground rounded-bl-sm"
                      : "bg-card border border-border text-foreground rounded-bl-sm"
                  }`}
                >
                  {msg.content}
                </div>
              </div>
            ))}

            {/* Puntos de escritura */}
            {loading && (
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
            )}

            <div ref={bottomRef} />
          </div>
        )}

        {/* Input */}
        <div className={chatStarted ? "mt-auto" : ""}>
          <form onSubmit={handleSubmit}>
            <div className="bg-card backdrop-blur-md rounded-2xl shadow-2xl border-2 border-border overflow-hidden">
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Pregúntame sobre cualquier obra literaria..."
                className="w-full px-6 py-5 placeholder-foreground/40 bg-transparent resize-none focus:outline-none text-base"
                rows="2"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage(message);
                  }
                }}
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
        </div>
      </div>

      <style>{`
        @keyframes float {
          0%, 100% { transform: translateY(0px); }
          50% { transform: translateY(-10px); }
        }
        @keyframes sparkle {
          0%, 100% { opacity: 0.3; transform: scale(0.8) rotate(0deg); }
          50% { opacity: 1; transform: scale(1.2) rotate(180deg); }
        }
      `}</style>
    </div>
  );
};