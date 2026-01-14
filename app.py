from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import json
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)
app.secret_key = "cinafe_secret_key"

# =========================
# GOOGLE DRIVE CONFIG
# =========================
SCOPES = ["https://www.googleapis.com/auth/drive"]
GOOGLE_CREDS = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])

credentials = service_account.Credentials.from_service_account_info(
    GOOGLE_CREDS, scopes=SCOPES
)

drive_service = build("drive", "v3", credentials=credentials)

# IDs DAS PASTAS
PASTA_ANO = "2026"
PASTA_SOLICITACOES = "SOLICITAÇÕES"

# =========================
# BANCO DE DADOS
# =========================
def get_db():
    conn = sqlite3.connect("cinafe.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS escolas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            codigo TEXT UNIQUE NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS comunicados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            mensagem TEXT NOT NULL,
            data TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            descricao TEXT NOT NULL,
            prazo TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS envios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            solicitacao_id INTEGER,
            escola TEXT,
            arquivo TEXT,
            link_drive TEXT,
            data_envio TEXT
        )
    """)

    conn.commit()
    conn.close()


def create_admin():
    conn = get_db()
    admin = conn.execute(
        "SELECT * FROM users WHERE username = ?", ("admin",)
    ).fetchone()

    if not admin:
        conn.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("admin123"), "admin")
        )
        conn.commit()

    conn.close()


init_db()
create_admin()

# =========================
# FUNÇÕES GOOGLE DRIVE
# =========================
def get_or_create_folder(name, parent_id=None):
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = drive_service.files().list(
        q=query, spaces="drive", fields="files(id, name)"
    ).execute()

    files = results.get("files", [])
    if files:
        return files[0]["id"]

    folder_metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    if parent_id:
        folder_metadata["parents"] = [parent_id]

    folder = drive_service.files().create(
        body=folder_metadata, fields="id"
    ).execute()

    return folder.get("id")


def upload_to_drive(file_path, file_name, solicitacao, escola):
    root_ano = get_or_create_folder(PASTA_ANO)
    root_solic = get_or_create_folder(PASTA_SOLICITACOES, root_ano)
    pasta_solic = get_or_create_folder(solicitacao, root_solic)
    pasta_escola = get_or_create_folder(escola, pasta_solic)

    media = MediaFileUpload(file_path, resumable=True)
    file_metadata = {
        "name": file_name,
        "parents": [pasta_escola]
    }

    uploaded = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    return uploaded["webViewLink"]


# =========================
# ROTAS
# =========================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect("/dashboard")

        return "Usuário ou senha inválidos"

    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    conn = get_db()
    comunicados = conn.execute("SELECT * FROM comunicados ORDER BY id DESC").fetchall()
    solicitacoes = conn.execute("SELECT * FROM solicitacoes ORDER BY id DESC").fetchall()
    conn.close()

    return render_template(
        "dashboard.html",
        role=session["role"],
        comunicados=comunicados,
        solicitacoes=solicitacoes
    )


@app.route("/nova-solicitacao", methods=["GET", "POST"])
def nova_solicitacao():
    if session.get("role") != "admin":
        return redirect("/")

    if request.method == "POST":
        conn = get_db()
        conn.execute(
            "INSERT INTO solicitacoes (titulo, descricao, prazo) VALUES (?, ?, ?)",
            (request.form["titulo"], request.form["descricao"], request.form["prazo"])
        )
        conn.commit()
        conn.close()
        return redirect("/dashboard")

    return """
    <h2>Nova Solicitação</h2>
    <form method="POST">
        <input name="titulo" placeholder="Título" required><br><br>
        <textarea name="descricao" placeholder="Descrição" required></textarea><br><br>
        <input type="date" name="prazo" required><br><br>
        <button>Criar</button>
    </form>
    """


@app.route("/enviar/<int:id>", methods=["GET", "POST"])
def enviar(id):
    if session.get("role") != "escola":
        return redirect("/")

    if request.method == "POST":
        file = request.files["arquivo"]
        filename = secure_filename(file.filename)
        temp_path = f"/tmp/{filename}"
        file.save(temp_path)

        conn = get_db()
        solicitacao = conn.execute(
            "SELECT titulo FROM solicitacoes WHERE id=?", (id,)
        ).fetchone()["titulo"]

        link = upload_to_drive(
            temp_path, filename, solicitacao, session["user"]
        )

        conn.execute(
            "INSERT INTO envios (solicitacao_id, escola, arquivo, link_drive, data_envio) VALUES (?, ?, ?, ?, ?)",
            (id, session["user"], filename, link, datetime.now().strftime("%d/%m/%Y %H:%M"))
        )
        conn.commit()
        conn.close()

        os.remove(temp_path)
        return "Arquivo enviado para o Google Drive com sucesso"

    return """
    <h2>Enviar Arquivo</h2>
    <form method="POST" enctype="multipart/form-data">
        <input type="file" name="arquivo" required><br><br>
        <button>Enviar</button>
    </form>
    """


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")
