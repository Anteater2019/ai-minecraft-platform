from pydantic import BaseModel


class GenerateRequest(BaseModel):
    prompt: str


class LootItem(BaseModel):
    item: str
    min: int
    max: int


class MobData(BaseModel):
    name: str
    health: int
    attack_damage: int
    abilities: list[str]
    loot: list[LootItem]
