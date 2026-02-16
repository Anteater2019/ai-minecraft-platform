"""Unit tests for addon_builder module."""

import json
import zipfile

from app.addon_builder import build_addon_zip, sanitize_name
from app.schemas import LootItem, MobData


def _make_mob(**overrides) -> MobData:
    """Create a MobData with sensible defaults, overridable per-test."""
    defaults = {
        "name": "Fire Dragon",
        "health": 100,
        "attack_damage": 15,
        "abilities": ["melee attack"],
        "loot": [LootItem(item="minecraft:diamond", min=1, max=3)],
    }
    defaults.update(overrides)
    return MobData(**defaults)


# --- sanitize_name tests ---


class TestSanitizeName:
    def test_basic(self):
        assert sanitize_name("Fire Dragon") == "fire_dragon"

    def test_special_chars(self):
        assert sanitize_name("Mob @#$ 123!") == "mob_123"

    def test_empty_fallback(self):
        assert sanitize_name("") == "custom_mob"

    def test_only_special_chars(self):
        assert sanitize_name("@#$%") == "custom_mob"

    def test_leading_trailing_spaces(self):
        assert sanitize_name("  Ice Golem  ") == "ice_golem"

    def test_single_word(self):
        assert sanitize_name("Zombie") == "zombie"


# --- build_addon_zip tests ---


class TestBuildAddonZip:
    def test_valid_zip(self):
        mob = _make_mob()
        buf = build_addon_zip(mob)
        assert zipfile.is_zipfile(buf)

    def test_contains_bp_and_rp_folders(self):
        mob = _make_mob()
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert any(n.startswith("fire_dragon_BP/") for n in names)
            assert any(n.startswith("fire_dragon_RP/") for n in names)

    def test_has_all_expected_files(self):
        mob = _make_mob()
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            names = set(zf.namelist())
            expected = {
                "fire_dragon_BP/manifest.json",
                "fire_dragon_BP/entities/fire_dragon.json",
                "fire_dragon_BP/loot_tables/entities/fire_dragon.json",
                "fire_dragon_RP/manifest.json",
                "fire_dragon_RP/entity/fire_dragon.entity.json",
                "fire_dragon_RP/render_controllers/fire_dragon.render_controllers.json",
                "fire_dragon_RP/texts/en_US.lang",
                "fire_dragon_RP/textures/entity/fire_dragon.png",
            }
            assert expected == names

    def test_bp_manifest_structure(self):
        mob = _make_mob()
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            manifest = json.loads(zf.read("fire_dragon_BP/manifest.json"))
            assert manifest["format_version"] == 2
            assert manifest["modules"][0]["type"] == "data"
            assert "dependencies" in manifest
            assert len(manifest["dependencies"]) == 1

    def test_rp_manifest_structure(self):
        mob = _make_mob()
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            manifest = json.loads(zf.read("fire_dragon_RP/manifest.json"))
            assert manifest["format_version"] == 2
            assert manifest["modules"][0]["type"] == "resources"
            assert "dependencies" in manifest

    def test_entity_health_and_attack(self):
        mob = _make_mob(health=200, attack_damage=25)
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            entity = json.loads(zf.read("fire_dragon_BP/entities/fire_dragon.json"))
            components = entity["minecraft:entity"]["components"]
            assert components["minecraft:health"]["value"] == 200
            assert components["minecraft:health"]["max"] == 200
            assert components["minecraft:attack"]["damage"] == 25

    def test_flying_replaces_walk_navigation(self):
        mob = _make_mob(abilities=["flying"])
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            entity = json.loads(zf.read("fire_dragon_BP/entities/fire_dragon.json"))
            components = entity["minecraft:entity"]["components"]
            assert "minecraft:navigation.fly" in components
            assert "minecraft:navigation.walk" not in components
            assert "minecraft:flying" in components

    def test_swim_replaces_walk_navigation(self):
        mob = _make_mob(abilities=["swim"])
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            entity = json.loads(zf.read("fire_dragon_BP/entities/fire_dragon.json"))
            components = entity["minecraft:entity"]["components"]
            assert "minecraft:navigation.swim" in components
            assert "minecraft:navigation.walk" not in components

    def test_unknown_abilities_skipped(self):
        mob = _make_mob(abilities=["laser eyes", "time travel"])
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            entity = json.loads(zf.read("fire_dragon_BP/entities/fire_dragon.json"))
            components = entity["minecraft:entity"]["components"]
            # Should still have default walk navigation (nothing replaced it)
            assert "minecraft:navigation.walk" in components
            # Should not have any unknown component
            assert "laser eyes" not in json.dumps(components)

    def test_loot_table_entries(self):
        loot = [
            LootItem(item="minecraft:diamond", min=1, max=3),
            LootItem(item="minecraft:gold_ingot", min=2, max=5),
        ]
        mob = _make_mob(loot=loot)
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            table = json.loads(
                zf.read("fire_dragon_BP/loot_tables/entities/fire_dragon.json")
            )
            entries = table["pools"][0]["entries"]
            assert len(entries) == 2
            assert entries[0]["name"] == "minecraft:diamond"
            assert entries[0]["functions"][0]["count"]["min"] == 1
            assert entries[0]["functions"][0]["count"]["max"] == 3
            assert entries[1]["name"] == "minecraft:gold_ingot"

    def test_lang_file_has_display_name(self):
        mob = _make_mob(name="Ice Golem")
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            lang = zf.read("ice_golem_RP/texts/en_US.lang").decode()
            assert "Ice Golem" in lang
            assert "entity.custom:ice_golem.name=Ice Golem" in lang
            assert "Spawn Ice Golem" in lang

    def test_texture_png_magic_bytes(self):
        mob = _make_mob()
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            png_data = zf.read("fire_dragon_RP/textures/entity/fire_dragon.png")
            assert png_data[:4] == b"\x89PNG"

    def test_manifests_cross_reference(self):
        """BP depends on RP uuid and vice versa."""
        mob = _make_mob()
        buf = build_addon_zip(mob)
        with zipfile.ZipFile(buf) as zf:
            bp = json.loads(zf.read("fire_dragon_BP/manifest.json"))
            rp = json.loads(zf.read("fire_dragon_RP/manifest.json"))
            bp_uuid = bp["header"]["uuid"]
            rp_uuid = rp["header"]["uuid"]
            # BP depends on RP
            assert bp["dependencies"][0]["uuid"] == rp_uuid
            # RP depends on BP
            assert rp["dependencies"][0]["uuid"] == bp_uuid
