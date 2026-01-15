from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import psycopg2, psycopg2.extras
import os, json
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
app.secret_key = os.environ.get("SECRET_KEY", "cinafe_secret")

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
    cur.close()
    conn.close()

init_db()

# ==================================================
# GOOGLE DRIVE (TOKEN GLOBAL - INSTITUCIONAL)
# ==================================================
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_JSON = json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON"))
TOKEN_FILE = "token.json"

def get_drive_service():
    if not os.path.exists(TOKEN_FILE):
        raise Exception("Google Drive ainda não autorizado")

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)

def get_or_create_folder(drive, name, parent_id=None):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        q += f" and '{parent_id}' in parents"

    result = drive.files().list(
        q=q,
        spaces="drive",
        fields="files(id)"
    ).execute()

    files = result.get("files", [])
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

# ==================================================
# GOOGLE OAUTH (APENAS ADMIN)
# ==================================================
@app.route("/autorizar-google")
def autorizar_google():
    if session.get("role") != "admin":
        return redirect("/dashboard")

    flow = Flow.from_client_config(
        CLIENT_JSON,
        scopes=SCOPES,
        redirect_uri=url_for("oauth_callback", _external=True)
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent"
    )

    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth_callback():
    flow = Flow.from_client_config(
        CLIENT_JSON,
        scopes=SCOPES,
        redirect_uri=url_for("oauth_callback", _external=True)
    )

    flow.fetch_token(authorization_response=request.url)

    with open(TOKEN_FILE, "w") as token:
        token.write(flow.credentials.to_json())

    return redirect("/dashboard")

# ==================================================
# LOGIN
# ==================================================
@app.route("/", methods=["GET","POST"])
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
    cur = conn.cursor()

    cur.execute("SELECT * FROM solicitacoes ORDER BY id DESC")
    solicitacoes = cur.fetchall()

    cur.execute("SELECT * FROM comunicados ORDER BY data DESC")
    comunicados = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "dashboard.html",
        role=session["role"],
        solicitacoes=solicitacoes,
        comunicados=comunicados
    )

# ==================================================
# ENVIO DE ARQUIVO (ESCOLA - SEM OAUTH)
# ==================================================
@app.route("/enviar/<int:id>", methods=["GET","POST"])
def enviar(id):
    if session.get("role") != "escola":
        return redirect("/dashboard")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM solicitacoes WHERE id=%s", (id,))
    solicitacao = cur.fetchone()

    if request.method == "POST":
        try:
            drive = get_drive_service()
        except Exception:
            return "<h3>O Google Drive ainda não foi autorizado pela secretaria.</h3>"

        file = request.files["arquivo"]
        filename = secure_filename(file.filename)
        temp_path = f"/tmp/{filename}"
        file.save(temp_path)

        pasta_ano = get_or_create_folder(drive, "2026")
        pasta_solic = get_or_create_folder(drive, "SOLICITACOES", pasta_ano)
        pasta_titulo = get_or_create_folder(drive, solicitacao["titulo"], pasta_solic)
        pasta_escola = get_or_create_folder(drive, session["user"], pasta_titulo)

        media = MediaFileUpload(temp_path)
        uploaded = drive.files().create(
            body={"name": filename, "parents": [pasta_escola]},
            media_body=media,
            fields="webViewLink"
        ).execute()

        cur.execute("""
            INSERT INTO envios (solicitacao_id, escola, arquivo, link_drive, data_envio)
            VALUES (%s,%s,%s,%s,%s)
        """, (
            id,
            session["user"],
            filename,
            uploaded["webViewLink"],
            datetime.now()
        ))

        conn.commit()
        cur.close()
        conn.close()
        os.remove(temp_path)

        return f"""
        <h3>Arquivo enviado com sucesso em {datetime.now().strftime('%d/%m/%Y às %H:%M')}</h3>
        <a href="/dashboard">Voltar ao painel</a>
        """

    cur.close()
    conn.close()
    return render_template("enviar.html", solicitacao=solicitacao)
