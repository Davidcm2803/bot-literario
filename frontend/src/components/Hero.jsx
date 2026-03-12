import { useState, useEffect, useRef } from "react";
import { Header } from "./Layout/Header";
import { WelcomeScreen } from "./Views/WelcomeScreen";
import { ChatMessage } from "./UI/ChatMessage";
import { TypingIndicator } from "./UI/TypingIndicator";
import { ChatInput } from "./UI/ChatInput";

export const Hero = ({ activeConversation, onConversationSave, onNewChat }) => {
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [serverStatus, setServerStatus] = useState("checking");
  const [selectedQuestions, setSelectedQuestions] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const bottomRef = useRef(null);

  // Cargar conversación activa cuando se selecciona desde el sidebar
  useEffect(() => {
    if (activeConversation) {
      setMessages(activeConversation.messages);
      setHistory(activeConversation.history);
      setConversationId(activeConversation.id);
    } else {
      // null = nuevo chat, limpiar todo
      setMessages([]);
      setHistory([]);
      setConversationId(null);
      setMessage("");
    }
  }, [activeConversation]);

  useEffect(() => {
    const fetchQuestions = async () => {
      try {
        const res = await fetch("http://localhost:8090/questions");
        const data = await res.json();
        const shuffled = [...data.questions].sort(() => Math.random() - 0.5);
        setSelectedQuestions(shuffled.slice(0, 3));
      } catch {
        setSelectedQuestions([
          "¿Quién escribió Don Quijote?",
          "¿De qué trata The Antichrist?",
          "¿Cuál es el tema principal de The Prince?",
        ]);
      }
    };
    fetchQuestions();
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

  const handleNewChat = () => {
    setMessages([]);
    setHistory([]);
    setConversationId(null);
    setMessage("");
    if (onNewChat) onNewChat();
  };

  const sendMessage = async (text) => {
    if (!text.trim()) return;

    const newMessages = [...messages, { role: "user", content: text }];
    setMessages(newMessages);
    setMessage("");
    setLoading(true);

    try {
      const res = await fetch("http://localhost:8090/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, history }),
      });

      if (!res.ok) throw new Error(`Server error: ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let firstToken = true;
      let fullResponse = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const token = decoder.decode(value);
        fullResponse += token;

        if (firstToken) {
          setLoading(false);
          setMessages((prev) => [...prev, { role: "bot", content: "" }]);
          firstToken = false;
        }

        setMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last.role === "bot") {
            updated[updated.length - 1] = { ...last, content: last.content + token };
          }
          return updated;
        });
      }

      const newHistory = [...history, { question: text, answer: fullResponse }];
      setHistory(newHistory);

      const finalMessages = [...newMessages, { role: "bot", content: fullResponse }];
      const isNew = !conversationId;
      const id = conversationId || Date.now();

      if (isNew) {
        setConversationId(id);
        onConversationSave({
          id,
          title: text,
          messages: finalMessages,
          history: newHistory,
        });
      }

    } catch (err) {
      console.error("❌ Error completo:", err);
      setLoading(false);
      setMessages((prev) => [
        ...prev,
        { role: "bot", content: "❌ No se pudo conectar con el servidor.", error: true },
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
            <div ref={bottomRef} />
          </div>
        )}

        <div className={chatStarted ? "sticky bottom-0 bg-background pt-2 pb-4" : ""}>
          <ChatInput
            message={message}
            onChange={setMessage}
            onSubmit={sendMessage}
            loading={loading}
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