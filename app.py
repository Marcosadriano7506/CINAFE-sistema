from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import json
from datetime import datetime

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==================================================
# APP
# ==================================================
app = Flask(__name__)
app.secret_key = "cinafe_secret_key"

# ==================================================
# GOOGLE DRIVE - OAUTH (RENDER SAFE)
# ==================================================
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

CLIENT_SECRETS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
GOOGLE_DRIVE_TOKEN = os.environ.get("GOOGLE_DRIVE_TOKEN")

PASTA_ANO = "2026"
PASTA_SOLICITACOES = "SOLICITACOES"

# ==================================================
# GOOGLE DRIVE FUNÇÕES
# ==================================================
def get_drive_service():
    if not CLIENT_SECRETS_JSON:
        raise Exception("GOOGLE_CREDENTIALS_JSON não configurado")

    if not GOOGLE_DRIVE_TOKEN:
        raise Exception("Google Drive não autorizado")

    creds = Credentials.from_authorized_user_info(
        json.loads(GOOGLE_DRIVE_TOKEN),
        SCOPES
    )

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

    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = drive.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def upload_to_drive(file_path, file_name, solicitacao, escola):
    drive = get_drive_service()

    pasta_ano = get_or_create_folder(PASTA_ANO)
    pasta_solic = get_or_create_folder(PASTA_SOLICITACOES, pasta_ano)
    pasta_nome = get_or_create_folder(solicitacao, pasta_solic)
    pasta_escola = get_or_create_folder(escola, pasta_nome)

    media = MediaFileUpload(file_path, resumable=False)

    metadata = {"name": file_name, "parents": [pasta_escola]}

    uploaded = drive.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    return uploaded["webViewLink"]

# ==================================================
# BANCO
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
# COMUNICADOS (INLINE)
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
        <br><a href="/dashboard">Voltar</a>
    """

# ==================================================
# ESCOLAS (INLINE)
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
            <h3>Escola cadastrada com sucesso</h3>
            <p><b>Login:</b> {codigo}</p>
            <p><b>Senha:</b> {senha}</p>
            <a href="/dashboard">Voltar</a>
        """

    return """
        <h2>Cadastrar Escola</h2>
        <form method="POST">
            <input name="nome" placeholder="Nome da escola" required><br><br>
            <input name="codigo" placeholder="Código da escola" required><br><br>
            <button>Cadastrar</button>
        </form>
        <br><a href="/dashboard">Voltar</a>
    """

# ==================================================
# SOLICITAÇÕES (INLINE)
# ==================================================
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
        <br><a href="/dashboard">Voltar</a>
    """

# ==================================================
# ENVIO (ESCOLA)
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
        msg = f"Arquivo enviado com sucesso em {envio.strftime('%d/%m/%Y às %H:%M')}"
        if envio.date() > prazo.date():
            msg += " (FORA DO PRAZO)"

        return f"<h3>{msg}</h3><a href='/dashboard'>Voltar</a>"

    conn.close()

    return f"""
        <h2>Enviar Arquivo</h2>
        <p><b>Solicitação:</b> {solicitacao['titulo']}</p>
        <p><b>Prazo:</b> {solicitacao['prazo']}</p>
        <form method="POST" enctype="multipart/form-data">
            <input type="file" name="arquivo" required><br><br>
            <button>Enviar</button>
        </form>
        <br><a href="/dashboard">Voltar</a>
    """

# ==================================================
# OAUTH
# ==================================================
@app.route("/autorizar-google")
def autorizar_google():
    flow = Flow.from_client_config(
        json.loads(CLIENT_SECRETS_JSON),
        scopes=SCOPES
    )
    flow.redirect_uri = "https://cinafe.onrender.com/oauth2callback"

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent"
    )
    return redirect(auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    flow = Flow.from_client_config(
        json.loads(CLIENT_SECRETS_JSON),
        scopes=SCOPES
    )
    flow.redirect_uri = "https://cinafe.onrender.com/oauth2callback"

    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    return f"""
        <h2>Autorização concluída</h2>
        <p>Copie TODO o conteúdo abaixo e cole na variável <b>GOOGLE_DRIVE_TOKEN</b> no Render:</p>
        <textarea rows="15" cols="120">{creds.to_json()}</textarea>
    """

# ==================================================
# MAIN
# ==================================================
if __name__ == "__main__":
    app.run()
