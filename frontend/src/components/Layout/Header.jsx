import { BookOpen } from "lucide-react";
import { ServerStatus } from "../UI/ServerStatus";

export const Header = ({ serverStatus }) => {
  return (
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
      <ServerStatus status={serverStatus} />
    </div>
  );
};