from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import json
from datetime import datetime

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==================================================
# APP
# ==================================================
app = Flask(__name__)
app.secret_key = "cinafe_secret_key"

# ==================================================
# GOOGLE DRIVE - OAUTH
# ==================================================
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_SECRETS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
TOKEN_FILE = "token.json"

PASTA_ANO = "2026"
PASTA_SOLICITACOES = "SOLICITACOES"


# ==================================================
# GOOGLE DRIVE FUNÇÕES
# ==================================================
def get_drive_service():
    if not CLIENT_SECRETS_JSON:
        raise Exception("Credenciais OAuth não configuradas")

    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise Exception("Google Drive não autorizado")

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def get_or_create_folder(name, parent_id=None):
    drive = get_drive_service()

    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = drive.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)"
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

    folder = drive.files().create(
        body=metadata,
        fields="id"
    ).execute()

    return folder["id"]


def upload_to_drive(file_path, file_name, solicitacao, escola):
    drive = get_drive_service()

    pasta_ano = get_or_create_folder(PASTA_ANO)
    pasta_solic = get_or_create_folder(PASTA_SOLICITACOES, pasta_ano)
    pasta_nome = get_or_create_folder(solicitacao, pasta_solic)
    pasta_escola = get_or_create_folder(escola, pasta_nome)

    media = MediaFileUpload(file_path, resumable=False)

    metadata = {
        "name": file_name,
        "parents": [pasta_escola]
    }

    uploaded = drive.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    return uploaded["webViewLink"]


# ==================================================
# BANCO DE DADOS
# ==================================================
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS comunicados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT,
            mensagem TEXT,
            data TEXT
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

# ==================================================
# LOGIN
# ==================================================
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


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ==================================================
# DASHBOARD
# ==================================================
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    conn = get_db()
    solicitacoes = conn.execute(
        "SELECT * FROM solicitacoes ORDER BY id DESC"
    ).fetchall()

    comunicados = conn.execute(
        "SELECT * FROM comunicados ORDER BY id DESC"
    ).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        role=session["role"],
        solicitacoes=solicitacoes,
        comunicados=comunicados
    )


# ==================================================
# COMUNICADOS
# ==================================================
@app.route("/novo-comunicado", methods=["GET", "POST"])
def novo_comunicado():
    if session.get("role") != "admin":
        return redirect("/dashboard")

    if request.method == "POST":
        titulo = request.form["titulo"]
        mensagem = request.form["mensagem"]
        data = datetime.now().strftime("%d/%m/%Y %H:%M")

        conn = get_db()
        conn.execute(
            "INSERT INTO comunicados (titulo, mensagem, data) VALUES (?, ?, ?)",
            (titulo, mensagem, data)
        )
        conn.commit()
        conn.close()

        return redirect("/dashboard")

    return """
        <h2>Novo Comunicado</h2>
        <form method="POST">
            <input name="titulo" placeholder="Título" required><br><br>
            <textarea name="mensagem" placeholder="Mensagem" required></textarea><br><br>
            <button>Publicar</button>
        </form>
        <br>
        <a href="/dashboard">Voltar</a>
    """


# ==================================================
# ADMIN - ESCOLAS E SOLICITAÇÕES
# ==================================================
@app.route("/criar-escola", methods=["GET", "POST"])
def criar_escola():
    if session.get("role") != "admin":
        return redirect("/")

    if request.method == "POST":
        nome = request.form["nome"]
        codigo = request.form["codigo"].lower()
        senha = f"{codigo}@123"

        conn = get_db()
        conn.execute(
            "INSERT INTO escolas (nome, codigo) VALUES (?, ?)",
            (nome, codigo)
        )
        conn.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            (codigo, generate_password_hash(senha), "escola")
        )
        conn.commit()
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


# ==================================================
# CONTROLE DA SECRETARIA
# ==================================================
@app.route("/controle/<int:id>")
def controle(id):
    if session.get("role") != "admin":
        return redirect("/")

    conn = get_db()

    solicitacao = conn.execute(
        "SELECT * FROM solicitacoes WHERE id=?", (id,)
    ).fetchone()

    escolas = conn.execute(
        "SELECT codigo FROM escolas"
    ).fetchall()

    envios = conn.execute(
        "SELECT * FROM envios WHERE solicitacao_id=?", (id,)
    ).fetchall()

    envios_dict = {e["escola"]: e for e in envios}

    prazo = datetime.strptime(solicitacao["prazo"], "%Y-%m-%d")
    hoje = datetime.now()

    resultado = []

    for escola in escolas:
        codigo = escola["codigo"]

        if codigo in envios_dict:
            envio = datetime.strptime(envios_dict[codigo]["data_envio"], "%Y-%m-%d %H:%M")
            status = "🟢 Enviado" if envio.date() <= prazo.date() else "🔴 Fora do prazo"
            link = envios_dict[codigo]["link_drive"]
        else:
            status = "🟡 Pendente" if hoje.date() <= prazo.date() else "🔴 Em atraso"
            link = None

        resultado.append({
            "escola": codigo,
            "status": status,
            "link": link
        })

    conn.close()

    return render_template(
        "controle.html",
        solicitacao=solicitacao,
        resultado=resultado
    )


# ==================================================
# ESCOLA - ENVIO
# ==================================================
@app.route("/enviar/<int:id>", methods=["GET", "POST"])
def enviar(id):
    if session.get("role") != "escola":
        return redirect("/")

    conn = get_db()
    solicitacao = conn.execute(
        "SELECT * FROM solicitacoes WHERE id=?", (id,)
    ).fetchone()
    prazo = datetime.strptime(solicitacao["prazo"], "%Y-%m-%d")

    if request.method == "POST":
        file = request.files["arquivo"]
        filename = secure_filename(file.filename)

        temp_path = f"/tmp/{filename}"
        file.save(temp_path)

        data_envio = datetime.now().strftime("%Y-%m-%d %H:%M")

        link = upload_to_drive(
            temp_path,
            filename,
            solicitacao["titulo"],
            session["user"]
        )

        conn.execute(
            "INSERT INTO envios (solicitacao_id, escola, arquivo, link_drive, data_envio) VALUES (?, ?, ?, ?, ?)",
            (id, session["user"], filename, link, data_envio)
        )
        conn.commit()
        conn.close()

        os.remove(temp_path)

        envio = datetime.strptime(data_envio, "%Y-%m-%d %H:%M")
        fora_prazo = envio.date() > prazo.date()

        msg = f"Arquivo enviado com sucesso em {envio.strftime('%d/%m/%Y às %H:%M')}"
        if fora_prazo:
            msg += " (FORA DO PRAZO)"

        return f"""
            <h3>{msg}</h3>
            <a href="/dashboard">Voltar ao painel</a>
        """

    conn.close()

    return f"""
        <h2>Enviar Arquivo</h2>
        <p><strong>Solicitação:</strong> {solicitacao['titulo']}</p>
        <p><strong>Prazo:</strong> {solicitacao['prazo']}</p>
        <form method="POST" enctype="multipart/form-data">
            <input type="file" name="arquivo" required><br><br>
            <button>Enviar</button>
        </form>
    """
