from pydantic import BaseModel


class GenerateRequest(BaseModel):
    prompt: str


class LootItem(BaseModel):
    item: str = "minecraft:bone"
    min: int = 1
    max: int = 1


class MobData(BaseModel):
    name: str = "Custom Mob"
    health: int = 20
    attack_damage: int = 5
    abilities: list[str] = []
    loot: list[LootItem] = []
