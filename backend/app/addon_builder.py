"""Convert MobData into a downloadable .mcaddon ZIP (behavior pack + resource pack)."""

from __future__ import annotations

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
    row = b""
    for _ in range(width):
        row += b"\xff\x00\xff\xff"  # RGBA magenta
    raw = b""
    for _ in range(height):
        raw += b"\x00" + row  # filter byte 0 per row

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


def _bp_manifest(mob_id: str, rp_header_uuid: str) -> dict:
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
            {"uuid": rp_header_uuid, "version": [1, 0, 0]}
        ],
    }


def _bp_entity(mob_id: str, mob: MobData) -> dict:
    components: dict = {
        "minecraft:physics": {},
        "minecraft:type_family": {
            "family": ["mob", "custom"],
        },
        "minecraft:collision_box": {
            "width": 0.8,
            "height": 1.8,
        },
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
        "minecraft:jump.static": {},
        "minecraft:navigation.walk": {
            "can_path_over_water": True,
            "avoid_water": True,
            "can_walk": True,
        },
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
            "look_distance": 6.0,
            "probability": 0.02,
        },
        "minecraft:behavior.random_look_around": {
            "priority": 8,
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

    if has_fly_nav or has_swim_nav:
        components.pop("minecraft:navigation.walk", None)

    return {
        "format_version": "1.12.0",
        "minecraft:entity": {
            "description": {
                "identifier": f"custom:{mob_id}",
                "is_spawnable": True,
                "is_summonable": True,
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


def _rp_manifest(mob_id: str) -> dict:
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
    }


def _rp_entity(mob_id: str) -> dict:
    return {
        "format_version": "1.10.0",
        "minecraft:client_entity": {
            "description": {
                "identifier": f"custom:{mob_id}",
                "materials": {
                    "default": "entity_alphatest",
                },
                "textures": {
                    "default": f"textures/entity/{mob_id}",
                },
                "geometry": {
                    "default": f"geometry.{mob_id}",
                },
                "render_controllers": [
                    "controller.render.default",
                ],
                "spawn_egg": {
                    "base_color": "#FF00FF",
                    "overlay_color": "#800080",
                },
            },
        },
    }


def _rp_geometry(mob_id: str) -> dict:
    """Simple humanoid-style geometry: head + body + 4 limbs."""
    return {
        "format_version": "1.12.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": f"geometry.{mob_id}",
                    "texture_width": 16,
                    "texture_height": 16,
                    "visible_bounds_width": 1,
                    "visible_bounds_height": 2,
                    "visible_bounds_offset": [0, 1, 0],
                },
                "bones": [
                    {
                        "name": "body",
                        "pivot": [0, 24, 0],
                        "cubes": [
                            {
                                "origin": [-4, 12, -2],
                                "size": [8, 12, 4],
                                "uv": [0, 0],
                            }
                        ],
                    },
                    {
                        "name": "head",
                        "parent": "body",
                        "pivot": [0, 24, 0],
                        "cubes": [
                            {
                                "origin": [-4, 24, -4],
                                "size": [8, 8, 8],
                                "uv": [0, 0],
                            }
                        ],
                    },
                    {
                        "name": "left_arm",
                        "parent": "body",
                        "pivot": [5, 22, 0],
                        "cubes": [
                            {
                                "origin": [4, 12, -2],
                                "size": [4, 12, 4],
                                "uv": [0, 0],
                            }
                        ],
                    },
                    {
                        "name": "right_arm",
                        "parent": "body",
                        "pivot": [-5, 22, 0],
                        "cubes": [
                            {
                                "origin": [-8, 12, -2],
                                "size": [4, 12, 4],
                                "uv": [0, 0],
                            }
                        ],
                    },
                    {
                        "name": "left_leg",
                        "parent": "body",
                        "pivot": [2, 12, 0],
                        "cubes": [
                            {
                                "origin": [0, 0, -2],
                                "size": [4, 12, 4],
                                "uv": [0, 0],
                            }
                        ],
                    },
                    {
                        "name": "right_leg",
                        "parent": "body",
                        "pivot": [-2, 12, 0],
                        "cubes": [
                            {
                                "origin": [-4, 0, -2],
                                "size": [4, 12, 4],
                                "uv": [0, 0],
                            }
                        ],
                    },
                ],
            }
        ],
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
    rp_header_uuid = _deterministic_uuid("rp.header", mob_id)

    bp = f"{mob_id}_BP"
    rp = f"{mob_id}_RP"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # --- Behavior Pack ---
        zf.writestr(
            f"{bp}/manifest.json",
            json.dumps(_bp_manifest(mob_id, rp_header_uuid), indent=2),
        )
        zf.writestr(f"{bp}/pack_icon.png", PLACEHOLDER_PNG)
        zf.writestr(
            f"{bp}/entities/{mob_id}.json",
            json.dumps(_bp_entity(mob_id, mob), indent=2),
        )
        zf.writestr(
            f"{bp}/loot_tables/entities/{mob_id}.json",
            json.dumps(_bp_loot_table(mob), indent=2),
        )

        # --- Resource Pack ---
        zf.writestr(
            f"{rp}/manifest.json",
            json.dumps(_rp_manifest(mob_id), indent=2),
        )
        zf.writestr(f"{rp}/pack_icon.png", PLACEHOLDER_PNG)
        zf.writestr(
            f"{rp}/entity/{mob_id}.entity.json",
            json.dumps(_rp_entity(mob_id), indent=2),
        )
        zf.writestr(
            f"{rp}/models/entity/{mob_id}.geo.json",
            json.dumps(_rp_geometry(mob_id), indent=2),
        )
        zf.writestr(
            f"{rp}/texts/en_US.lang",
            _rp_lang(mob_id, mob.name),
        )
        zf.writestr(
            f"{rp}/textures/entity/{mob_id}.png",
            PLACEHOLDER_PNG,
        )

    buf.seek(0)
    return buf
