from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.schemas import GenerateRequest, MobData
from app.generator import generate_mob

app = FastAPI(title="AI Minecraft Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/generate-json", response_model=MobData)
async def generate_json(req: GenerateRequest):
    try:
        return await generate_mob(req.prompt)
    except ConnectionError:
        raise HTTPException(status_code=503, detail="Ollama is unreachable")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to generate valid mob data: {exc}",
        )
