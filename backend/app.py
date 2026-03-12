import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify
from flask_cors import CORS

from models.schema import create_schema
from models.books import upload_all_books
from routes.user_routes import user_bp
from routes.books_routes import books_bp
from routes.bot_routes import bot_bp
from config import init_weaviate

app = Flask(__name__)
CORS(app, origins=["http://localhost:5173", "http://localhost:5174"])

# Weaviate client
client = init_weaviate()
app.config["WEAVIATE_CLIENT"] = client

# Inicializar schema y cargar libros
create_schema(client)
print("Cargando libros...")
upload_all_books(client, books_folder="books")
print("Libros cargados.")

# Registrar blueprints
app.register_blueprint(user_bp)
app.register_blueprint(books_bp)
app.register_blueprint(bot_bp)

@app.route("/")
def home():
    return jsonify({"message": "Bot Literario con Weaviate"})

@app.route("/init-db")
def init_db():
    create_schema(client)
    return jsonify({"message": "Schema creado/verificado"})

if __name__ == "__main__":
    app.run(port=8090, debug=True)