from flask import Flask, render_template, request, redirect, session, url_for
import psycopg2, psycopg2.extras
import os, json
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==================================================
# APP
# ==================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cinafe-secret")

# ==================================================
# BANCO DE DADOS (POSTGRES)
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
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS escolas (
        id SERIAL PRIMARY KEY,
        nome TEXT,
        codigo TEXT UNIQUE
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS solicitacoes (
        id SERIAL PRIMARY KEY,
        titulo TEXT,
        descricao TEXT,
        prazo DATE
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS envios (
        id SERIAL PRIMARY KEY,
        solicitacao_id INTEGER,
        escola TEXT,
        arquivo TEXT,
        link_drive TEXT,
        data_envio TIMESTAMP
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS comunicados (
        id SERIAL PRIMARY KEY,
        titulo TEXT,
        mensagem TEXT,
        data TIMESTAMP
    )""")

    cur.execute("""
    INSERT INTO users (username, password, role)
    VALUES ('admin', %s, 'admin')
    ON CONFLICT (username) DO NOTHING
    """, (generate_password_hash("admin123"),))

    conn.commit()
    conn.close()

init_db()

# ==================================================
# GOOGLE DRIVE - OAUTH
# ==================================================
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_CONFIG = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
TOKEN_FILE = "token.json"

def get_drive_service():
    if not os.path.exists(TOKEN_FILE):
        return None

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("drive", "v3", credentials=creds)

# ==================================================
# OAUTH ROTAS
# ==================================================
@app.route("/autorizar-google")
def autorizar_google():
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri="https://cinafe.onrender.com/oauth2callback"
    )

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )

    session["state"] = state
    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        state=session["state"],
        redirect_uri="https://cinafe.onrender.com/oauth2callback"
    )

    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    with open(TOKEN_FILE, "w") as token:
        token.write(creds.to_json())

    return redirect("/dashboard")

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
        conn.close()

        if user and check_password_hash(user["password"], request.form["password"]):
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect("/dashboard")

        return "Login inválido"

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

    cur.execute("SELECT * FROM comunicados ORDER BY id DESC")
    comunicados = cur.fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        solicitacoes=solicitacoes,
        comunicados=comunicados,
        role=session["role"]
    )

# ==================================================
# ENVIO DE ARQUIVO
# ==================================================
@app.route("/enviar/<int:id>", methods=["GET", "POST"])
def enviar(id):
    if session.get("role") != "escola":
        return redirect("/dashboard")

    drive = get_drive_service()
    if not drive:
        return redirect("/autorizar-google")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM solicitacoes WHERE id=%s", (id,))
    solicitacao = cur.fetchone()

    if request.method == "POST":
        file = request.files["arquivo"]
        filename = secure_filename(file.filename)
        temp_path = f"/tmp/{filename}"
        file.save(temp_path)

        media = MediaFileUpload(temp_path, resumable=False)
        uploaded = drive.files().create(
            body={"name": filename},
            media_body=media,
            fields="id, webViewLink"
        ).execute()

        cur.execute("""
            INSERT INTO envios (solicitacao_id, escola, arquivo, link_drive, data_envio)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            id,
            session["user"],
            filename,
            uploaded["webViewLink"],
            datetime.now()
        ))

        conn.commit()
        conn.close()
        os.remove(temp_path)

        return f"""
        <h3>Arquivo enviado com sucesso em {datetime.now().strftime('%d/%m/%Y às %H:%M')}</h3>
        <a href="/dashboard">Voltar</a>
        """

    conn.close()
    return render_template("enviar.html", solicitacao=solicitacao)
