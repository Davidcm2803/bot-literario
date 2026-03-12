import { BookOpen, Sparkles } from "lucide-react";

export const WelcomeScreen = ({ questions, onQuestionClick }) => {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-8">
      <div className="text-center">
        <div className="flex justify-center mb-6">
          <div className="relative bg-card p-8 rounded-full shadow-2xl border-2 border-border animate-[float_3s_ease-in-out_infinite]">
            <BookOpen className="w-16 h-16 text-primary" />
            <Sparkles className="w-6 h-6 text-primary absolute -top-2 -right-2 animate-[sparkle_2s_ease-in-out_infinite]" />
          </div>
        </div>
        <h2 className="text-2xl font-serif mb-2">¿En qué puedo ayudarte?</h2>
        <p className="text-foreground/50 text-sm">
          Pregúntame sobre libros, autores o movimientos literarios
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 w-full">
        {questions.map((question, index) => (
          <button
            key={index}
            onClick={() => onQuestionClick(question)}
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
  );
};