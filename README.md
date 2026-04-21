# IAL Chatbot — Backend

Backend do assistente virtual da **IAL Corretora de Seguros**, construído com Flask e integrado ao modelo **Llama 4 Scout** via Groq API. Suporta conversas em texto, análise de imagens e leitura de documentos (PDF, DOCX).

---

## Repositório

**GitHub:** https://github.com/pronatan/ial-chatbot-backend  
**Produção:** https://api.ialcorretora.com.br  
**Frontend:** https://ialcorretora.com.br

---

## Stack

| Tecnologia | Uso |
|---|---|
| Python 3.11 | Linguagem |
| Flask 3.0 | Framework web |
| Groq API | Inferência do modelo de IA |
| Llama 4 Scout 17B | Modelo multimodal (texto + imagens) |
| pdfplumber | Extração de texto de PDFs |
| python-docx | Extração de texto de arquivos DOCX |
| Pillow | Processamento de imagens |
| Gunicorn | Servidor WSGI para produção |
| Render.com | Hospedagem do backend (free tier) |

---

## Arquitetura

```
Frontend (Hostgator)          Backend (Render)
ialcorretora.com.br    →      api.ialcorretora.com.br
chatbot-backend.js            app.py (Flask)
                                  ↓
                              Groq API
                              Llama 4 Scout 17B
                              (multimodal)
```

O frontend é estático (HTML/CSS/JS) hospedado no Hostgator. O backend é uma API REST separada hospedada no Render, que recebe as mensagens do chat, processa arquivos e consulta o modelo de IA.

---

## Endpoints

### `GET /api/health`
Verifica se o backend está no ar.

**Resposta:**
```json
{
  "status": "ok",
  "message": "Backend IAL funcionando!",
  "model": "meta-llama/llama-4-scout-17b-16e-instruct",
  "multimodal": true,
  "suporta": ["texto", "imagens (jpg/png/webp)", "PDF", "DOCX", "TXT"]
}
```

---

### `POST /api/chat`
Envia uma mensagem (e opcionalmente um arquivo) para o assistente.

**Aceita dois formatos:**

#### Só texto — `application/json`
```json
{
  "message": "O que é proteção veicular?",
  "session_id": "usuario_123"
}
```

#### Com arquivo — `multipart/form-data`
| Campo | Tipo | Descrição |
|---|---|---|
| `message` | string | Texto da mensagem (opcional se houver arquivo) |
| `session_id` | string | ID da sessão para manter histórico |
| `file` | file | Imagem (jpg/png/webp), PDF, DOCX ou TXT — máx. 10MB |

**Resposta:**
```json
{
  "response": "A proteção veicular é uma alternativa ao seguro tradicional...",
  "session_id": "usuario_123"
}
```

**Como o arquivo é processado:**
- **Imagem** → convertida para base64 e enviada diretamente ao modelo multimodal
- **PDF** → texto extraído com `pdfplumber` e enviado como contexto
- **DOCX** → texto extraído com `python-docx` e enviado como contexto
- **TXT** → lido como texto puro

---

### `POST /api/clear-session`
Limpa o histórico de uma sessão.

```json
{
  "session_id": "usuario_123"
}
```

---

## Rodando localmente

### 1. Clone o repositório
```bash
git clone https://github.com/pronatan/ial-chatbot-backend.git
cd ial-chatbot-backend
```

### 2. Crie o ambiente virtual
```bash
python -m venv venv
venv\Scripts\activate      # Windows
source venv/bin/activate   # Linux/Mac
```

### 3. Instale as dependências
```bash
pip install -r requirements.txt
```

### 4. Configure a chave da API
Crie um arquivo `.env` na raiz do projeto:
```
GROQ_API_KEY=gsk_sua_chave_aqui
```

Obtenha sua chave gratuita em: https://console.groq.com → API Keys

### 5. Inicie o servidor
```bash
python app.py
```

O servidor sobe em `http://localhost:5000`.

---

## Deploy no Render

O backend está configurado para deploy automático no Render via `Procfile` e `render.yaml`.

### Passos para novo deploy:

1. Acesse https://render.com e faça login com GitHub
2. Clique em **New + → Web Service**
3. Conecte o repositório `pronatan/ial-chatbot-backend`
4. Configure:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance Type:** Free
5. Em **Environment → Add Variable:**
   - `GROQ_API_KEY` = sua chave Groq
6. Clique **Create Web Service**

> **Atenção:** O plano free do Render hiberna após 15 minutos de inatividade. A primeira requisição após hibernação pode demorar ~50 segundos para acordar o servidor.

---

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GROQ_API_KEY` | ✅ Sim | Chave da API Groq (https://console.groq.com) |

---

## Modelo de IA

**`meta-llama/llama-4-scout-17b-16e-instruct`**

- Multimodal: aceita texto e imagens
- Gratuito no plano free do Groq
- Excelente desempenho em português
- Limites free tier: 30 req/min, 14.400 req/dia

Se o modelo for descontinuado, consulte os modelos disponíveis:
```bash
curl https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer $GROQ_API_KEY"
```
E atualize `GROQ_MODEL` em `app.py`.

---

## Fallback

Se a API Groq estiver indisponível (erro, rate limit, chave inválida), o backend responde automaticamente com respostas pré-definidas por palavras-chave — garantindo que o chatbot nunca fique mudo para o cliente.

---

## Estrutura do projeto

```
ial-chatbot-backend/
├── app.py              # Aplicação Flask principal
├── requirements.txt    # Dependências Python
├── Procfile            # Comando de start para o Render
├── render.yaml         # Configuração do Render
├── .env                # Variáveis locais (não commitado)
├── .gitignore
└── README.md
```

---

## CORS

O backend aceita requisições apenas dos seguintes origens:

- `https://ialcorretora.com.br`
- `https://www.ialcorretora.com.br`
- `http://localhost:3000`
- `http://localhost:5500`
- `http://127.0.0.1:5500`
- `http://127.0.0.1:3000`

Para adicionar uma nova origem, edite a lista `origins` no início de `app.py`.
