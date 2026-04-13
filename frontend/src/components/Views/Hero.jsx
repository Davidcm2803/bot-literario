import { useState, useEffect, useRef } from "react";
import { Header } from "../Layout/Header";
import { WelcomeScreen } from "./WelcomeScreen";
import { ChatMessage } from "../UI/ChatMessage";
import { TypingIndicator } from "../UI/TypingIndicator";
import { ChatInput } from "../UI/ChatInput";

export const Hero = ({ activeConversation, onConversationSave, onNewChat, isCollapsed }) => {
  const [message, setMessage]               = useState("");
  const [messages, setMessages]             = useState([]);
  const [history, setHistory]               = useState([]);
  const [loading, setLoading]               = useState(false);
  const [serverStatus, setServerStatus]     = useState("checking");
  const [selectedQuestions, setSelectedQuestions] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [bookSelectList, setBookSelectList] = useState(null);
  const pendingQuestionRef                  = useRef(null);
  const bottomRef                           = useRef(null);

  // Carga o limpia el estado cuando cambia la conversacion activa desde el sidebar
  useEffect(() => {
    if (activeConversation) {
      setMessages(activeConversation.messages);
      setHistory(activeConversation.history || []);
      setConversationId(activeConversation.id);
      setBookSelectList(null);
    } else {
      setMessages([]);
      setHistory([]);
      setConversationId(null);
      setMessage("");
      setBookSelectList(null);
    }
  }, [activeConversation]);

  // Obtiene preguntas sugeridas del servidor, con fallback si falla
  useEffect(() => {
    const fetchQuestions = async () => {
      try {
        const res  = await fetch("http://localhost:8090/questions");
        const data = await res.json();
        const shuffled = [...data.questions].sort(() => Math.random() - 0.5);
        setSelectedQuestions(shuffled.slice(0, 3));
      } catch {
        setSelectedQuestions([
          "Quien escribio Don Quijote?",
          "De que trata The Antichrist?",
          "Cual es el tema principal de The Prince?",
        ]);
      }
    };
    fetchQuestions();
  }, []);

  // Verifica si el servidor esta disponible al montar el componente
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

  // Hace scroll automatico al ultimo mensaje cada vez que cambia la lista
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading, bookSelectList]);

  // Reinicia todo el estado local y notifica al padre
  const handleNewChat = () => {
    setMessages([]);
    setHistory([]);
    setConversationId(null);
    setMessage("");
    setBookSelectList(null);
    if (onNewChat) onNewChat();
  };

  // Llama al padre para persistir la conversacion
  const saveConversation = (id, title, finalMessages, newHistory) => {
    onConversationSave({ id, title, messages: finalMessages, history: newHistory });
  };

  // Cuando el usuario elige un libro, retoma la pregunta original y la reenvía con el titulo incluido
  const handleBookSelect = (book) => {
    setBookSelectList(null);
    const originalQuestion = pendingQuestionRef.current;
    pendingQuestionRef.current = null;
    if (!originalQuestion) return;
    sendMessage(`${originalQuestion} en ${book.title}`);
  };

  const sendMessage = async (text) => {
    if (!text.trim()) return;

    setBookSelectList(null);
    const newMessages = [...messages, { role: "user", content: text }];
    setMessages(newMessages);
    setMessage("");
    setLoading(true);

    // Se guarda el historial antes del await para evitar leer estado desactualizado
    const currentHistory = history;

    try {
      const res = await fetch("http://localhost:8090/ask", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ question: text, history: currentHistory }),
      });

      if (!res.ok) throw new Error(`Server error: ${res.status}`);

      const contentType = res.headers.get("content-type") || "";

      // El servidor responde con JSON cuando no puede identificar el libro
      if (contentType.includes("application/json")) {
        const data = await res.json();

        if (data.type === "book_select") {
          setLoading(false);
          pendingQuestionRef.current = text;

          const botMsg      = "No estoy seguro de que libro te refieres. Sobre cual quieres preguntar?";
          const updatedMsgs = [...newMessages, { role: "bot", content: botMsg }];

          setMessages(updatedMsgs);
          setBookSelectList(data.books);

          const newHistory = [...currentHistory, { question: text, answer: botMsg }];
          setHistory(newHistory);

          const id    = conversationId || Date.now();
          const isNew = !conversationId;
          if (isNew) setConversationId(id);
          saveConversation(id, text, updatedMsgs, newHistory);

          return;
        }

        throw new Error("Respuesta JSON inesperada del servidor");
      }

      // Caso normal: el servidor hace streaming de tokens
      const reader     = res.body.getReader();
      const decoder    = new TextDecoder();
      let firstToken   = true;
      let fullResponse = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const token = decoder.decode(value);
        fullResponse += token;

        // Al llegar el primer token se oculta el indicador y se agrega el mensaje vacio
        if (firstToken) {
          setLoading(false);
          setMessages((prev) => [...prev, { role: "bot", content: "" }]);
          firstToken = false;
        }

        // Cada token se concatena al ultimo mensaje del bot
        setMessages((prev) => {
          const updated = [...prev];
          const last    = updated[updated.length - 1];
          if (last.role === "bot") {
            updated[updated.length - 1] = { ...last, content: last.content + token };
          }
          return updated;
        });
      }

      // Al terminar el stream se guarda la conversacion completa
      const newHistory    = [...currentHistory, { question: text, answer: fullResponse }];
      const finalMessages = [...newMessages, { role: "bot", content: fullResponse }];
      const isNew         = !conversationId;
      const id            = conversationId || Date.now();

      setHistory(newHistory);
      if (isNew) setConversationId(id);
      saveConversation(id, isNew ? text : (activeConversation?.title || text), finalMessages, newHistory);

    } catch (err) {
      console.error("Error completo:", err);
      setLoading(false);
      setMessages((prev) => [
        ...prev,
        { role: "bot", content: "No se pudo conectar con el servidor.", error: true },
      ]);
    }
  };

  const chatStarted = messages.length > 0;

  return (
    <div className="min-h-screen bg-background flex flex-col flex-1">
      <Header serverStatus={serverStatus} onNewChat={handleNewChat} />

      <div className="flex-1 flex flex-col max-w-3xl mx-auto w-full px-4 py-6">
        {!chatStarted && (
          <WelcomeScreen
            questions={selectedQuestions}
            onQuestionClick={setMessage}
          />
        )}

        {chatStarted && (
          <div className="flex-1 flex flex-col gap-4 mb-4">
            {messages.map((msg, index) => (
              <ChatMessage key={index} {...msg} />
            ))}
            {loading && <TypingIndicator />}

            {/* Selector de libros: aparece cuando el servidor no identifica el libro */}
            {bookSelectList && (
              <div className="flex flex-col gap-2 pl-2">
                <p className="text-xs text-foreground/50 mb-1">Selecciona un libro:</p>
                <div className="flex flex-wrap gap-2">
                  {bookSelectList.map((book) => (
                    <button
                      key={book.id}
                      onClick={() => handleBookSelect(book)}
                      className="px-4 py-2 rounded-xl border-2 border-primary text-primary text-sm font-medium hover:bg-primary hover:text-primary-foreground transition-all"
                    >
                      {book.title}
                      <span className="ml-1 text-xs opacity-60">- {book.author}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        )}

        <div className={chatStarted ? "sticky bottom-0 bg-background pt-2 pb-4" : ""}>
          <ChatInput
            message={message}
            onChange={setMessage}
            onSubmit={sendMessage}
            loading={loading}
            disabled={!!bookSelectList}
          />
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