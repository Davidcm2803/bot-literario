import { useState, useEffect } from "react";
import { Hero } from "../components/Views/Hero";
import { SideBar } from "../components/Layout/SideBar";
import { useAuth } from "../hooks/useAuth";
import { saveConversation, loadConversations, deleteConversation } from "../lib/firebase";

export const Home = () => {
  const { user }                                    = useAuth();
  const [conversations, setConversations]           = useState([]);
  const [activeConversation, setActiveConversation] = useState(null);
  const [chatKey, setChatKey]                       = useState(0);
  const [isCollapsed, setIsCollapsed]               = useState(false);

  useEffect(() => {
    if (!user) {
      setConversations([]);
      setActiveConversation(null);
      return;
    }

    const fetch = async () => {
      try {
        const data = await loadConversations(user.uid);
        setConversations(data);
      } catch (e) {
        console.error("Error cargando conversaciones:", e);
      }
    };

    fetch();
  }, [user]);

  const addConversation = async (conversation) => {
    setConversations((prev) => {
      const exists = prev.find((c) => c.id === conversation.id);
      if (exists) {
        return prev.map((c) => c.id === conversation.id ? conversation : c);
      }
      return [conversation, ...prev];
    });

    if (user) {
      try {
        await saveConversation(user.uid, conversation);
      } catch (e) {
        console.error("Error guardando conversación:", e);
      }
    }
  };

  const handleDeleteConversation = async (conv) => {
    // Quitar del estado local inmediatamente
    setConversations((prev) => prev.filter((c) => c.id !== conv.id));

    // Si era la conversación activa, limpiar el chat
    if (activeConversation?.id === conv.id) {
      setActiveConversation(null);
      setChatKey((prev) => prev + 1);
    }

    // Borrar de Firestore si hay usuario
    if (user) {
      try {
        await deleteConversation(user.uid, conv.id);
      } catch (e) {
        console.error("Error eliminando conversación:", e);
      }
    }
  };

  const startNewChat = () => {
    setActiveConversation(null);
    setChatKey((prev) => prev + 1);
  };

  return (
    <div className="min-h-screen bg-background">
      <SideBar
        conversations={conversations}
        activeConversation={activeConversation}
        onSelectConversation={setActiveConversation}
        onNewChat={startNewChat}
        onDeleteConversation={handleDeleteConversation}
        isCollapsed={isCollapsed}
        onCollapse={setIsCollapsed}
      />

      <Hero
        key={chatKey}
        activeConversation={activeConversation}
        onConversationSave={addConversation}
        onNewChat={startNewChat}
      />
    </div>
  );
};