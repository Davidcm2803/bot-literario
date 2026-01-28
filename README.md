# Bot Literario

Bot Literario es una aplicación web tipo **chatbot** que permite al usuario realizar
preguntas sobre una colección de libros en formato **TXT**.  
El sistema responde utilizando únicamente el contexto de los libros cargados.

El proyecto está dividido en:
- **Backend**: API en Flask
- **Frontend**: Aplicación web (React + Vite)

---

## Requerimientos del Proyecto

El bot cumple con los siguientes requerimientos:

- Cargar libros en formato TXT desde disco
- Exponer un servidor Flask en el puerto **8090**
- Permitir realizar preguntas vía endpoint `/ask`
- Mostrar preguntas y respuestas en una interfaz tipo chat

---

## Libros Utilizados

Los textos utilizados provienen de Project Gutenberg:

- **Don Quijote** – Miguel de Cervantes
  https://www.gutenberg.org/cache/epub/2000/pg2000.txt

- **El Príncipe** – Nicolás Maquiavelo  
  https://www.gutenberg.org/cache/epub/1232/pg1232.txt

---

## Arquitectura del Proyecto

```text
bot-literario/
│
├── backend/
│   ├── app.py
│   ├── cargar.py
│   ├── ask.py
│   ├── database.py
│   ├── config.py
│   ├── requirements.txt
│   │
│   ├── data/
│   │   ├── raw/
│   │   │   ├── iliada.txt
│   │   │   ├── principe.txt
│   │   │   └── mas_alla_bien_mal.txt
│   │   │
│   │   └── processed/
│   │
│   └── venv/  1000 por sinpe y lo compartimos 
│
├── frontend/
│   ├── node_modules/ Necesario descargar en local
│   ├── public/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Chat.jsx
│   │   │   ├── Message.jsx
│   │   │   └── InputBox.jsx
│   │   │
│   │   ├── services/
│   │   │   └── api.js
│   │   │
│   │   ├── styles/
│   │   │   └── chat.css
│   │   │
│   │   ├── App.jsx
│   │   └── main.jsx
│   │
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.js
│   └── eslint.config.js
│
├── README.md
└── .gitignore

