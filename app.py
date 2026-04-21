from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import jwt
import json
import time
import base64
import io
import os
import re
import hashlib
from datetime import datetime, timezone, timedelta
from functools import wraps

# Carrega .env em desenvolvimento local
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# ============================================================
# CONFIGURAÇÃO DE SEGURANÇA
# ============================================================
JWT_SECRET      = os.environ.get("JWT_SECRET", "ial-dev-secret-change-in-production")
JWT_ALGORITHM   = "HS256"
JWT_EXPIRY_HOURS = 24  # token válido por 24h

GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL      = "meta-llama/llama-4-scout-17b-16e-instruct"

MAX_FILE_SIZE   = 10 * 1024 * 1024   # 10 MB
MAX_MSG_LENGTH  = 2000                # caracteres por mensagem

ALLOWED_ORIGINS = [
    "https://ialcorretora.com.br",
    "https://www.ialcorretora.com.br",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://127.0.0.1:3000",
]

ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}

# ============================================================
# CORS
# ============================================================
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# ============================================================
# RATE LIMITING
# ============================================================
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "60 per hour"],
    storage_uri="memory://",
)

# ============================================================
# HEADERS DE SEGURANÇA (aplicados em todas as respostas)
# ============================================================
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]          = "DENY"
    response.headers["X-XSS-Protection"]         = "1; mode=block"
    response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]        = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Remove header que expõe o servidor
    response.headers.pop("Server", None)
    return response

# ============================================================
# JWT — HELPERS
# ============================================================
def generate_token(session_id: str) -> str:
    """Gera um JWT vinculado ao session_id"""
    payload = {
        "session_id": session_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """Valida o JWT e retorna o payload, ou None se inválido"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def require_auth(f):
    """Decorator: exige JWT válido no header Authorization: Bearer <token>"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Token de autenticação ausente"}), 401

        token = auth_header.split(" ", 1)[1].strip()
        payload = verify_token(token)
        if payload is None:
            return jsonify({"error": "Token inválido ou expirado"}), 401

        # Disponibiliza o payload para a rota
        g.jwt_payload = payload
        return f(*args, **kwargs)
    return decorated


# ============================================================
# VALIDAÇÃO DE INPUT
# ============================================================
def sanitize_text(text: str) -> str:
    """Remove caracteres de controle e limita tamanho"""
    # Remove caracteres de controle (exceto newline e tab)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text[:MAX_MSG_LENGTH]


def validate_file(file) -> tuple[bool, str]:
    """Valida tipo e tamanho do arquivo. Retorna (ok, mensagem_erro)"""
    if not file or not file.filename:
        return False, "Arquivo inválido"

    # Verifica MIME type
    mime = file.content_type or ""
    ext  = os.path.splitext(file.filename.lower())[1]

    allowed_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".docx", ".txt"}
    if ext not in allowed_exts and mime not in ALLOWED_MIME_TYPES:
        return False, f"Tipo de arquivo não permitido: {ext or mime}"

    # Lê os bytes para verificar tamanho (e depois rebobina)
    file_bytes = file.read()
    file.seek(0)

    if len(file_bytes) > MAX_FILE_SIZE:
        return False, "Arquivo muito grande. Máximo 10MB."

    if len(file_bytes) == 0:
        return False, "Arquivo vazio."

    return True, ""


# ============================================================
# EXTRAÇÃO DE CONTEÚDO DE ARQUIVOS
# ============================================================
def extract_pdf_text(file_bytes: bytes) -> str | None:
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text.strip() or None
    except Exception as e:
        print(f"❌ Erro ao extrair PDF: {e}")
        return None


def extract_docx_text(file_bytes: bytes) -> str | None:
    try:
        from docx import Document
        doc  = Document(io.BytesIO(file_bytes))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return text.strip() or None
    except Exception as e:
        print(f"❌ Erro ao extrair DOCX: {e}")
        return None


def image_to_base64(file_bytes: bytes) -> str:
    return base64.b64encode(file_bytes).decode("utf-8")


# ============================================================
# GROQ API
# ============================================================
IAL_SYSTEM_PROMPT = """Você é o assistente virtual da IAL Corretora de Seguros. Responda SEMPRE em português brasileiro, de forma CLARA, DIRETA e AMIGÁVEL.

SOBRE A IAL:
- Mais de 15 anos de experiência no mercado
- Registro SUSEP: 201008615 (cadastro ATIVO)
- Corretora INDEPENDENTE — trabalhamos com várias seguradoras, não apenas uma
- Diferencial: "A seguradora assume o risco. A IAL assume o relacionamento."
- Atendemos todo o Brasil, atendimento digital e consultivo
- Atendimento GRATUITO e sem compromisso

PRODUTOS E SERVIÇOS:
- Seguro Auto
- Proteção Veicular (alternativa ao seguro: aceita perfis recusados, veículos antigos, sem análise de crédito rígida, custo mais acessível, cobre roubo/furto/colisão/assistência 24h)
- Seguro Residencial, Empresarial, Vida, Cibernético, Responsabilidade Civil
- Previdência Privada
- Consórcios (imóveis, veículos e investimentos — sem juros, sem entrada, ideal para sair do aluguel)

PROTEÇÃO VEICULAR:
- Aceita perfis recusados por seguradoras e veículos mais antigos
- Não exige análise de crédito — custo mais acessível
- Coberturas: roubo, furto, colisão, assistência 24h (guincho, pane elétrica/mecânica)

CONSÓRCIOS:
- Sem juros e sem entrada — ideal para conquistar a casa própria
- Pode ser usado como estratégia de crescimento patrimonial

CONTATO: WhatsApp (63) 98468-8161

REGRAS:
- Responda em 2 a 4 frases, de forma objetiva
- Sempre em português brasileiro, amigável, emojis com moderação
- Para preços/cotações, direcione para o WhatsApp
- Mencione 15 anos e SUSEP ao falar de confiabilidade
- Perfil recusado → sugira proteção veicular
- Quer sair do aluguel → sugira consórcio
- Ao analisar documentos/imagens, seja detalhado e útil
- Nunca invente informações fora deste contexto
"""

conversations: dict = {}


def call_groq(messages: list) -> str | None:
    if not GROQ_API_KEY:
        print("⚠️  GROQ_API_KEY não configurada")
        return None
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": messages,
                "max_tokens": 500,
                "temperature": 0.7,
                "top_p": 0.9,
            },
            timeout=30,
        )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        print(f"❌ Groq {response.status_code}: {response.text[:200]}")
        return None
    except Exception as e:
        print(f"❌ Erro Groq: {e}")
        return None


# ============================================================
# FALLBACK POR PALAVRAS-CHAVE
# ============================================================
def fallback_response(message: str) -> str:
    m = message.lower()
    w = m.split()

    if any(x in w for x in ["olá","ola","oi","hey","opa"]) or any(p in m for p in ["bom dia","boa tarde","boa noite"]):
        return "Olá! 😊 Sou o assistente virtual da IAL Corretora. Temos mais de 15 anos de experiência e registro SUSEP 201008615. Como posso ajudar você hoje?"
    if any(p in m for p in ["o que é a ial","o que e a ial","quem é a ial","sobre a ial"]):
        return "A IAL Corretora é uma empresa com mais de 15 anos de experiência, especializada em proteger pessoas, famílias e empresas com soluções personalizadas em seguros, consórcios e planejamento financeiro. 😊"
    if any(x in m for x in ["confiável","confiavel","confiar","susep","credibilidade"]):
        return "Sim! A IAL possui registro ativo na SUSEP (201008615) e trabalha com as principais seguradoras do Brasil. Mais de 15 anos de experiência! ✅"
    if any(x in m for x in ["consórcio","consorcio"]):
        return "Temos consórcios para imóveis, veículos e investimentos — sem juros e sem entrada. Quer saber mais? WhatsApp: (63) 98468-8161"
    if any(x in m for x in ["aluguel","casa própria","casa propria","imóvel","imovel"]):
        return "Consórcio é a melhor estratégia para sair do aluguel! Sem juros, sem entrada. Quer uma simulação? WhatsApp: (63) 98468-8161 🏠"
    if any(x in m for x in ["recusado","negado","não aprovado","nao aprovado"]):
        return "A Proteção Veicular é ideal para você! Aceita praticamente qualquer perfil, mesmo quem foi recusado por seguradoras. WhatsApp: (63) 98468-8161"
    if any(p in m for p in ["proteção veicular","protecao veicular"]):
        return "A Proteção Veicular é uma alternativa ao seguro tradicional — mais acessível, aceita perfis recusados e veículos antigos. WhatsApp: (63) 98468-8161"
    if any(x in m for x in ["preço","preco","valor","quanto custa","caro","barato"]):
        return "Os valores variam conforme seu perfil. Trabalhamos com várias seguradoras para o melhor custo-benefício. Cotação gratuita: (63) 98468-8161"
    if any(x in m for x in ["contato","telefone","whatsapp","falar","ligar"]):
        return "Entre em contato pelo WhatsApp: (63) 98468-8161. Atendimento gratuito para todo o Brasil! 😊"
    if any(x in m for x in ["obrigado","obrigada","valeu","obg","vlw"]):
        return "Por nada! Estou aqui para ajudar. Qualquer dúvida, é só chamar! WhatsApp: (63) 98468-8161 😊"
    if any(x in m for x in ["tchau","adeus","bye","até logo","ate logo"]):
        return "Até logo! Qualquer dúvida, estamos à disposição pelo WhatsApp: (63) 98468-8161. Tenha um ótimo dia! 😊"
    return "Posso ajudar com Seguros, Proteção Veicular, Consórcios ou Previdência. Sobre qual você gostaria de saber mais? Ou fale direto: (63) 98468-8161 📱"


# ============================================================
# ROTAS
# ============================================================

@app.route("/api/token", methods=["POST"])
@limiter.limit("20 per hour")
def get_token():
    """
    Gera um JWT para o cliente iniciar uma sessão de chat.
    O frontend chama este endpoint uma vez ao abrir o chat.

    Body (JSON): { "session_id": "string_unica" }  (opcional — gera automaticamente se omitido)
    Retorna: { "token": "...", "session_id": "...", "expires_in": 86400 }
    """
    data       = request.json or {}
    session_id = data.get("session_id") or hashlib.sha256(
        f"{get_remote_address()}{time.time()}".encode()
    ).hexdigest()[:16]

    token = generate_token(session_id)
    print(f"🔑 Token gerado para sessão: {session_id}")

    return jsonify({
        "token":      token,
        "session_id": session_id,
        "expires_in": JWT_EXPIRY_HOURS * 3600,
    })


@app.route("/api/chat", methods=["POST"])
@limiter.limit("30 per minute; 500 per day")
@require_auth
def chat():
    """
    Endpoint principal do chat.
    Requer: Authorization: Bearer <jwt>
    Aceita: JSON (só texto) ou multipart/form-data (texto + arquivo)
    """
    session_id = "default"
    try:
        start_time = time.time()

        # session_id vem do JWT (não do body — evita spoofing)
        session_id = g.jwt_payload.get("session_id", "default")

        # Parse do body
        if request.content_type and "multipart/form-data" in request.content_type:
            message = sanitize_text(request.form.get("message", ""))
            file    = request.files.get("file")
        else:
            data    = request.json or {}
            message = sanitize_text(data.get("message", ""))
            file    = None

        print(f"📨 [{session_id[:8]}] {message[:60]}{'...' if len(message) > 60 else ''}")

        if not message and not file:
            return jsonify({"error": "Envie uma mensagem ou arquivo"}), 400

        # Validar arquivo
        if file:
            ok, err = validate_file(file)
            if not ok:
                return jsonify({"error": err}), 400

        # Gerenciar histórico
        if session_id not in conversations:
            conversations[session_id] = []

        # Processar arquivo
        user_content = []

        if file:
            file_bytes = file.read()
            filename   = file.filename.lower()
            mime_type  = file.content_type or ""

            print(f"📎 {file.filename} ({len(file_bytes)/1024:.1f} KB) [{mime_type}]")

            if mime_type.startswith("image/") or filename.endswith((".jpg",".jpeg",".png",".webp",".gif")):
                img_mime = mime_type if mime_type.startswith("image/") else "image/jpeg"
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img_mime};base64,{image_to_base64(file_bytes)}"}
                })
                user_content.append({"type": "text", "text": message or "Analise esta imagem e me diga o que você vê. Se for relacionado a veículo, seguro ou documento, forneça informações úteis."})

            elif filename.endswith(".pdf") or "pdf" in mime_type:
                pdf_text = extract_pdf_text(file_bytes)
                text_part = (f"{message}\n\n[Conteúdo do PDF]\n{pdf_text[:4000]}" if message else f"Analise este documento:\n\n{pdf_text[:4000]}") if pdf_text else (message or "PDF enviado, mas não foi possível extrair o texto.")
                user_content.append({"type": "text", "text": text_part})

            elif filename.endswith(".docx") or "word" in mime_type:
                docx_text = extract_docx_text(file_bytes)
                text_part = (f"{message}\n\n[Conteúdo do documento]\n{docx_text[:4000]}" if message else f"Analise este documento:\n\n{docx_text[:4000]}") if docx_text else (message or "Documento enviado, mas não foi possível extrair o texto.")
                user_content.append({"type": "text", "text": text_part})

            else:
                try:
                    file_text = file_bytes.decode("utf-8", errors="ignore")
                    text_part = f"{message}\n\n[Conteúdo do arquivo]\n{file_text[:4000]}" if message else f"Analise este conteúdo:\n\n{file_text[:4000]}"
                except Exception:
                    text_part = message or "Arquivo enviado."
                user_content.append({"type": "text", "text": text_part})
        else:
            user_content.append({"type": "text", "text": message})

        # Histórico (só texto para não explodir memória)
        conversations[session_id].append({"role": "user", "content": message or "[arquivo]"})
        if len(conversations[session_id]) > 8:
            conversations[session_id] = conversations[session_id][-8:]

        # Montar mensagens para o Groq
        groq_messages = [{"role": "system", "content": IAL_SYSTEM_PROMPT}]
        for msg in conversations[session_id][:-1]:
            groq_messages.append({"role": msg["role"], "content": msg["content"]})
        groq_messages.append({"role": "user", "content": user_content})

        # Chamar Groq
        bot_response = call_groq(groq_messages)

        if not bot_response:
            print("⚠️  Usando fallback")
            bot_response = fallback_response(message) if message else "Recebi seu arquivo! Para análise detalhada, entre em contato: (63) 98468-8161 📱"

        conversations[session_id].append({"role": "assistant", "content": bot_response})

        elapsed = time.time() - start_time
        print(f"✅ {elapsed:.2f}s")

        return jsonify({"response": bot_response, "session_id": session_id})

    except Exception as e:
        print(f"❌ Erro: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"response": "Entre em contato pelo WhatsApp (63) 98468-8161 para atendimento imediato! 📱", "session_id": session_id})


@app.route("/api/clear-session", methods=["POST"])
@limiter.limit("10 per minute")
@require_auth
def clear_session():
    session_id = g.jwt_payload.get("session_id", "default")
    conversations.pop(session_id, None)
    return jsonify({"message": "Sessão limpa"})


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status":      "ok",
        "message":     "Backend IAL funcionando!",
        "model":       GROQ_MODEL,
        "multimodal":  True,
        "security":    ["JWT", "CORS", "RateLimit", "InputValidation", "SecurityHeaders"],
        "suporta":     ["texto", "imagens (jpg/png/webp)", "PDF", "DOCX", "TXT"],
    })


# ============================================================
# ERRO HANDLERS
# ============================================================
@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({"error": "Muitas requisições. Aguarde um momento e tente novamente."}), 429

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint não encontrado"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Método não permitido"}), 405


if __name__ == "__main__":
    print("🚀 Backend IAL iniciado!")
    print("📡 Servidor: http://localhost:5000")
    print(f"🤖 Modelo: {GROQ_MODEL}")
    print("🔒 Segurança: JWT + CORS + RateLimit + InputValidation")
    print("📎 Suporte: texto, imagens (jpg/png/webp), PDF, DOCX, TXT")
    app.run(debug=True, host="0.0.0.0", port=5000)
