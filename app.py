from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import psycopg2
import psycopg2.extras
import os

# ==================================================
# APP
# ==================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cinafe_secret_key")

DATABASE_URL = os.environ.get("DATABASE_URL")

# ==================================================
# DATABASE
# ==================================================
def get_db():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL não configurada")

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )

# ==================================================
# LOGIN
# ==================================================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                "SELECT * FROM users WHERE username=%s",
                (request.form["username"],)
            )
            user = cur.fetchone()

            if user and check_password_hash(user["password"], request.form["password"]):
                session["user"] = user["username"]
                session["role"] = user["role"]
                return redirect("/dashboard")

            return "Usuário ou senha inválidos"

        finally:
            conn.close()

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

    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM solicitacoes ORDER BY id DESC")
        solicitacoes = cur.fetchall()

        cur.execute("SELECT * FROM comunicados ORDER BY id DESC")
        comunicados = cur.fetchall()

        return render_template(
            "dashboard.html",
            role=session["role"],
            solicitacoes=solicitacoes,
            comunicados=comunicados
        )

    finally:
        conn.close()

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

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO comunicados (titulo, mensagem, data) VALUES (%s, %s, NOW())",
            (titulo, mensagem)
        )
        conn.commit()
        cur.close()
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
        try:
            conn = get_db()
            cur = conn.cursor()

            codigo = request.form["codigo"].lower()
            senha = f"{codigo}@123"

            cur.execute(
                "INSERT INTO escolas (nome, codigo) VALUES (%s, %s)",
                (request.form["nome"], codigo)
            )

            cur.execute(
                "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
                (codigo, generate_password_hash(senha), "escola")
            )

            conn.commit()

            return f"""
                <h3>Escola cadastrada</h3>
                <p>Login: {codigo}</p>
                <p>Senha: {senha}</p>
                <a href="/dashboard">Voltar</a>
            """

        finally:
            conn.close()

    return render_template("criar_escola.html")

# ==================================================
# SOLICITAÇÕES
# ==================================================
@app.route("/nova-solicitacao", methods=["GET", "POST"])
def nova_solicitacao():
    if session.get("role") != "admin":
        return redirect("/")

    if request.method == "POST":
        try:
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
            return redirect("/dashboard")

        finally:
            conn.close()

    return render_template("nova_solicitacao.html")

# ==================================================
# CONTROLE DA SECRETARIA
# ==================================================
@app.route("/controle/<int:solicitacao_id>")
def controle(solicitacao_id):
    if session.get("role") != "admin":
        return redirect("/")

    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT * FROM solicitacoes WHERE id=%s",
            (solicitacao_id,)
        )
        solicitacao = cur.fetchone()

        cur.execute("SELECT codigo FROM escolas")
        escolas = cur.fetchall()

        cur.execute(
            "SELECT * FROM envios WHERE solicitacao_id=%s",
            (solicitacao_id,)
        )
        envios = cur.fetchall()

        envios_dict = {e["escola"]: e for e in envios}
        prazo = solicitacao["prazo"]
        hoje = datetime.now().date()

        resultado = []

        for e in escolas:
            codigo = e["codigo"]

            if codigo in envios_dict:
                envio = envios_dict[codigo]
                data_envio = envio["data_envio"].date()
                status = "🟢 Enviado" if data_envio <= prazo else "🔴 Fora do prazo"
                link = envio["link_drive"]
            else:
                status = "🟡 Pendente" if hoje <= prazo else "🔴 Em atraso"
                link = None

            resultado.append({
                "escola": codigo,
                "status": status,
                "link": link
            })

        return render_template(
            "controle.html",
            solicitacao=solicitacao,
            resultado=resultado
        )

    finally:
        conn.close()

# ==================================================
# ENVIO DA ESCOLA
# ==================================================
@app.route("/enviar/<int:solicitacao_id>", methods=["GET", "POST"])
def enviar(solicitacao_id):
    if session.get("role") != "escola":
        return redirect("/")

    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT * FROM solicitacoes WHERE id=%s",
            (solicitacao_id,)
        )
        solicitacao = cur.fetchone()

        if request.method == "POST":
            arquivo = request.files["arquivo"]
            filename = secure_filename(arquivo.filename)

            cur.execute(
                """
                INSERT INTO envios
                (solicitacao_id, escola, arquivo, data_envio)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    solicitacao_id,
                    session["user"],
                    filename,
                    datetime.now()
                )
            )

            conn.commit()

            return f"""
                <h3>Arquivo enviado com sucesso em {datetime.now().strftime('%d/%m/%Y às %H:%M')}</h3>
                <a href="/dashboard">Voltar</a>
            """

        return render_template(
            "enviar.html",
            solicitacao=solicitacao
        )

    finally:
        conn.close()
