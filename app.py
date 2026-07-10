import os
import json
import requests
import functools
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "troca-isso-em-producao")

# ── Config ────────────────────────────────────────────────
NOTION_TOKEN  = os.getenv("NOTION_TOKEN")
GEMINI_KEY    = os.getenv("GEMINI_API_KEY")
APP_PASSWORD  = os.getenv("APP_PASSWORD", "admin")
DIARIO_ID     = os.getenv("NOTION_DIARIO_ID", "")
HISTORICO_FILE = "historico.json"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

PAGINAS = {
    "Faculdade": "380af00afe0b805f856dfb0191c75274",
    "Pessoal":   "380af00afe0b80129b5dc83f754a6301",
    "Academia":  "380af00afe0b80258d1ddb3d0474061c",
    "Projetos":  "380af00afe0b8065b405f368840a97e9",
}

ICONES = {
    "Faculdade": "🎓",
    "Pessoal":   "👤",
    "Academia":  "💪",
    "Projetos":  "🚀",
}

ultimo_estado = {
    "data": None,
    "paginas": {},
    "diario": [],
    "resumo": "",
    "sugestao": "",
    "processando": False,
}


# ── Auth ──────────────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("autenticado"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    erro = None
    if request.method == "POST":
        senha = request.form.get("senha", "")
        if senha == APP_PASSWORD:
            session["autenticado"] = True
            return redirect(url_for("index"))
        erro = "Senha incorreta."
    return render_template("login.html", erro=erro)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Notion ────────────────────────────────────────────────
def extrair_texto_bloco(bloco):
    tipo = bloco.get("type")
    conteudo = bloco.get(tipo, {})
    textos = conteudo.get("rich_text", [])
    texto = "".join(t.get("plain_text", "") for t in textos)
    if not texto:
        return None

    prefixos = {
        "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
        "bulleted_list_item": "• ", "numbered_list_item": "- ",
        "to_do": "☐ " if not conteudo.get("checked") else "☑ ",
        "toggle": "▸ ", "quote": "> ",
    }
    return prefixos.get(tipo, "") + texto


def buscar_blocos(page_id):
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    resp = requests.get(url, headers=NOTION_HEADERS)
    if resp.status_code != 200:
        return []

    itens = []
    for bloco in resp.json().get("results", []):
        texto = extrair_texto_bloco(bloco)
        if texto:
            itens.append({"texto": texto, "nivel": 0})
        if bloco.get("has_children"):
            for filho in buscar_blocos(bloco["id"]):
                filho["nivel"] += 1
                itens.append(filho)
    return itens


def buscar_diario():
    if not DIARIO_ID:
        return []

    itens = buscar_blocos(DIARIO_ID)
    limite = datetime.now() - timedelta(days=7)
    resultado = []
    data_atual = None

    import re
    padrao = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b")

    for item in itens:
        match = padrao.search(item["texto"])
        if match:
            try:
                dia, mes = int(match.group(1)), int(match.group(2))
                ano = int(match.group(3)) if match.group(3) else datetime.now().year
                if ano < 100:
                    ano += 2000
                data_atual = datetime(ano, mes, dia)
            except Exception:
                pass

        if data_atual and data_atual >= limite:
            resultado.append({
                "texto": item["texto"],
                "data": data_atual.strftime("%d/%m/%Y"),
                "nivel": item["nivel"]
            })

    return resultado


def adicionar_nota_notion(pagina_id, texto):
    url = f"https://api.notion.com/v1/blocks/{pagina_id}/children"
    payload = {"children": [{
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"type": "text", "text": {"content": texto}}]
        }
    }]}
    return requests.patch(url, headers=NOTION_HEADERS, json=payload).status_code == 200


# ── Gemini API ────────────────────────────────────────────
def gemini_query(prompt):
    if not GEMINI_KEY:
        return ""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            candidates = resp.json().get("candidates", [])
            if candidates:
                return candidates[0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"Erro Gemini: {e}")
    return ""


def gerar_resumo(paginas, diario):
    notas = []
    for nome, dados in paginas.items():
        if dados["itens"]:
            textos = [i["texto"] for i in dados["itens"]]
            notas.append(f"{nome}: {'; '.join(textos)}")

    diario_txt = ""
    if diario:
        entradas = [f"{e['data']} - {e['texto']}" for e in diario[-10:]]
        diario_txt = "\nDiário recente:\n" + "\n".join(entradas)

    prompt = f"""Você é um assistente de organização pessoal. Analise as notas abaixo e gere:
1. Um resumo compacto em 3-4 frases das principais tarefas e projetos
2. Liste os itens com prazo ou urgência detectados

Responda em português, seja direto e objetivo. Máximo 150 palavras.

Notas:
{chr(10).join(notas)}
{diario_txt}"""

    return gemini_query(prompt)


def gerar_sugestao(paginas, diario):
    notas = []
    for nome, dados in paginas.items():
        if dados["itens"]:
            textos = [i["texto"] for i in dados["itens"]]
            notas.append(f"{nome}: {'; '.join(textos)}")

    hoje_str = datetime.now().strftime("%d/%m/%Y")
    diario_txt = ""
    if diario:
        hoje = [e["texto"] for e in diario if e["data"] == hoje_str]
        if hoje:
            diario_txt = f"\nJá feito hoje: {'; '.join(hoje)}"

    prompt = f"""Hoje é {datetime.now().strftime('%A, %d/%m/%Y')}.
Baseado nas tarefas abaixo, sugira 3 prioridades para hoje de forma objetiva.
Máximo 3 linhas. Responda em português.

Tarefas:
{chr(10).join(notas)}
{diario_txt}"""

    return gemini_query(prompt)


# ── Histórico ─────────────────────────────────────────────
def salvar_historico(texto):
    historico = carregar_historico()
    historico.insert(0, {"data": datetime.now().strftime("%d/%m/%Y %H:%M"), "texto": texto})
    historico = historico[:7]
    with open(HISTORICO_FILE, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=2)


def carregar_historico():
    if not os.path.exists(HISTORICO_FILE):
        return []
    try:
        with open(HISTORICO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ── Contexto para copiar ──────────────────────────────────
def gerar_texto_copia(filtro=None):
    paginas = ultimo_estado["paginas"]
    diario  = ultimo_estado["diario"]

    linhas = [
        f"[CONTEXTO DAS MINHAS NOTAS NO NOTION — {ultimo_estado['data']}]",
        "Use essas informações para me ajudar com organização, planejamento e tarefas do dia a dia.",
        ""
    ]

    if ultimo_estado["resumo"] and not filtro:
        linhas += ["=== RESUMO INTELIGENTE ===", ultimo_estado["resumo"], ""]

    alvos = {filtro: paginas[filtro]} if filtro and filtro in paginas else paginas
    for nome, dados in alvos.items():
        linhas.append(f"=== {nome.upper()} ===")
        linhas.extend([("  " * i["nivel"]) + i["texto"] for i in dados["itens"]] or ["(sem conteúdo)"])
        linhas.append("")

    if diario and not filtro:
        linhas.append("=== DIÁRIO (últimos 7 dias) ===")
        data_ant = None
        for e in diario:
            if e["data"] != data_ant:
                linhas.append(f"\n{e['data']}")
                data_ant = e["data"]
            linhas.append(f"  {e['texto']}")
        linhas.append("")

    return "\n".join(linhas)


# ── Atualização ───────────────────────────────────────────
def atualizar_tudo():
    global ultimo_estado
    ultimo_estado["processando"] = True
    print(f"[{datetime.now().strftime('%H:%M')}] Atualizando...")

    paginas = {}
    for nome, page_id in PAGINAS.items():
        itens = buscar_blocos(page_id)
        paginas[nome] = {"icone": ICONES[nome], "itens": itens}

    diario  = buscar_diario()
    resumo  = gerar_resumo(paginas, diario)
    sugestao = gerar_sugestao(paginas, diario)

    ultimo_estado.update({
        "data": datetime.now().strftime("%d/%m/%Y às %H:%M"),
        "paginas": paginas,
        "diario": diario,
        "resumo": resumo,
        "sugestao": sugestao,
        "processando": False,
    })

    salvar_historico(gerar_texto_copia())
    print(f"[{datetime.now().strftime('%H:%M')}] Pronto.")


scheduler = BackgroundScheduler()
scheduler.add_job(atualizar_tudo, "cron", hour=8, minute=0)
scheduler.start()
atualizar_tudo()


# ── Rotas ─────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/estado")
@login_required
def api_estado():
    return jsonify(ultimo_estado)


@app.route("/api/copiar")
@login_required
def api_copiar():
    filtro = request.args.get("filtro")
    return jsonify({"texto": gerar_texto_copia(filtro)})


@app.route("/api/atualizar", methods=["POST"])
@login_required
def api_atualizar():
    import threading
    threading.Thread(target=atualizar_tudo).start()
    return jsonify({"ok": True})


@app.route("/api/nota", methods=["POST"])
@login_required
def api_nota():
    data = request.json
    texto  = data.get("texto", "").strip()
    pagina = data.get("pagina", "")
    if not texto or pagina not in PAGINAS:
        return jsonify({"ok": False})
    return jsonify({"ok": adicionar_nota_notion(PAGINAS[pagina], texto)})


@app.route("/api/historico")
@login_required
def api_historico():
    return jsonify(carregar_historico())


if __name__ == "__main__":
    app.run(debug=False, port=5000)
