from flask import Flask, render_template, request, redirect, session
import psycopg2
import psycopg2.extras
import os
import json
from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==================================================
# APP
# ==================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cinafe_secret")

# ==================================================
# DATABASE
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
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS escolas (
        id SERIAL PRIMARY KEY,
        nome TEXT,
        codigo TEXT UNIQUE
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS solicitacoes (
        id SERIAL PRIMARY KEY,
        titulo TEXT,
        descricao TEXT,
        prazo DATE
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS comunicados (
        id SERIAL PRIMARY KEY,
        titulo TEXT,
        mensagem TEXT,
        data TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS envios (
        id SERIAL PRIMARY KEY,
        solicitacao_id INTEGER,
        escola TEXT,
        arquivo TEXT,
        link_drive TEXT,
        data_envio TIMESTAMP
    );
    """)

    conn.commit()
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
    conn.close()

init_db()
create_admin()

# ==================================================
# GOOGLE DRIVE (TOKEN VIA ENV)
# ==================================================
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
PASTA_ANO = "2026"
PASTA_SOLIC = "SOLICITACOES"

GOOGLE_TOKEN_JSON = os.environ.get("GOOGLE_TOKEN_JSON")

def get_drive_service():
    if not GOOGLE_TOKEN_JSON:
        raise Exception("Google Drive não autorizado pela secretaria.")

    token_info = json.loads(GOOGLE_TOKEN_JSON)
    creds = Credentials.from_authorized_user_info(token_info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)

def get_or_create_folder(name, parent_id=None):
    drive = get_drive_service()

    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    res = drive.files().list(q=query, fields="files(id,name)").execute()
    if res["files"]:
        return res["files"][0]["id"]

    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]

    folder = drive.files().create(body=body, fields="id").execute()
    return folder["id"]

def upload_to_drive(path, filename, solicitacao, escola):
    drive = get_drive_service()

    ano_id = get_or_create_folder(PASTA_ANO)
    raiz = get_or_create_folder(PASTA_SOLIC, ano_id)
    pasta_solic = get_or_create_folder(solicitacao, raiz)
    pasta_escola = get_or_create_folder(escola, pasta_solic)

    media = MediaFileUpload(path, resumable=False)
    file = drive.files().create(
        body={"name": filename, "parents": [pasta_escola]},
        media_body=media,
        fields="webViewLink"
    ).execute()

    return file["webViewLink"]

# ==================================================
# LOGIN / LOGOUT
# ==================================================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect("/dashboard")

        return render_template("login.html", erro="Usuário ou senha inválidos")

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
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO comunicados (titulo, mensagem, data) VALUES (%s,%s,%s)",
            (request.form["titulo"], request.form["mensagem"], datetime.now())
        )
        conn.commit()
        conn.close()
        return redirect("/dashboard")

    return render_template("novo_comunicado.html")

# ==================================================
# ESCOLAS
# ==================================================
@app.route("/criar-escola", methods=["GET", "POST"])
def criar_escola():
    if session.get("role") != "admin":
        return redirect("/")

    if request.method == "POST":
        codigo = request.form["codigo"].lower()
        senha = f"{codigo}@123"

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO escolas (nome, codigo) VALUES (%s,%s)",
            (request.form["nome"], codigo)
        )

        cur.execute(
            "INSERT INTO users (username,password,role) VALUES (%s,%s,%s)",
            (codigo, generate_password_hash(senha), "escola")
        )

        conn.commit()
        conn.close()

        return f"Login: {codigo} | Senha: {senha}"

    return render_template("criar_escola.html")

# ==================================================
# SOLICITAÇÕES
# ==================================================
@app.route("/nova-solicitacao", methods=["GET", "POST"])
def nova_solicitacao():
    if session.get("role") != "admin":
        return redirect("/")

    if request.method == "POST":
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO solicitacoes (titulo,descricao,prazo) VALUES (%s,%s,%s)",
            (request.form["titulo"], request.form["descricao"], request.form["prazo"])
        )
        conn.commit()
        conn.close()
        return redirect("/dashboard")

    return render_template("nova_solicitacao.html")

# ==================================================
# ENVIO (ESCOLA)
# ==================================================
@app.route("/enviar/<int:id>", methods=["GET", "POST"])
def enviar(id):
    if session.get("role") != "escola":
        return redirect("/dashboard")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM solicitacoes WHERE id=%s", (id,))
    solicitacao = cur.fetchone()

    if not solicitacao:
        conn.close()
        return "Solicitação não encontrada", 404

    if request.method == "POST":
        file = request.files.get("arquivo")
        if not file or file.filename == "":
            conn.close()
            return "Arquivo inválido", 400

        filename = secure_filename(file.filename)
        temp_path = f"/tmp/{filename}"

        try:
            file.save(temp_path)

            data_envio = datetime.now()
            link = upload_to_drive(
                temp_path,
                filename,
                solicitacao["titulo"],
                session["user"]
            )

            cur.execute("""
                INSERT INTO envios
                (solicitacao_id, escola, arquivo, link_drive, data_envio)
                VALUES (%s,%s,%s,%s,%s)
            """, (
                id,
                session["user"],
                filename,
                link,
                data_envio
            ))

            conn.commit()
            os.remove(temp_path)

            msg = f"Arquivo enviado com sucesso em {data_envio.strftime('%d/%m/%Y às %H:%M')}"
            return render_template("envio_sucesso.html", mensagem=msg)

        except Exception as e:
            conn.rollback()
            return f"<pre>ERRO NO ENVIO:\n{str(e)}</pre>", 500

        finally:
            conn.close()

    conn.close()
    return render_template("enviar.html", solicitacao=solicitacao)
