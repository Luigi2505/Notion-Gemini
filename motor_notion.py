import os
import requests
import pyperclip
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

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


def extrair_texto_bloco(bloco):
    tipo = bloco.get("type")
    conteudo = bloco.get(tipo, {})

    textos = conteudo.get("rich_text", [])
    texto = "".join(t.get("plain_text", "") for t in textos)

    if not texto:
        return None

    prefixos = {
        "heading_1": "# ",
        "heading_2": "## ",
        "heading_3": "### ",
        "bulleted_list_item": "• ",
        "numbered_list_item": "- ",
        "to_do": "☐ " if not conteudo.get("checked") else "☑ ",
        "toggle": "▸ ",
        "quote": "> ",
    }

    prefixo = prefixos.get(tipo, "")
    return f"{prefixo}{texto}"


def buscar_blocos(page_id):
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    response = requests.get(url, headers=HEADERS)

    if response.status_code != 200:
        return []

    blocos = response.json().get("results", [])
    linhas = []

    for bloco in blocos:
        texto = extrair_texto_bloco(bloco)
        if texto:
            linhas.append(texto)

        # busca filhos recursivamente (subpaginas e toggles)
        if bloco.get("has_children"):
            filhos = buscar_blocos(bloco["id"])
            for filho in filhos:
                linhas.append("  " + filho)

    return linhas


def gerar_resumo():
    hoje = datetime.now().strftime("%d/%m/%Y %H:%M")
    linhas = [
        f"[CONTEXTO DAS MINHAS NOTAS NO NOTION — {hoje}]",
        "Use essas informações para me ajudar com organização, planejamento e tarefas do dia a dia.",
        ""
    ]

    for nome, page_id in PAGINAS.items():
        conteudo = buscar_blocos(page_id)
        linhas.append(f"=== {nome.upper()} ===")
        if conteudo:
            linhas.extend(conteudo)
        else:
            linhas.append("(sem conteúdo)")
        linhas.append("")

    return "\n".join(linhas)


if __name__ == "__main__":
    print("Buscando notas no Notion...")
    resumo = gerar_resumo()

    print("\n" + "="*50)
    print(resumo)
    print("="*50)

    try:
        pyperclip.copy(resumo)
        print("\n✅ Copiado para a área de transferência! Cole no Gemini.")
    except Exception:
        print("\n⚠️  Não foi possível copiar automaticamente. Copie o texto acima manualmente.")
