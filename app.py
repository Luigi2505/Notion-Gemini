import os
import re
import json
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

app = Flask(__name__)

TOKEN = os.getenv("NOTION_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
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
    "Diário":    "📓",
}

PAGINA_DIARIO = os.getenv("NOTION_DIARIO_ID", "")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"
HISTORICO_FILE = "historico.json"

ultimo_estado = {
    "data": None,
    "paginas": {},
    "diario": [],
    "resumo_ollama": "",
    "sugestao_dia": "",
    "processando": False,
}


# ── Notion ──────────────────────────────────────────────

def extrair_texto_bloco(bloco):
    tipo = bloco.get("type")
    conteudo = bloco.get(tipo, {})
    textos = conteudo.get("rich_text", [])
    texto = "".join(t.get("plain_text", "") for t in textos)
    if not texto:
        return None, None

    prefixos = {
        "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
        "bulleted_list_item": "• ", "numbered_list_item": "- ",
        "to_do": "☐ " if not conteudo.get("checked") else "☑ ",
        "toggle": "▸ ", "quote": "> ",
    }
    prefixo = prefixos.get(tipo, "")
    return f"{prefixo}{texto}", tipo


def buscar_blocos(page_id):
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code != 200:
        return []

    itens = []
    for bloco in resp.json().get("results", []):
        texto, tipo = extrair_texto_bloco(bloco)
        if texto:
            itens.append({"texto": texto, "tipo": tipo, "nivel": 0})
        if bloco.get("has_children"):
            for filho in buscar_blocos(bloco["id"]):
                filho["nivel"] += 1
                itens.append(filho)
    return itens


def buscar_diario():
    if not PAGINA_DIARIO:
        return []

    itens = buscar_blocos(PAGINA_DIARIO)
    limite = datetime.now() - timedelta(days=7)
    resultado = []
    data_atual = None

    # padrões de data: "01/06", "01/06/2025", "1 de junho", etc.
    padrao_data = re.compile(
        r"\b(\d{1,2})[/\-\.](\d{1,2})(?:[/\-\.](\d{2,4}))?\b|"
        r"\b(\d{1,2})\s+de\s+\w+\b",
        re.IGNORECASE
    )

    for item in itens:
        texto = item["texto"]
        match = padrao_data.search(texto)
        if match:
            try:
                partes = texto.strip("# ").split("/")
                if len(partes) >= 2:
                    dia, mes = int(partes[0]), int(partes[1])
                    ano = int(partes[2]) if len(partes) > 2 else datetime.now().year
                    data_atual = datetime(ano, mes, dia)
            except Exception:
                pass

        if data_atual and data_atual >= limite:
            resultado.append({
                "texto": texto,
                "data": data_atual.strftime("%d/%m/%Y"),
                "nivel": item["nivel"]
            })

    return resultado


def adicionar_nota_notion(pagina_id, texto):
    url = f"https://api.notion.com/v1/blocks/{pagina_id}/children"
    payload = {
        "children": [{
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": texto}}]
            }
        }]
    }
    resp = requests.patch(url, headers=HEADERS, json=payload)
    return resp.status_code == 200


# ── Ollama ──────────────────────────────────────────────

def ollama_query(prompt):
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False
        }, timeout=60)
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"Erro Ollama: {e}")
    return ""


def gerar_resumo_ollama(paginas, diario):
    notas = []
    for nome, dados in paginas.items():
        if dados["itens"]:
            textos = [i["texto"] for i in dados["itens"]]
            notas.append(f"{nome}: {'; '.join(textos)}")

    diario_txt = ""
    if diario:
        entradas = [f"{e['data']} - {e['texto']}" for e in diario[-10:]]
        diario_txt = f"\nDiário recente:\n" + "\n".join(entradas)

    prompt = f"""Você é um assistente de organização pessoal. Analise as notas abaixo e gere:
1. Um resumo compacto em 3-4 frases das principais tarefas e projetos
2. Lista dos itens com prazo ou urgência detectados
3. Responda em português, seja direto e objetivo

Notas:
{chr(10).join(notas)}
{diario_txt}

Resposta:"""

    return ollama_query(prompt)


def gerar_sugestao_dia(paginas, diario):
    notas = []
    for nome, dados in paginas.items():
        if dados["itens"]:
            textos = [i["texto"] for i in dados["itens"]]
            notas.append(f"{nome}: {'; '.join(textos)}")

    diario_txt = ""
    if diario:
        hoje = datetime.now().strftime("%d/%m/%Y")
        entradas_hoje = [e["texto"] for e in diario if e["data"] == hoje]
        if entradas_hoje:
            diario_txt = f"\nO que já foi feito hoje: {'; '.join(entradas_hoje)}"

    prompt = f"""Hoje é {datetime.now().strftime('%A, %d/%m/%Y')}.
Baseado nas tarefas e notas abaixo, sugira de forma objetiva 3 prioridades para hoje.
Seja direto, máximo 3 linhas. Responda em português.

Tarefas:
{chr(10).join(notas)}
{diario_txt}

Sugestão para hoje:"""

    return ollama_query(prompt)


# ── Histórico ────────────────────────────────────────────

def salvar_historico(texto):
    historico = carregar_historico()
    entrada = {
        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "texto": texto
    }
    historico.insert(0, entrada)
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


# ── Contexto para copiar ─────────────────────────────────

def gerar_texto_copia(filtro=None):
    paginas = ultimo_estado["paginas"]
    diario = ultimo_estado["diario"]
    data = ultimo_estado["data"]

    linhas = [
        f"[CONTEXTO DAS MINHAS NOTAS NO NOTION — {data}]",
        "Use essas informações para me ajudar com organização, planejamento e tarefas do dia a dia.",
        ""
    ]

    if ultimo_estado["resumo_ollama"]:
        linhas.append("=== RESUMO INTELIGENTE ===")
        linhas.append(ultimo_estado["resumo_ollama"])
        linhas.append("")

    alvos = {filtro: paginas[filtro]} if filtro and filtro in paginas else paginas
    for nome, dados in alvos.items():
        linhas.append(f"=== {nome.upper()} ===")
        if dados["itens"]:
            for item in dados["itens"]:
                indent = "  " * item["nivel"]
                linhas.append(f"{indent}{item['texto']}")
        else:
            linhas.append("(sem conteúdo)")
        linhas.append("")

    if diario and not filtro:
        linhas.append("=== DIÁRIO (últimos 7 dias) ===")
        data_ant = None
        for entrada in diario:
            if entrada["data"] != data_ant:
                linhas.append(f"\n{entrada['data']}")
                data_ant = entrada["data"]
            linhas.append(f"  {entrada['texto']}")
        linhas.append("")

    return "\n".join(linhas)


# ── Atualização principal ─────────────────────────────────

def atualizar_tudo():
    global ultimo_estado
    ultimo_estado["processando"] = True
    print(f"[{datetime.now().strftime('%H:%M')}] Atualizando...")

    paginas = {}
    for nome, page_id in PAGINAS.items():
        itens = buscar_blocos(page_id)
        paginas[nome] = {
            "icone": ICONES[nome],
            "itens": itens,
            "tem_urgente": False,
        }

    diario = buscar_diario()
    resumo = gerar_resumo_ollama(paginas, diario)
    sugestao = gerar_sugestao_dia(paginas, diario)

    ultimo_estado.update({
        "data": datetime.now().strftime("%d/%m/%Y às %H:%M"),
        "paginas": paginas,
        "diario": diario,
        "resumo_ollama": resumo,
        "sugestao_dia": sugestao,
        "processando": False,
    })

    texto = gerar_texto_copia()
    salvar_historico(texto)
    print(f"[{datetime.now().strftime('%H:%M')}] Pronto.")


scheduler = BackgroundScheduler()
scheduler.add_job(atualizar_tudo, "cron", hour=8, minute=0)
scheduler.start()
atualizar_tudo()


# ── Rotas ─────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/estado")
def api_estado():
    return jsonify({
        "data": ultimo_estado["data"],
        "paginas": ultimo_estado["paginas"],
        "diario": ultimo_estado["diario"],
        "resumo_ollama": ultimo_estado["resumo_ollama"],
        "sugestao_dia": ultimo_estado["sugestao_dia"],
        "processando": ultimo_estado["processando"],
    })


@app.route("/api/copiar")
def api_copiar():
    filtro = request.args.get("filtro")
    texto = gerar_texto_copia(filtro)
    return jsonify({"texto": texto})


@app.route("/api/atualizar", methods=["POST"])
def api_atualizar():
    import threading
    threading.Thread(target=atualizar_tudo).start()
    return jsonify({"ok": True})


@app.route("/api/nota", methods=["POST"])
def api_nota():
    data = request.json
    texto = data.get("texto", "").strip()
    pagina = data.get("pagina", "")

    if not texto or pagina not in PAGINAS:
        return jsonify({"ok": False, "erro": "Dados inválidos"})

    ok = adicionar_nota_notion(PAGINAS[pagina], texto)
    return jsonify({"ok": ok})


@app.route("/api/historico")
def api_historico():
    return jsonify(carregar_historico())


if __name__ == "__main__":
    app.run(debug=True, port=5000)
