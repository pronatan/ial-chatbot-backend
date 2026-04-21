from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import time
import base64
import io
import os

# Carrega .env em desenvolvimento local (ignorado em produção)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# CORS: permite o site da IAL e localhost (dev)
CORS(app, origins=[
    "https://ialcorretora.com.br",
    "https://www.ialcorretora.com.br",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://127.0.0.1:3000",
])

# ============================================================
# GROQ API — chave via variável de ambiente (produção)
# ou diretamente aqui (desenvolvimento local)
# ============================================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL   = "meta-llama/llama-4-scout-17b-16e-instruct"

# Tamanho máximo de upload: 10MB
MAX_FILE_SIZE = 10 * 1024 * 1024

# Base de conhecimento da IAL (system prompt)
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
- Seguro Residencial
- Seguro Empresarial
- Seguro de Vida
- Seguro Cibernético
- Responsabilidade Civil
- Previdência Privada
- Consórcios (imóveis, veículos e investimentos — sem juros, sem entrada, ideal para sair do aluguel)

CONSÓRCIOS:
- Sem juros e sem entrada
- Ideal para conquistar a casa própria
- Pode ser usado como estratégia de investimento e crescimento patrimonial

PROTEÇÃO VEICULAR:
- Alternativa ao seguro tradicional (associados contribuem entre si)
- Aceita perfis recusados por seguradoras
- Aceita veículos mais antigos
- Não exige análise de crédito
- Custo mais acessível que seguro tradicional
- Coberturas: roubo, furto, colisão, assistência 24h (guincho, pane elétrica/mecânica)

CONTATO: WhatsApp (63) 98468-8161

REGRAS DE RESPOSTA:
- Responda em 2 a 4 frases, de forma objetiva
- Sempre em português brasileiro
- Seja amigável e use emojis com moderação
- Para dúvidas sobre preços ou cotações, direcione para o WhatsApp
- Mencione os 15 anos de experiência e SUSEP quando falar de confiabilidade
- Se o cliente tiver perfil recusado em seguro, sugira proteção veicular
- Se quiser sair do aluguel, sugira consórcio
- Ao analisar documentos ou imagens enviados pelo cliente, seja detalhado e útil
- Nunca invente informações que não estão neste contexto
"""

conversations = {}


# ============================================================
# EXTRAÇÃO DE CONTEÚDO DE ARQUIVOS
# ============================================================

def extract_pdf_text(file_bytes):
    """Extrai texto de um PDF"""
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text.strip() if text.strip() else None
    except Exception as e:
        print(f"❌ Erro ao extrair PDF: {e}")
        return None


def extract_docx_text(file_bytes):
    """Extrai texto de um arquivo DOCX"""
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        return text.strip() if text.strip() else None
    except Exception as e:
        print(f"❌ Erro ao extrair DOCX: {e}")
        return None


def image_to_base64(file_bytes, mime_type):
    """Converte imagem para base64"""
    return base64.b64encode(file_bytes).decode('utf-8')


# ============================================================
# CHAMADA À API GROQ
# ============================================================

def call_groq(messages):
    """Chama a API do Groq — suporta texto e imagens"""
    try:
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": GROQ_MODEL,
            "messages": messages,
            "max_tokens": 500,
            "temperature": 0.7,
            "top_p": 0.9
        }

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        else:
            print(f"❌ Erro Groq: {response.status_code} - {response.text[:300]}")
            return None

    except Exception as e:
        print(f"❌ Erro ao chamar Groq: {e}")
        return None


# ============================================================
# FALLBACK POR PALAVRAS-CHAVE
# ============================================================

def fallback_response(message):
    """Respostas automáticas por palavras-chave quando a API não está disponível"""
    message_lower = message.lower()
    words_in_message = message_lower.split()

    if any(w in words_in_message for w in ['olá', 'ola', 'oi', 'hey', 'opa']) or \
       any(p in message_lower for p in ['bom dia', 'boa tarde', 'boa noite']):
        return "Olá! 😊 Sou o assistente virtual da IAL Corretora. Temos mais de 15 anos de experiência e registro SUSEP 201008615. Como posso ajudar você hoje?"

    if any(p in message_lower for p in ['o que é a ial', 'o que e a ial', 'quem é a ial', 'sobre a ial']):
        return "A IAL Corretora é uma empresa com mais de 15 anos de experiência, especializada em proteger pessoas, famílias e empresas com soluções personalizadas em seguros, consórcios e planejamento financeiro. 😊"

    if any(p in message_lower for p in ['é seguradora', 'e seguradora', 'corretora independente']):
        return "Não somos uma seguradora — somos uma corretora independente! Trabalhamos com várias seguradoras para encontrar a melhor solução para você. 👍"

    if any(p in message_lower for p in ['por que escolher', 'porque escolher', 'vantagem', 'vantagens']):
        return "Porque aqui você recebe orientação estratégica, não apenas um produto. A seguradora assume o risco — a IAL assume o relacionamento. 💡"

    if any(w in message_lower for w in ['confiável', 'confiavel', 'confiar', 'susep', 'credibilidade']):
        return "Sim! A IAL possui registro ativo na SUSEP (201008615) e trabalha com as principais seguradoras do Brasil. Mais de 15 anos de experiência! ✅"

    if any(w in message_lower for w in ['gratuito', 'grátis', 'gratis', 'cobrado']):
        return "Sim! Toda a análise e orientação inicial são totalmente gratuitas e sem compromisso. 😊"

    if any(w in message_lower for w in ['consórcio', 'consorcio']):
        return "Temos consórcios para imóveis, veículos e investimentos — sem juros e sem entrada. Quer saber mais? WhatsApp: (63) 98468-8161"

    if any(w in message_lower for w in ['aluguel', 'casa própria', 'casa propria', 'imóvel', 'imovel']):
        return "Consórcio é a melhor estratégia para sair do aluguel! Sem juros, sem entrada. Quer uma simulação? WhatsApp: (63) 98468-8161 🏠"

    if any(w in message_lower for w in ['recusado', 'negado', 'não aprovado', 'nao aprovado']):
        return "A Proteção Veicular é ideal para você! Aceita praticamente qualquer perfil, mesmo quem foi recusado por seguradoras. WhatsApp: (63) 98468-8161"

    if any(p in message_lower for p in ['proteção veicular', 'protecao veicular']):
        return "A Proteção Veicular é uma alternativa ao seguro tradicional — mais acessível, aceita perfis recusados e veículos antigos. WhatsApp: (63) 98468-8161"

    if any(w in message_lower for w in ['preço', 'preco', 'valor', 'quanto custa', 'caro', 'barato']):
        return "Os valores variam conforme seu perfil. Trabalhamos com várias seguradoras para o melhor custo-benefício. Cotação gratuita: (63) 98468-8161"

    if any(w in message_lower for w in ['contato', 'telefone', 'whatsapp', 'falar', 'ligar']):
        return "Entre em contato pelo WhatsApp: (63) 98468-8161. Atendimento gratuito para todo o Brasil! 😊"

    if any(w in message_lower for w in ['obrigado', 'obrigada', 'valeu', 'obg', 'vlw']):
        return "Por nada! Estou aqui para ajudar. Qualquer dúvida, é só chamar! WhatsApp: (63) 98468-8161 😊"

    if any(w in message_lower for w in ['tchau', 'adeus', 'bye', 'até logo', 'ate logo']):
        return "Até logo! Qualquer dúvida, estamos à disposição pelo WhatsApp: (63) 98468-8161. Tenha um ótimo dia! 😊"

    return "Posso ajudar com Seguros, Proteção Veicular, Consórcios ou Previdência. Sobre qual você gostaria de saber mais? Ou fale direto: (63) 98468-8161 📱"


# ============================================================
# ROTAS
# ============================================================

@app.route('/api/chat', methods=['POST'])
def chat():
    """Endpoint de chat — aceita texto, imagens e documentos"""
    session_id = 'default'
    try:
        start_time = time.time()

        # Suporte a multipart/form-data (com arquivo) e JSON (só texto)
        if request.content_type and 'multipart/form-data' in request.content_type:
            message    = request.form.get('message', '').strip()
            session_id = request.form.get('session_id', 'default')
            file       = request.files.get('file')
        else:
            data       = request.json or {}
            message    = data.get('message', '').strip()
            session_id = data.get('session_id', 'default')
            file       = None

        print(f"📨 [{session_id}] {message[:60]}{'...' if len(message) > 60 else ''}")

        if not message and not file:
            return jsonify({'error': 'Envie uma mensagem ou arquivo'}), 400

        # Gerenciar histórico
        if session_id not in conversations:
            conversations[session_id] = []

        # --- Processar arquivo enviado ---
        user_content = []  # conteúdo multimodal para o Groq

        if file:
            file_bytes = file.read()
            filename   = file.filename.lower()
            mime_type  = file.content_type or ''

            print(f"📎 Arquivo: {file.filename} ({len(file_bytes)/1024:.1f} KB)")

            if len(file_bytes) > MAX_FILE_SIZE:
                return jsonify({'error': 'Arquivo muito grande. Máximo 10MB.'}), 400

            # Imagem → manda direto para o modelo multimodal
            if mime_type.startswith('image/') or filename.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                img_b64   = image_to_base64(file_bytes, mime_type)
                img_mime  = mime_type if mime_type.startswith('image/') else 'image/jpeg'
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img_mime};base64,{img_b64}"}
                })
                text_part = message if message else "Analise esta imagem e me diga o que você vê. Se for relacionado a veículo, seguro ou documento, forneça informações úteis."
                user_content.append({"type": "text", "text": text_part})

            # PDF → extrai texto e manda como contexto
            elif filename.endswith('.pdf') or 'pdf' in mime_type:
                pdf_text = extract_pdf_text(file_bytes)
                if pdf_text:
                    text_part = f"{message}\n\n[Conteúdo do PDF enviado pelo cliente]\n{pdf_text[:4000]}" if message else f"Analise este documento:\n\n{pdf_text[:4000]}"
                    user_content.append({"type": "text", "text": text_part})
                else:
                    user_content.append({"type": "text", "text": message or "O cliente enviou um PDF mas não foi possível extrair o texto."})

            # DOCX → extrai texto
            elif filename.endswith('.docx') or 'word' in mime_type:
                docx_text = extract_docx_text(file_bytes)
                if docx_text:
                    text_part = f"{message}\n\n[Conteúdo do documento enviado]\n{docx_text[:4000]}" if message else f"Analise este documento:\n\n{docx_text[:4000]}"
                    user_content.append({"type": "text", "text": text_part})
                else:
                    user_content.append({"type": "text", "text": message or "O cliente enviou um documento mas não foi possível extrair o texto."})

            # TXT / outros
            else:
                try:
                    file_text = file_bytes.decode('utf-8', errors='ignore')
                    text_part = f"{message}\n\n[Conteúdo do arquivo]\n{file_text[:4000]}" if message else f"Analise este conteúdo:\n\n{file_text[:4000]}"
                    user_content.append({"type": "text", "text": text_part})
                except Exception:
                    user_content.append({"type": "text", "text": message or "O cliente enviou um arquivo."})

        else:
            # Só texto
            user_content.append({"type": "text", "text": message})

        # Adicionar mensagem do usuário ao histórico
        # Histórico armazena só texto para não explodir memória
        history_text = message if message else "[arquivo enviado]"
        conversations[session_id].append({'role': 'user', 'content': history_text})

        # Manter últimas 8 mensagens
        if len(conversations[session_id]) > 8:
            conversations[session_id] = conversations[session_id][-8:]

        # Montar mensagens para o Groq
        groq_messages = [{'role': 'system', 'content': IAL_SYSTEM_PROMPT}]

        # Histórico anterior (só texto)
        for msg in conversations[session_id][:-1]:
            groq_messages.append({'role': msg['role'], 'content': msg['content']})

        # Mensagem atual (pode ter imagem)
        groq_messages.append({'role': 'user', 'content': user_content})

        # Chamar Groq
        bot_response = call_groq(groq_messages)

        # Fallback se Groq falhar
        if not bot_response:
            print("⚠️  Usando fallback por palavras-chave")
            bot_response = fallback_response(message) if message else "Recebi seu arquivo! Para análise detalhada, entre em contato pelo WhatsApp: (63) 98468-8161 📱"

        # Salvar resposta no histórico
        conversations[session_id].append({'role': 'assistant', 'content': bot_response})

        elapsed = time.time() - start_time
        print(f"✅ Resposta em {elapsed:.2f}s")

        return jsonify({
            'response': bot_response,
            'session_id': session_id
        })

    except Exception as e:
        print(f"❌ Erro geral: {e}")
        import traceback; traceback.print_exc()
        return jsonify({
            'response': 'Entre em contato pelo WhatsApp (63) 98468-8161 para atendimento imediato! 📱',
            'session_id': session_id
        })


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'message': 'Backend IAL funcionando!',
        'model': GROQ_MODEL,
        'multimodal': True,
        'suporta': ['texto', 'imagens (jpg/png/webp)', 'PDF', 'DOCX', 'TXT']
    })


@app.route('/api/clear-session', methods=['POST'])
def clear_session():
    try:
        data = request.json
        session_id = data.get('session_id', 'default')
        if session_id in conversations:
            del conversations[session_id]
        return jsonify({'message': 'Sessão limpa'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("🚀 Backend IAL iniciado!")
    print("📡 Servidor: http://localhost:5000")
    print(f"🤖 Modelo: {GROQ_MODEL}")
    print("📎 Suporte: texto, imagens (jpg/png/webp), PDF, DOCX, TXT")
    app.run(debug=True, host='0.0.0.0', port=5000)
