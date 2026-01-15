from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import os
from datetime import datetime

# =====================================
# APP
# =====================================
app = Flask(__name__)
app.secret_key = "cinafe_secret_key"

DATABASE_URL = os.environ.get("DATABASE_URL")

# =====================================
# BANCO DE DADOS (POSTGRESQL)
# =====================================
def get_db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS escolas (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            codigo TEXT UNIQUE NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes (
            id SERIAL PRIMARY KEY,
            titulo TEXT NOT NULL,
            descricao TEXT NOT NULL,
            prazo DATE NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS envios (
            id SERIAL PRIMARY KEY,
            solicitacao_id INTEGER REFERENCES solicitacoes(id),
            escola TEXT,
            arquivo TEXT,
            data_envio TIMESTAMP
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

    conn.commit()
    cur.close()
    conn.close()

def create_admin():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE username = %s", ("admin",))
    admin = cur.fetchone()

    if not admin:
        cur.execute(
            "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
            ("admin", generate_password_hash("admin123"), "admin")
        )
        conn.commit()

    cur.close()
    conn.close()

init_db()
create_admin()

# =====================================
# LOGIN
# =====================================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        cur.close()
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

# =====================================
# DASHBOARD
# =====================================
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

# =====================================
# COMUNICADOS
# =====================================
@app.route("/novo-comunicado", methods=["GET", "POST"])
def novo_comunicado():
    if session.get("role") != "admin":
        return redirect("/dashboard")

    if request.method == "POST":
        titulo = request.form["titulo"]
        mensagem = request.form["mensagem"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO comunicados (titulo, mensagem, data) VALUES (%s, %s, %s)",
            (titulo, mensagem, datetime.now())
        )
        conn.commit()
        cur.close()
        conn.close()

        return redirect("/dashboard")

    return render_template("novo_comunicado.html")

# =====================================
# CADASTRAR ESCOLA
# =====================================
@app.route("/criar-escola", methods=["GET", "POST"])
def criar_escola():
    if session.get("role") != "admin":
        return redirect("/")

    if request.method == "POST":
        nome = request.form["nome"]
        codigo = request.form["codigo"].lower()
        senha = f"{codigo}@123"

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO escolas (nome, codigo) VALUES (%s, %s)",
            (nome, codigo)
        )

        cur.execute(
            "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
            (codigo, generate_password_hash(senha), "escola")
        )

        conn.commit()
        cur.close()
        conn.close()

        return f"""
            <h3>Escola cadastrada</h3>
            <p>Login: {codigo}</p>
            <p>Senha: {senha}</p>
            <a href="/dashboard">Voltar</a>
        """

    return render_template("criar_escola.html")

# =====================================
# NOVA SOLICITAÇÃO
# =====================================
@app.route("/nova-solicitacao", methods=["GET", "POST"])
def nova_solicitacao():
    if session.get("role") != "admin":
        return redirect("/")

    if request.method == "POST":
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO solicitacoes (titulo, descricao, prazo) VALUES (%s, %s, %s)",
            (
                request.form["titulo"],
                request.form["descricao"],
                request.form["prazo"]
            )
        )

        conn.commit()
        cur.close()
        conn.close()

        return redirect("/dashboard")

    return render_template("nova_solicitacao.html")

# =====================================
# ENVIO DE ARQUIVO (SEM DRIVE)
# =====================================
@app.route("/enviar/<int:id>", methods=["GET", "POST"])
def enviar(id):
    if session.get("role") != "escola":
        return redirect("/")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM solicitacoes WHERE id = %s", (id,))
    solicitacao = cur.fetchone()

    if request.method == "POST":
        arquivo = request.files["arquivo"].filename

        cur.execute(
            "INSERT INTO envios (solicitacao_id, escola, arquivo, data_envio) VALUES (%s, %s, %s, %s)",
            (id, session["user"], arquivo, datetime.now())
        )

        conn.commit()
        cur.close()
        conn.close()

        return f"""
            <h3>Arquivo enviado com sucesso em {datetime.now().strftime('%d/%m/%Y às %H:%M')}</h3>
            <a href="/dashboard">Voltar</a>
        """

    cur.close()
    conn.close()

    return render_template("enviar.html", solicitacao=solicitacao)
