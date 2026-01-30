import React, { useState, useEffect } from 'react';
import { BookOpen, Sparkles, Send } from 'lucide-react';

export const Hero = () => {
  const [message, setMessage] = useState('');
  const [selectedQuestions, setSelectedQuestions] = useState([]);
  
  const allQuestions = [
    "¿FajitasdePollo?",
    "¿Galleta?",
    "lmao",
    "¿Raimbow Siege Six?",
    "¿UN ROCKET?",
  ];

  useEffect(() => {
    const shuffled = [...allQuestions].sort(() => Math.random() - 0.5);
    setSelectedQuestions(shuffled.slice(0, 3));
  }, []);

  const handleQuestionClick = (question) => {
    setMessage(question);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (message.trim()) {
      console.log('Mensaje enviado:', message);
      //se supone que aca va la conexion con py
      setMessage('');
    }
  };

  return (
    <div className="min-h-screen bg-background relative overflow-hidden">
      
      <div className="container mx-auto px-4 py-12 md:py-20 relative z-10">
        {/* Header */}
        <div className="text-center mb-12 md:mb-16">
          <h1 className="text-5xl md:text-7xl font-serif mb-4 animate-[fadeIn_0.8s_ease-out_forwards] opacity-0">
            Biblio IA
          </h1>
          <p className="text-lg md:text-xl tracking-wide animate-[fadeIn_0.8s_ease-out_0.2s_forwards] opacity-0">
            Tu bot literario inteligente
          </p>
        </div>

        {/* Animacion del Icon */}
        <div className="flex justify-center mb-12 animate-[fadeIn_0.8s_ease-out_0.4s_forwards] opacity-0">
          <div className="relative">

            {/* Main icon del libro y las chispas*/}
            <div className="relative bg-card p-8 rounded-full shadow-2xl border-2 border-border animate-[float_3s_ease-in-out_infinite]">
              <BookOpen className="w-16 h-16 md:w-20 md:h-20 text-primary animate-[bookOpen_2s_ease-in-out_infinite]" />
              <Sparkles className="w-6 h-6 text-primary absolute -top-2 -right-2 animate-[sparkle_2s_ease-in-out_infinite]" />
            </div>
          </div>
        </div>

        {/* Cards */}
        <div className="max-w-4xl mx-auto mb-12">
          <p className="text-center text-sm uppercase tracking-widest mb-6">
            Preguntas sugeridas
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 animate-[fadeIn_0.8s_ease-out_0.6s_forwards] opacity-0">
            {selectedQuestions.map((question, index) => (
              <button
                key={index}
                onClick={() => handleQuestionClick(question)}
                className="group bg-card backdrop-blur-sm p-6 rounded-2xl shadow-lg hover:shadow-2xl transition-all duration-300 border border-border hover:border-primary/50 text-left hover:-translate-y-1 animate-[slideUp_0.6s_ease-out_forwards] opacity-0"
                style={{ animationDelay: `${0.7 + index * 0.1}s` }}
              >
                <div className="flex items-start gap-3">
                  <div className="w-2 h-2 rounded-full bg-primary mt-2 group-hover:scale-125 transition-transform" />
                  <p className="text-sm md:text-base  group-hover:text-foreground transition-colors leading-relaxed">
                    {question}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Input del bot*/}
        <div className="max-w-3xl mx-auto animate-[fadeIn_0.8s_ease-out_1s_forwards] opacity-0">
          <form onSubmit={handleSubmit} className="relative">
            <div className="bg-card backdrop-blur-md rounded-2xl shadow-2xl border-2 border-border overflow-hidden">
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Pregúntame sobre cualquier obra literaria, autor o movimiento..."
                className="w-full px-6 py-5 placeholder-foreground/40 bg-transparent resize-none focus:outline-none text-lg"
                rows="3"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit(e);
                  }
                }}
              />
              <div className="flex items-center justify-between px-6 py-4 bg-background border-t border-border">
                <div className="flex items-center gap-2 text-xs ">
                  <kbd className="px-2 py-1 bg-card rounded border border-border font-mono">Enter</kbd>
                  <span>para enviar</span>
                </div>
                <button
                  type="submit"
                  disabled={!message.trim()}
                  className="bg-primary text-primary-foreground px-6 py-3 rounded-xl font-medium hover:opacity-90 transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 shadow-lg hover:shadow-xl hover:-translate-y-0.5 transform"
                >
                  Enviar
                  <Send className="w-4 h-4" />
                </button>
              </div>
            </div>
          </form>
          
          <p className="text-center text-xs text-foreground/50 mt-4">
            Explora el mundo de la literatura con inteligencia artificial
          </p>
        </div>
      </div>

      {/* Animacion del icon */}
      <style>{`
        @keyframes fadeIn {
          from {
            opacity: 0;
            transform: translateY(20px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        @keyframes slideUp {
          from {
            opacity: 0;
            transform: translateY(30px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        @keyframes float {
          0%, 100% {
            transform: translateY(0px);
          }
          50% {
            transform: translateY(-10px);
          }
        }

        @keyframes bookOpen {
          0%, 100% {
            transform: rotateY(0deg);
          }
          50% {
            transform: rotateY(15deg);
          }
        }

        @keyframes sparkle {
          0%, 100% {
            opacity: 0.3;
            transform: scale(0.8) rotate(0deg);
          }
          50% {
            opacity: 1;
            transform: scale(1.2) rotate(180deg);
          }
        }
      `}</style>
    </div>
  );
}