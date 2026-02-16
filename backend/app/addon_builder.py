"""Convert MobData into a downloadable .mcaddon ZIP (behavior pack + resource pack)."""

import base64
import io
import json
import re
import struct
import uuid
import zipfile
import zlib

from app.schemas import MobData

# --- Placeholder texture: 16x16 magenta PNG (no Pillow needed) ---

def _make_placeholder_png() -> bytes:
    """Generate a minimal 16x16 magenta PNG in pure Python."""
    width, height = 16, 16
    # RGBA: magenta (255, 0, 255, 255)
    row = b""
    for _ in range(width):
        row += b"\xff\x00\xff\xff"
    raw = b""
    for _ in range(height):
        raw += b"\x00" + row  # filter byte 0 (None) per row

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


PLACEHOLDER_PNG = _make_placeholder_png()


# --- Name sanitization ---

def sanitize_name(name: str) -> str:
    """Convert a display name to a safe snake_case identifier."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "custom_mob"


# --- Ability mapping ---

ABILITY_MAP: dict[str, dict] = {
    "melee attack": {
        "minecraft:behavior.melee_attack": {
            "priority": 3,
            "speed_multiplier": 1.2,
            "track_target": True,
        },
    },
    "ranged attack": {
        "minecraft:behavior.ranged_attack": {
            "priority": 3,
            "burst_shots": 1,
            "charge_shoot_trigger": 2.0,
            "speed_multiplier": 1.0,
        },
    },
    "flying": {
        "minecraft:flying": {},
        "minecraft:navigation.fly": {
            "can_path_over_water": True,
            "can_pass_doors": True,
        },
    },
    "teleport": {
        "minecraft:teleport": {
            "random_teleports": True,
            "max_random_teleport_time": 30.0,
            "random_teleport_cube_length": 16.0,
        },
    },
    "explode": {
        "minecraft:explode": {
            "fuse_length": 1.5,
            "fuse_lit": True,
            "power": 3,
            "causes_fire": False,
        },
    },
    "shoot fireballs": {
        "minecraft:shooter": {
            "def": "minecraft:small_fireball",
            "type": "ranged",
            "aux_val": 0,
        },
        "minecraft:behavior.ranged_attack": {
            "priority": 3,
            "burst_shots": 1,
            "charge_shoot_trigger": 2.0,
            "speed_multiplier": 1.0,
        },
    },
    "swim": {
        "minecraft:navigation.swim": {
            "can_path_over_water": False,
            "can_sink": False,
        },
    },
}


def _deterministic_uuid(namespace: str, name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ai-minecraft.{namespace}.{name}"))


# --- Behavior Pack builders ---

def _bp_manifest(mob_id: str, rp_uuid: str) -> dict:
    return {
        "format_version": 2,
        "header": {
            "name": f"{mob_id} Behavior Pack",
            "description": f"Behavior pack for {mob_id}",
            "uuid": _deterministic_uuid("bp.header", mob_id),
            "version": [1, 0, 0],
            "min_engine_version": [1, 20, 0],
        },
        "modules": [
            {
                "type": "data",
                "uuid": _deterministic_uuid("bp.module", mob_id),
                "version": [1, 0, 0],
            }
        ],
        "dependencies": [
            {"uuid": rp_uuid, "version": [1, 0, 0]}
        ],
    }


def _bp_entity(mob_id: str, mob: MobData) -> dict:
    components: dict = {
        "minecraft:health": {
            "value": mob.health,
            "max": mob.health,
        },
        "minecraft:attack": {
            "damage": mob.attack_damage,
        },
        "minecraft:movement": {
            "value": 0.3,
        },
        "minecraft:movement.basic": {},
        "minecraft:navigation.walk": {
            "can_path_over_water": True,
            "avoid_water": True,
        },
        "minecraft:physics": {},
        "minecraft:pushable": {
            "is_pushable": True,
            "is_pushable_by_piston": True,
        },
        "minecraft:behavior.random_stroll": {
            "priority": 6,
            "speed_multiplier": 1.0,
        },
        "minecraft:behavior.look_at_player": {
            "priority": 7,
            "look_distance": 8.0,
        },
        "minecraft:behavior.hurt_by_target": {
            "priority": 1,
        },
        "minecraft:loot": {
            "table": f"loot_tables/entities/{mob_id}.json",
        },
    }

    has_fly_nav = False
    has_swim_nav = False

    for ability in mob.abilities:
        key = ability.lower().strip()
        if key not in ABILITY_MAP:
            continue
        for comp_name, comp_data in ABILITY_MAP[key].items():
            components[comp_name] = comp_data
            if comp_name == "minecraft:navigation.fly":
                has_fly_nav = True
            if comp_name == "minecraft:navigation.swim":
                has_swim_nav = True

    # Navigation conflict resolution: fly/swim replaces default walk
    if has_fly_nav or has_swim_nav:
        components.pop("minecraft:navigation.walk", None)

    return {
        "format_version": "1.20.0",
        "minecraft:entity": {
            "description": {
                "identifier": f"custom:{mob_id}",
                "is_spawnable": True,
                "is_summonable": True,
                "is_experimental": False,
            },
            "components": components,
        },
    }


def _bp_loot_table(mob: MobData) -> dict:
    entries = []
    for loot_item in mob.loot:
        entries.append({
            "type": "item",
            "name": loot_item.item,
            "weight": 1,
            "functions": [
                {
                    "function": "set_count",
                    "count": {"min": loot_item.min, "max": loot_item.max},
                }
            ],
        })
    return {
        "pools": [
            {
                "rolls": 1,
                "entries": entries,
            }
        ],
    }


# --- Resource Pack builders ---

def _rp_manifest(mob_id: str, bp_uuid: str) -> dict:
    return {
        "format_version": 2,
        "header": {
            "name": f"{mob_id} Resource Pack",
            "description": f"Resource pack for {mob_id}",
            "uuid": _deterministic_uuid("rp.header", mob_id),
            "version": [1, 0, 0],
            "min_engine_version": [1, 20, 0],
        },
        "modules": [
            {
                "type": "resources",
                "uuid": _deterministic_uuid("rp.module", mob_id),
                "version": [1, 0, 0],
            }
        ],
        "dependencies": [
            {"uuid": bp_uuid, "version": [1, 0, 0]}
        ],
    }


def _rp_entity(mob_id: str) -> dict:
    return {
        "format_version": "1.20.0",
        "minecraft:client_entity": {
            "description": {
                "identifier": f"custom:{mob_id}",
                "materials": {"default": "entity_alphatest"},
                "textures": {
                    "default": f"textures/entity/{mob_id}",
                },
                "geometry": {
                    "default": "geometry.humanoid",
                },
                "render_controllers": [
                    f"controller.render.{mob_id}",
                ],
                "spawn_egg": {
                    "base_color": "#FF00FF",
                    "overlay_color": "#800080",
                },
            },
        },
    }


def _rp_render_controller(mob_id: str) -> dict:
    return {
        "format_version": "1.20.0",
        "render_controllers": {
            f"controller.render.{mob_id}": {
                "geometry": "Geometry.default",
                "materials": [{"*": "Material.default"}],
                "textures": ["Texture.default"],
            },
        },
    }


def _rp_lang(mob_id: str, display_name: str) -> str:
    return (
        f"entity.custom:{mob_id}.name={display_name}\n"
        f"item.spawn_egg.entity.custom:{mob_id}.name=Spawn {display_name}\n"
    )


# --- Main builder ---

def build_addon_zip(mob: MobData) -> io.BytesIO:
    """Build a .mcaddon ZIP from MobData and return it as an in-memory buffer."""
    mob_id = sanitize_name(mob.name)
    bp_uuid = _deterministic_uuid("bp.header", mob_id)
    rp_uuid = _deterministic_uuid("rp.header", mob_id)

    bp_prefix = f"{mob_id}_BP"
    rp_prefix = f"{mob_id}_RP"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Behavior Pack
        zf.writestr(
            f"{bp_prefix}/manifest.json",
            json.dumps(_bp_manifest(mob_id, rp_uuid), indent=2),
        )
        zf.writestr(
            f"{bp_prefix}/entities/{mob_id}.json",
            json.dumps(_bp_entity(mob_id, mob), indent=2),
        )
        zf.writestr(
            f"{bp_prefix}/loot_tables/entities/{mob_id}.json",
            json.dumps(_bp_loot_table(mob), indent=2),
        )

        # Resource Pack
        zf.writestr(
            f"{rp_prefix}/manifest.json",
            json.dumps(_rp_manifest(mob_id, bp_uuid), indent=2),
        )
        zf.writestr(
            f"{rp_prefix}/entity/{mob_id}.entity.json",
            json.dumps(_rp_entity(mob_id), indent=2),
        )
        zf.writestr(
            f"{rp_prefix}/render_controllers/{mob_id}.render_controllers.json",
            json.dumps(_rp_render_controller(mob_id), indent=2),
        )
        zf.writestr(
            f"{rp_prefix}/texts/en_US.lang",
            _rp_lang(mob_id, mob.name),
        )
        zf.writestr(
            f"{rp_prefix}/textures/entity/{mob_id}.png",
            PLACEHOLDER_PNG,
        )

    buf.seek(0)
    return buf
