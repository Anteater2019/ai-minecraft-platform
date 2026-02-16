import json
import re

import httpx

from app.config import OLLAMA_BASE_URL, MODEL_NAME, MAX_RETRIES
from app.schemas import MobData

SYSTEM_PROMPT = """You are a Minecraft Bedrock addon designer.
Return ONLY valid JSON matching this exact schema — no markdown, no explanation.
Schema: { "name": string, "health": number (1-1000), "attack_damage": number (1-100), "abilities": string[], "loot": [{ "item": string (minecraft:item_id), "min": number, "max": number }] }"""


def _extract_json(text: str) -> dict:
    """Extract JSON object from model output, handling markdown fences."""
    # Try parsing the whole string first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try to find a JSON object in the text
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return json.loads(match.group(0))

    raise ValueError("No valid JSON found in model output")


async def generate_mob(prompt: str) -> MobData:
    """Call Ollama and return validated MobData."""
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": MODEL_NAME,
                        "system": SYSTEM_PROMPT,
                        "prompt": prompt,
                        "stream": False,
                    },
                )
                resp.raise_for_status()

            raw = resp.json()["response"]
            data = _extract_json(raw)
            return MobData.model_validate(data)

        except httpx.ConnectError:
            raise ConnectionError("Cannot reach Ollama — is it running?")
        except Exception as exc:
            last_error = exc

    raise last_error  # type: ignore[misc]
