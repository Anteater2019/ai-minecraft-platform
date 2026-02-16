from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.schemas import GenerateRequest, MobData
from app.generator import generate_mob
from app.addon_builder import build_addon_zip, sanitize_name

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


@app.post("/generate-addon")
async def generate_addon(req: GenerateRequest):
    try:
        mob_data = await generate_mob(req.prompt)
    except ConnectionError:
        raise HTTPException(status_code=503, detail="Ollama is unreachable")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to generate valid mob data: {exc}",
        )
    mob_id = sanitize_name(mob_data.name)
    zip_buf = build_addon_zip(mob_data)
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{mob_id}.mcaddon"',
        },
    )


@app.post("/build-addon")
async def build_addon(mob: MobData):
    mob_id = sanitize_name(mob.name)
    zip_buf = build_addon_zip(mob)
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{mob_id}.mcaddon"',
        },
    )
