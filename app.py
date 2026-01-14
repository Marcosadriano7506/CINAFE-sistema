from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3

app = Flask(__name__)
app.secret_key = "cinafe_secret_key"

# =========================
# BANCO DE DADOS
# =========================
def get_db():
    conn = sqlite3.connect("cinafe.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    # Tabela de usuários
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)

    # Tabela de escolas
    conn.execute("""
        CREATE TABLE IF NOT EXISTS escolas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            codigo TEXT UNIQUE NOT NULL
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


# Inicialização automática
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

    return render_template(
        "dashboard.html",
        role=session["role"]
    )


@app.route("/criar-escola", methods=["GET", "POST"])
def criar_escola():
    if "user" not in session or session["role"] != "admin":
        return redirect("/")

    if request.method == "POST":
        nome = request.form["nome"]
        codigo = request.form["codigo"]

        conn = get_db()

        # Cadastra escola
        conn.execute(
            "INSERT INTO escolas (nome, codigo) VALUES (?, ?)",
            (nome, codigo)
        )

        # Cria usuário da escola automaticamente
        senha_inicial = generate_password_hash(codigo + "@123")
        conn.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            (codigo, senha_inicial, "escola")
        )

        conn.commit()
        conn.close()

        return f"""
            <h3>Escola cadastrada com sucesso</h3>
            <p><strong>Login da escola:</strong> {codigo}</p>
            <p><strong>Senha inicial:</strong> {codigo}@123</p>
            <a href="/dashboard">Voltar ao painel</a>
        """

    return """
        <h2>Cadastrar Escola</h2>
        <form method="POST">
            <input name="nome" placeholder="Nome da escola" required><br><br>
            <input name="codigo" placeholder="Código da escola" required><br><br>
            <button type="submit">Cadastrar</button>
        </form>
        <br>
        <a href="/dashboard">Voltar</a>
    """


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")
