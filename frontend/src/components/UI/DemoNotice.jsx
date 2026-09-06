import { useState } from "react";

export function DemoNotice() {
  const [visible, setVisible] = useState(true);

  if (!visible) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
        padding: "1rem",
      }}
    >
      <div
        style={{
          background: "#1a1a1a",
          color: "#f5f5f5",
          borderRadius: "12px",
          padding: "2rem",
          maxWidth: "420px",
          width: "100%",
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <h2 style={{ marginTop: 0, fontSize: "1.25rem" }}>
          👋 Bienvenido a mi Bot Literario
        </h2>
        <p style={{ lineHeight: 1.5, color: "#ccc" }}>
          Este es uno de mis proyectos favoritos: un sistema RAG (Retrieval-Augmented
          Generation) construido con <strong>Python, Flask y Weaviate</strong> como
          base de datos vectorial, con búsqueda híbrida y re-ranking sobre libros
          completos.
        </p>
        <p style={{ lineHeight: 1.5, color: "#ccc" }}>
          Por costos de hosting, el backend no corre 24/7. Si quieres ver la demo
          completa en funcionamiento, escríbeme y lo activo:
        </p>
        <p style={{ fontWeight: 600, fontSize: "1.05rem" }}>
          📧{" "}
          <a
            href="mailto:davidcm2803@gmail.com"
            style={{ color: "#8ab4f8", textDecoration: "none" }}
          >
            davidcm2803@gmail.com
          </a>
        </p>
        <button
          onClick={() => setVisible(false)}
          style={{
            marginTop: "1rem",
            width: "100%",
            padding: "0.6rem",
            borderRadius: "8px",
            border: "none",
            background: "#8ab4f8",
            color: "#1a1a1a",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Entendido
        </button>
      </div>
    </div>
  );
}