from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import psycopg2
import psycopg2.extras
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
# DATABASE (POSTGRES)
# ==================================================
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS escolas (
            id SERIAL PRIMARY KEY,
            nome TEXT,
            codigo TEXT UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes (
            id SERIAL PRIMARY KEY,
            titulo TEXT,
            descricao TEXT,
            prazo DATE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS envios (
            id SERIAL PRIMARY KEY,
            solicitacao_id INTEGER,
            escola TEXT,
            arquivo TEXT,
            link_drive TEXT,
            data_envio TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS comunicados (
            id SERIAL PRIMARY KEY,
            titulo TEXT,
            mensagem TEXT,
            data TIMESTAMP
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

def create_admin():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, password, role) VALUES (%s,%s,%s)",
            ("admin", generate_password_hash("admin123"), "admin")
        )
        conn.commit()
    cur.close()
    conn.close()

init_db()
create_admin()

# ==================================================
# GOOGLE DRIVE - OAUTH
# ==================================================
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_CONFIG = json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON"))
TOKEN_FILE = "token.json"

def get_drive_service():
    if not os.path.exists(TOKEN_FILE):
        raise Exception("Google Drive não autorizado")

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)

@app.route("/autorizar-google")
def autorizar_google():
    if session.get("role") != "admin":
        return redirect("/dashboard")

    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=url_for("oauth2callback", _external=True)
    )

    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent"
    )

    session["oauth_state"] = state
    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        state=session.get("oauth_state"),
        redirect_uri=url_for("oauth2callback", _external=True)
    )

    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials
    with open(TOKEN_FILE, "w") as token:
        token.write(creds.to_json())

    return redirect("/dashboard")

# ==================================================
# DRIVE HELPERS
# ==================================================
def get_or_create_folder(service, name, parent=None):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    if parent:
        q += f" and '{parent}' in parents"

    res = service.files().list(q=q, spaces="drive", fields="files(id)").execute()
    files = res.get("files", [])

    if files:
        return files[0]["id"]

    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        metadata["parents"] = [parent]

    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]

def upload_to_drive(file_path, file_name, solicitacao, escola):
    service = get_drive_service()

    ano = get_or_create_folder(service, "2026")
    solicitacoes = get_or_create_folder(service, "SOLICITACOES", ano)
    pasta_solic = get_or_create_folder(service, solicitacao, solicitacoes)
    pasta_escola = get_or_create_folder(service, escola, pasta_solic)

    media = MediaFileUpload(file_path)
    file = service.files().create(
        body={"name": file_name, "parents": [pasta_escola]},
        media_body=media,
        fields="webViewLink"
    ).execute()

    return file["webViewLink"]

# ==================================================
# LOGIN
# ==================================================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (request.form["username"],))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user["password"], request.form["password"]):
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect("/dashboard")

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
    cur = conn.cursor()
    cur.execute("SELECT * FROM solicitacoes ORDER BY id DESC")
    solicitacoes = cur.fetchall()
    cur.execute("SELECT * FROM comunicados ORDER BY data DESC")
    comunicados = cur.fetchall()
    cur.close()
    conn.close()

    return render_template(
        "dashboard.html",
        solicitacoes=solicitacoes,
        comunicados=comunicados,
        role=session["role"],
        drive_ok=os.path.exists(TOKEN_FILE)
    )

# ==================================================
# ENVIO DE ARQUIVOS (ESCOLA)
# ==================================================
@app.route("/enviar/<int:id>", methods=["GET", "POST"])
def enviar(id):
    if session.get("role") != "escola":
        return redirect("/dashboard")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM solicitacoes WHERE id=%s", (id,))
    solicitacao = cur.fetchone()

    if request.method == "POST":
        if not os.path.exists(TOKEN_FILE):
            return "Google Drive ainda não autorizado pela secretaria."

        file = request.files["arquivo"]
        filename = secure_filename(file.filename)
        path = f"/tmp/{filename}"
        file.save(path)

        link = upload_to_drive(
            path,
            filename,
            solicitacao["titulo"],
            session["user"]
        )

        cur.execute("""
            INSERT INTO envios (solicitacao_id, escola, arquivo, link_drive, data_envio)
            VALUES (%s,%s,%s,%s,%s)
        """, (id, session["user"], filename, link, datetime.now()))

        conn.commit()
        os.remove(path)

        return f"Arquivo enviado com sucesso em {datetime.now().strftime('%d/%m/%Y às %H:%M')}"

    cur.close()
    conn.close()
    return render_template("enviar.html", solicitacao=solicitacao)
