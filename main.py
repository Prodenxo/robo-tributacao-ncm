"""
Backend FastAPI - Robô de Tributação por NCM
"""
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
import os
import uuid
import threading
from robo import processar_planilha

load_dotenv()

app = FastAPI(title="Robô Tributação NCM")

# Servir arquivos estáticos (frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Armazenar status e resultados das tarefas em memória
tarefas = {}  # {task_id: {"status": "...", "progresso": [], "arquivo": bytes}}
UPLOAD_DIR = "/tmp/robo_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.get("/")
async def home():
    return FileResponse("static/index.html")


@app.post("/api/upload")
async def upload_arquivo(arquivo: UploadFile = File(...)):
    """Recebe o Excel, inicia o processamento em background e retorna um task_id."""
    task_id = str(uuid.uuid4())[:8]
    conteudo = await arquivo.read()

    tarefas[task_id] = {
        "status": "processando",
        "progresso": ["📄 Arquivo recebido, iniciando processamento..."],
        "arquivo": None
    }

    # Rodar o robô em uma thread separada (não bloqueia o servidor)
    thread = threading.Thread(target=_processar_em_background, args=(task_id, conteudo))
    thread.start()

    return {"task_id": task_id}


def _processar_em_background(task_id, conteudo_bytes):
    """Executa o robô em background e salva o resultado."""
    login = os.getenv("EAUDITORIA_LOGIN", "")
    senha = os.getenv("EAUDITORIA_SENHA", "")

    def callback(msg):
        if task_id in tarefas:
            tarefas[task_id]["progresso"].append(msg)

    try:
        resultado_bytes = processar_planilha(conteudo_bytes, login, senha, callback=callback)
        tarefas[task_id]["arquivo"] = resultado_bytes
        tarefas[task_id]["status"] = "concluido"
    except Exception as e:
        tarefas[task_id]["status"] = "erro"
        tarefas[task_id]["progresso"].append(f"❌ Erro fatal: {str(e)}")


@app.get("/api/status/{task_id}")
async def status_tarefa(task_id: str):
    """Retorna o status atual do processamento."""
    if task_id not in tarefas:
        return JSONResponse(status_code=404, content={"erro": "Tarefa não encontrada"})

    tarefa = tarefas[task_id]
    return {
        "status": tarefa["status"],
        "progresso": tarefa["progresso"],
        "pronto": tarefa["status"] == "concluido"
    }


@app.get("/api/download/{task_id}")
async def download_resultado(task_id: str):
    """Retorna o arquivo Excel processado."""
    if task_id not in tarefas or tarefas[task_id]["arquivo"] is None:
        return JSONResponse(status_code=404, content={"erro": "Arquivo não disponível"})

    # Salvar temporariamente para enviar
    caminho = os.path.join(UPLOAD_DIR, f"{task_id}_resultado.xlsx")
    with open(caminho, "wb") as f:
        f.write(tarefas[task_id]["arquivo"])

    return FileResponse(
        caminho,
        filename="ncm_resultado.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
