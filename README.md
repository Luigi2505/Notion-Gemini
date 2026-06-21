# Notion → Gemini Context

Script que puxa suas notas do Notion e copia um resumo formatado para a área de transferência, pronto para colar no Gemini.

## Setup

### 1. Instalar dependências
```bash
pip install -r requirements.txt
```

### 2. Configurar o token
Abra o arquivo `.env` e substitua `cole_seu_token_aqui` pelo seu token do Notion:
```
NOTION_TOKEN=ntn_seutokenaqui
```

### 3. Conectar a integração às páginas
Isso é obrigatório — sem isso a API não consegue ler as páginas.

Para cada uma das 4 páginas (Faculdade, Pessoal, Academia, Projetos):
1. Abra a página no Notion
2. Clique em "..." no canto superior direito
3. Vá em "Connect to" (ou "Conexões")
4. Selecione a integração que você criou

### 4. Rodar
```bash
python resumo_notion.py
```

O resumo será impresso no terminal e copiado automaticamente para a área de transferência.
Cole no início da conversa no Gemini.

## Dica
Cole sempre no início de uma nova conversa com o Gemini, antes de fazer qualquer pergunta.
O Gemini vai usar o contexto automaticamente para te ajudar com organização e tarefas.
