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

# --- Pure-Python PNG generation (no Pillow) ---


def _make_png(width: int, height: int, pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    """Generate a PNG from a 2D list of RGBA tuples."""
    raw = b""
    for row in pixels:
        raw += b"\x00"  # filter byte
        for r, g, b, a in row:
            raw += bytes([r, g, b, a])

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


def _make_mob_texture(base_color: tuple[int, int, int]) -> bytes:
    """Generate a 64x64 entity texture with the vanilla player UV layout.

    Box UV layout (vanilla player model on 64x64):
      Head  [8,8,8]  uv=[0,0]   -> occupies cols 0..31,  rows 0..15
      Body  [8,12,4] uv=[16,16] -> occupies cols 16..39, rows 16..31
      R.Arm [4,12,4] uv=[40,16] -> occupies cols 40..55, rows 16..31
      R.Leg [4,12,4] uv=[0,16]  -> occupies cols 0..15,  rows 16..31
      L.Arm [4,12,4] uv=[32,48] -> occupies cols 32..47, rows 48..63
      L.Leg [4,12,4] uv=[16,48] -> occupies cols 16..31, rows 48..63
    """
    r, g, b = base_color
    # Slightly different shades for body parts
    head_color = (min(r + 40, 255), min(g + 40, 255), min(b + 40, 255), 255)
    body_color = (r, g, b, 255)
    arm_color = (max(r - 30, 0), max(g - 30, 0), max(b - 30, 0), 255)
    leg_color = (max(r - 60, 0), max(g - 60, 0), max(b - 60, 0), 255)

    transparent = (0, 0, 0, 0)

    # Start with transparent 64x64
    pixels = [[transparent] * 64 for _ in range(64)]

    def fill_rect(x: int, y: int, w: int, h: int, color: tuple[int, int, int, int]) -> None:
        for py in range(y, min(y + h, 64)):
            for px in range(x, min(x + w, 64)):
                pixels[py][px] = color

    # Head UV region: uv_offset=[0,0], cube [8,8,8]
    # Footprint: (2*8+2*8)=32 wide, (8+8)=16 tall
    fill_rect(0, 0, 32, 16, head_color)

    # Body UV region: uv_offset=[16,16], cube [8,12,4]
    # Footprint: (2*4+2*8)=24 wide, (4+12)=16 tall
    fill_rect(16, 16, 24, 16, body_color)

    # Right Leg UV region: uv_offset=[0,16], cube [4,12,4]
    # Footprint: (2*4+2*4)=16 wide, (4+12)=16 tall
    fill_rect(0, 16, 16, 16, leg_color)

    # Right Arm UV region: uv_offset=[40,16], cube [4,12,4]
    # Footprint: 16 wide, 16 tall
    fill_rect(40, 16, 16, 16, arm_color)

    # Left Leg UV region: uv_offset=[16,48], cube [4,12,4]
    fill_rect(16, 48, 16, 16, leg_color)

    # Left Arm UV region: uv_offset=[32,48], cube [4,12,4]
    fill_rect(32, 48, 16, 16, arm_color)

    # Add simple "face" on head front face: offset (d, d) = (8, 8), size (w, h) = (8, 8)
    eye_color = (220, 220, 220, 255)
    # Eyes at rows 10-11 within the head front face
    fill_rect(10, 10, 2, 2, eye_color)
    fill_rect(14, 10, 2, 2, eye_color)

    return _make_png(64, 64, pixels)


# Color mapping for mob themes
MOB_COLORS: dict[str, tuple[int, int, int]] = {
    "fire": (200, 60, 20),
    "flame": (200, 60, 20),
    "lava": (200, 80, 10),
    "ice": (80, 160, 220),
    "frost": (100, 180, 230),
    "snow": (200, 220, 240),
    "poison": (80, 180, 40),
    "toxic": (100, 200, 30),
    "shadow": (40, 20, 60),
    "dark": (30, 30, 50),
    "wither": (30, 30, 30),
    "undead": (60, 80, 60),
    "zombie": (80, 120, 70),
    "skeleton": (200, 200, 190),
    "water": (40, 100, 200),
    "ocean": (20, 80, 180),
    "dragon": (100, 20, 20),
    "ender": (20, 10, 30),
    "lightning": (240, 240, 100),
    "electric": (240, 220, 60),
    "earth": (120, 90, 60),
    "stone": (140, 140, 140),
    "crystal": (180, 100, 220),
    "magic": (160, 60, 200),
    "nature": (60, 140, 40),
    "forest": (40, 100, 30),
    "blood": (150, 20, 20),
    "ghost": (180, 200, 220),
    "spirit": (160, 180, 210),
    "gold": (220, 180, 40),
    "iron": (180, 180, 180),
    "diamond": (80, 220, 220),
    "emerald": (40, 200, 80),
}


def _guess_color(mob_name: str) -> tuple[int, int, int]:
    """Pick a color based on keywords in the mob name."""
    name_lower = mob_name.lower()
    for keyword, color in MOB_COLORS.items():
        if keyword in name_lower:
            return color
    # Default: muted purple
    return (120, 80, 160)


PLACEHOLDER_PNG = _make_mob_texture((120, 80, 160))


# --- Name sanitization ---


def sanitize_name(name: str) -> str:
    """Convert a display name to a safe snake_case identifier."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "custom_mob"


# --- Ability mapping ---
# Each ability maps to a dict of components to add to the entity.
# The special key "_remove" lists components to remove (e.g., walk nav when flying).
# The special key "_hostile" indicates the mob should actively target players.

ABILITY_MAP: dict[str, dict] = {
    "melee attack": {
        "_hostile": True,
        "minecraft:behavior.melee_attack": {
            "priority": 3,
            "speed_multiplier": 1.2,
            "track_target": True,
        },
    },
    "ranged attack": {
        "_hostile": True,
        "minecraft:shooter": {
            "def": "minecraft:arrow",
        },
        "minecraft:behavior.ranged_attack": {
            "priority": 3,
            "attack_interval_min": 3,
            "attack_interval_max": 5,
            "attack_radius": 15,
        },
    },
    "shoot fireballs": {
        "_hostile": True,
        "minecraft:shooter": {
            "def": "minecraft:small_fireball",
        },
        "minecraft:behavior.ranged_attack": {
            "priority": 3,
            "burst_shots": 3,
            "burst_interval": 0.3,
            "charge_shoot_trigger": 4,
            "attack_interval_min": 3,
            "attack_interval_max": 5,
            "attack_radius": 48,
        },
    },
    "shoot snowballs": {
        "_hostile": True,
        "minecraft:shooter": {
            "def": "minecraft:snowball",
        },
        "minecraft:behavior.ranged_attack": {
            "priority": 3,
            "attack_interval_min": 2,
            "attack_interval_max": 4,
            "attack_radius": 15,
        },
    },
    "shoot arrows": {
        "_hostile": True,
        "minecraft:shooter": {
            "def": "minecraft:arrow",
        },
        "minecraft:behavior.ranged_attack": {
            "priority": 3,
            "attack_interval_min": 3,
            "attack_interval_max": 5,
            "attack_radius": 15,
        },
    },
    "flying": {
        "_remove": ["minecraft:navigation.walk", "minecraft:movement.basic", "minecraft:jump.static"],
        "minecraft:can_fly": {},
        "minecraft:movement.fly": {},
        "minecraft:flying_speed": {
            "value": 0.6,
        },
        "minecraft:navigation.fly": {
            "can_path_over_water": True,
            "can_path_from_air": True,
        },
        "minecraft:physics": {
            "has_gravity": False,
        },
    },
    "teleport": {
        "minecraft:teleport": {
            "random_teleports": True,
            "min_random_teleport_time": 5,
            "max_random_teleport_time": 30,
            "random_teleport_cube": [32, 16, 32],
            "target_distance": 16,
            "target_teleport_chance": 1.0,
        },
    },
    "explode": {
        "_hostile": True,
        "minecraft:explode": {
            "fuse_length": 1.5,
            "fuse_lit": True,
            "power": 3,
            "causes_fire": False,
            "breaks_blocks": True,
            "destroy_affected_by_griefing": True,
        },
        "minecraft:behavior.melee_attack": {
            "priority": 3,
            "speed_multiplier": 1.25,
            "track_target": True,
        },
    },
    "swim": {
        "_remove": ["minecraft:navigation.walk", "minecraft:movement.basic"],
        "minecraft:breathable": {
            "breathes_air": True,
            "breathes_water": True,
        },
        "minecraft:underwater_movement": {
            "value": 0.12,
        },
        "minecraft:movement.amphibious": {},
        "minecraft:navigation.generic": {
            "is_amphibious": True,
            "can_path_over_water": True,
            "can_swim": True,
            "can_walk": True,
        },
    },
    "poison": {
        "_hostile": True,
        "minecraft:behavior.melee_attack": {
            "priority": 3,
            "speed_multiplier": 1.2,
            "track_target": True,
        },
        "minecraft:attack": {
            "damage": 3,
            "effect_name": "poison",
            "effect_duration": 10,
        },
    },
    "wither": {
        "_hostile": True,
        "minecraft:behavior.melee_attack": {
            "priority": 3,
            "speed_multiplier": 1.2,
            "track_target": True,
        },
        "minecraft:attack": {
            "damage": 5,
            "effect_name": "wither",
            "effect_duration": 10,
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

    needs_hostile_targeting = False

    for ability in mob.abilities:
        key = ability.lower().strip()
        if key not in ABILITY_MAP:
            continue
        mapping = ABILITY_MAP[key]

        # Check if this ability makes the mob hostile
        if mapping.get("_hostile"):
            needs_hostile_targeting = True

        # Remove conflicting components
        for comp_name in mapping.get("_remove", []):
            components.pop(comp_name, None)

        # Add new components
        for comp_name, comp_data in mapping.items():
            if comp_name.startswith("_"):
                continue
            components[comp_name] = comp_data

    # All mobs are hostile by default — actively seek and attack players
    if needs_hostile_targeting or True:
        components["minecraft:behavior.nearest_attackable_target"] = {
            "priority": 2,
            "must_see": True,
            "reselect_targets": True,
            "entity_types": [
                {
                    "filters": {
                        "test": "is_family",
                        "subject": "other",
                        "value": "player",
                    },
                    "max_dist": 16,
                }
            ],
        }
        # If no specific attack behavior was added, default to melee
        if "minecraft:behavior.melee_attack" not in components and "minecraft:behavior.ranged_attack" not in components:
            components["minecraft:behavior.melee_attack"] = {
                "priority": 3,
                "speed_multiplier": 1.2,
                "track_target": True,
            }

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
    """Humanoid geometry with proper Box UV matching vanilla player layout (64x64)."""
    return {
        "format_version": "1.12.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": f"geometry.{mob_id}",
                    "texture_width": 64,
                    "texture_height": 64,
                    "visible_bounds_width": 1,
                    "visible_bounds_height": 2.5,
                    "visible_bounds_offset": [0, 1.25, 0],
                },
                "bones": [
                    {
                        "name": "body",
                        "pivot": [0, 24, 0],
                        "cubes": [
                            {
                                "origin": [-4, 12, -2],
                                "size": [8, 12, 4],
                                "uv": [16, 16],
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
                        "name": "right_arm",
                        "parent": "body",
                        "pivot": [-5, 22, 0],
                        "cubes": [
                            {
                                "origin": [-8, 12, -2],
                                "size": [4, 12, 4],
                                "uv": [40, 16],
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
                                "uv": [32, 48],
                            }
                        ],
                    },
                    {
                        "name": "right_leg",
                        "parent": "body",
                        "pivot": [-1.9, 12, 0],
                        "cubes": [
                            {
                                "origin": [-3.9, 0, -2],
                                "size": [4, 12, 4],
                                "uv": [0, 16],
                            }
                        ],
                    },
                    {
                        "name": "left_leg",
                        "parent": "body",
                        "pivot": [1.9, 12, 0],
                        "cubes": [
                            {
                                "origin": [-0.1, 0, -2],
                                "size": [4, 12, 4],
                                "uv": [16, 48],
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

    # Generate colored texture based on mob name
    color = _guess_color(mob.name)
    texture_png = _make_mob_texture(color)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # --- Behavior Pack ---
        zf.writestr(
            f"{bp}/manifest.json",
            json.dumps(_bp_manifest(mob_id, rp_header_uuid), indent=2),
        )
        zf.writestr(f"{bp}/pack_icon.png", texture_png)
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
        zf.writestr(f"{rp}/pack_icon.png", texture_png)
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
            texture_png,
        )

    buf.seek(0)
    return buf
