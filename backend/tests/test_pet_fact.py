"""宠物习性知识注入测试（2026-08-15）

背景：用户口误问鹦鹉吃猫粮，角色顺着说——因宠物上下文只有名字/种类/状态，
无习性知识且无「说错可纠正」规则。修复 = SPECIES_META 增 diet/care + species_fact。
"""
from app.services.pet_service import species_fact, species_label, SPECIES_META


def test_species_fact_parrot_rejects_cat_food():
    assert "不吃猫粮" in species_fact("parrot")


def test_species_fact_present_for_all_whitelist():
    for key in SPECIES_META:
        f = species_fact(key)
        assert f, f"{key} 缺少习性知识"


def test_species_fact_unknown_returns_empty():
    assert species_fact("dragon") == ""
    assert species_fact(None) == ""


def test_species_label_still_works():
    assert species_label("parrot") == "鹦鹉"
