import { useState } from "react";
import { Hero } from "../components/Hero";
import { SideBar } from "../components/Layout/SideBar";

export const Home = () => {
  const [conversations, setConversations] = useState([]);
  const [activeConversation, setActiveConversation] = useState(null);
  const [chatKey, setChatKey] = useState(0);

  const addConversation = (conversation) => {
    setConversations((prev) => [conversation, ...prev]);
  };

  const startNewChat = () => {
    setActiveConversation(null);
    setChatKey((prev) => prev + 1);
  };

  return (
    <div className="flex">
      <SideBar
        conversations={conversations}
        activeConversation={activeConversation}
        onSelectConversation={setActiveConversation}
        onNewChat={startNewChat}
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