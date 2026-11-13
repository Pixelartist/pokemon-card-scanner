import json

try:
    with open("data/clip_card_index.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        
    # Find Krakeelo cards
    krakeelo_cards = []
    for card in data.get("cards", []):
        if "Krakeelo" in card.get("name", "").lower():
            krakeelo_cards.append(card)
    
    print(f"Found {len(krakeelo_cards)} Krakeelo card(s):\n")
    for card in krakeelo_cards:
        print(f"  name: {card.get('name')}")
        print(f"  ptcg_id: {card.get('ptcg_id')}")
        print(f"  set_code: {card.get('set_code')}")
        print(f"  number: {card.get('number')}")
        print(f"  rarity: {card.get('rarity')}")
        print(f"  types: {card.get('types', [])}")
        print(f"  supertype: {card.get('supertype')}")
        print(f"  subtypes: {card.get('subtypes', [])}")
        print()
except Exception as e:
    print(f"Error: {e}")
