import json

with open("data/clip_card_index.json", "r", encoding="utf-8") as f:
    data = json.load(f)

cards = data.get("cards", [])
krakeelo_cards = [c for c in cards if c.get("name") == "Krakeelo"]

print(f"Found {len(krakeelo_cards)} Krakeelo entries in clip_card_index\n")

for card in krakeelo_cards:
    print(f"Name: {card.get('name')}")
    print(f"  set_code: {card.get('set_code')}")
    print(f"  number: {card.get('number')}")
    print(f"  rarity: {repr(card.get('rarity'))}")
    print(f"  types: {card.get('types', [])}")
    print(f"  supertype: {card.get('supertype')}")
    print(f"  subtypes: {card.get('subtypes', [])}")
    print()
