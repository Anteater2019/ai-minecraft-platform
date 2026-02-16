from __future__ import annotations

import json
import re

import httpx

from app.config import OLLAMA_BASE_URL, MODEL_NAME, MAX_RETRIES
from app.schemas import MobData

SYSTEM_PROMPT = """You are a Minecraft Bedrock addon designer.
Return ONLY a single valid JSON object. No markdown, no comments, no explanation.

Example response:
{"name": "Flame Golem", "health": 80, "attack_damage": 12, "abilities": ["melee attack", "explode"], "loot": [{"item": "minecraft:iron_ingot", "min": 1, "max": 3}]}

Schema:
- name: string
- health: integer 1-1000
- attack_damage: integer 1-100
- abilities: array of strings
- loot: array of {"item": "minecraft:item_id", "min": integer, "max": integer}"""


def _fix_json(text: str) -> str:
    """Try to fix common JSON issues from LLM output."""
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Replace single quotes with double quotes (but not inside strings)
    text = re.sub(r"'([^']*)'", r'"\1"', text)
    # Remove JS-style comments
    text = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
    return text


def _extract_json(text: str) -> dict:
    """Extract JSON object from model output, handling common LLM quirks."""
    text = text.strip()

    # Try parsing the whole string first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        candidate = match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                return json.loads(_fix_json(candidate))
            except json.JSONDecodeError:
                pass

    # Try to find a JSON object in the text
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return json.loads(_fix_json(candidate))

    raise ValueError("No valid JSON found in model output")


def _normalize_mob_data(data: dict) -> dict:
    """Coerce fields that the model may return in the wrong type."""
    # Numeric fields: float → int
    for key in ("health", "attack_damage"):
        if key in data:
            try:
                data[key] = int(float(data[key]))
            except (ValueError, TypeError):
                del data[key]  # let default kick in

    # abilities: string → list
    if "abilities" in data and isinstance(data["abilities"], str):
        try:
            data["abilities"] = json.loads(data["abilities"])
        except json.JSONDecodeError:
            data["abilities"] = [s.strip() for s in data["abilities"].split(",") if s.strip()]
    if not isinstance(data.get("abilities"), list):
        data["abilities"] = []

    # loot: string → list
    if "loot" in data and isinstance(data["loot"], str):
        try:
            data["loot"] = json.loads(data["loot"])
        except json.JSONDecodeError:
            data["loot"] = []
    if not isinstance(data.get("loot"), list):
        data["loot"] = []

    # Filter out malformed loot entries
    data["loot"] = [
        entry for entry in data["loot"]
        if isinstance(entry, dict) and "item" in entry
    ]

    return data


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
                        "format": "json",
                    },
                )
                resp.raise_for_status()

            raw = resp.json()["response"]
            data = _extract_json(raw)
            data = _normalize_mob_data(data)
            return MobData.model_validate(data)

        except httpx.ConnectError:
            raise ConnectionError("Cannot reach Ollama — is it running?")
        except Exception as exc:
            last_error = exc

    raise last_error  # type: ignore[misc]
