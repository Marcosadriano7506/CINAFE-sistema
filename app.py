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
# CONFIG GOOGLE DRIVE
# =========================
SCOPES = ["https://www.googleapis.com/auth/drive"]

PASTA_ANO = "2026"
PASTA_SOLICITACOES = "SOLICITACOES"


def get_drive_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise Exception("Credenciais do Google Drive não encontradas")

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=SCOPES
    )
    return build("drive", "v3", credentials=credentials)


def get_or_create_folder(name, parent_id=None):
    drive_service = get_drive_service()

    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = drive_service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    files = results.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = drive_service.files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True
    ).execute()

    return folder["id"]


def upload_to_drive(file_path, file_name, solicitacao, escola):
    drive_service = get_drive_service()

    pasta_ano = get_or_create_folder(PASTA_ANO)
    pasta_solic = get_or_create_folder(PASTA_SOLICITACOES, pasta_ano)
    pasta_nome = get_or_create_folder(solicitacao, pasta_solic)
    pasta_escola = get_or_create_folder(escola, pasta_nome)

    media = MediaFileUpload(file_path, resumable=True)

    metadata = {
        "name": file_name,
        "parents": [pasta_escola]
    }

    uploaded = drive_service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True
    ).execute()

    return uploaded["webViewLink"]


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
            username TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS escolas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            codigo TEXT UNIQUE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT,
            descricao TEXT,
            prazo TEXT
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
        "SELECT * FROM users WHERE username='admin'"
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
# ROTAS
# =========================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
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
    solicitacoes = conn.execute(
        "SELECT * FROM solicitacoes ORDER BY id DESC"
    ).fetchall()
    conn.close()

    return render_template(
        "dashboard.html",
        role=session["role"],
        solicitacoes=solicitacoes
    )


@app.route("/criar-escola", methods=["GET", "POST"])
def criar_escola():
    if session.get("role") != "admin":
        return redirect("/")

    if request.method == "POST":
        nome = request.form["nome"]
        codigo = request.form["codigo"].lower()
        senha = f"{codigo}@123"

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO escolas (nome, codigo) VALUES (?, ?)",
                (nome, codigo)
            )
            conn.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                (codigo, generate_password_hash(senha), "escola")
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return "Código de escola já existe"

        conn.close()
        return f"""
            <h3>Escola cadastrada</h3>
            <p>Login: {codigo}</p>
            <p>Senha: {senha}</p>
            <a href="/dashboard">Voltar</a>
        """

    return """
        <h2>Cadastrar Escola</h2>
        <form method="POST">
            <input name="nome" placeholder="Nome da escola" required><br><br>
            <input name="codigo" placeholder="Código da escola" required><br><br>
            <button>Cadastrar</button>
        </form>
    """


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
            temp_path,
            filename,
            solicitacao,
            session["user"]
        )

        conn.execute(
            "INSERT INTO envios (solicitacao_id, escola, arquivo, link_drive, data_envio) VALUES (?, ?, ?, ?, ?)",
            (id, session["user"], filename, link,
             datetime.now().strftime("%d/%m/%Y %H:%M"))
        )
        conn.commit()
        conn.close()

        os.remove(temp_path)
        return "Arquivo enviado com sucesso"

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
