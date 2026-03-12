export const ServerStatus = ({ status }) => {
  const colorMap = {
    checking: "bg-yellow-400 animate-pulse",
    online: "bg-green-400",
    offline: "bg-red-400",
  };

  const labelMap = {
    checking: "Verificando...",
    online: "Conectado",
    offline: "Sin conexión",
  };

  return (
    <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-full border border-border bg-card">
      <span className={`w-2 h-2 rounded-full ${colorMap[status]}`} />
      <span>{labelMap[status]}</span>
    </div>
  );
};