import json
import os

# Check JSON files for PAR DE or sv04-212
json_files = ["data/clip_card_index.json", "src/static/cards_meta.json"]

for filepath in json_files:
    if os.path.exists(filepath):
        print(f"\n=== {filepath} ===")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            # Search for sv04-212 or PAR DE
            if isinstance(data, dict) and "cards" in data:
                for card in data["cards"]:
                    ptcg_id = card.get("ptcg_id", "")
                    set_code = card.get("set_code", "")
                    number = card.get("number", "")
                    
                    if "sv04" in str(ptcg_id).lower() or "sv04" == str(set_code).lower():
                        if number in ["212", "PAR DE"]:
                            print(f"\nFound match:")
                            print(f"  name: {card.get('name')}")
                            print(f"  ptcg_id: {card.get('ptcg_id')}")
                            print(f"  set_code: {card.get('set_code')}")
                            print(f"  number: {card.get('number')}")
                            print(f"  rarity: {repr(card.get('rarity'))}")
                            print(f"  types: {card.get('types', [])}")
                            print(f"  supertype: {card.get('supertype')}")
                            print(f"  subtypes: {card.get('subtypes', [])}")
                            
            elif isinstance(data, list):
                for card in data:
                    if isinstance(card, dict):
                        ptcg_id = card.get("ptcg_id", "")
                        set_code = card.get("set_code", "")
                        number = card.get("number", "")
                        
                        if ("sv04" in str(ptcg_id).lower() or "sv04" == str(set_code).lower()) and number == "212":
                            print(f"\nFound match:")
                            print(f"  name: {card.get('name')}")
                            print(f"  ptcg_id: {card.get('ptcg_id')}")
                            print(f"  set_code: {card.get('set_code')}")
                            print(f"  number: {card.get('number')}")
                            print(f"  rarity: {repr(card.get('rarity'))}")
        except Exception as e:
            print(f"Error: {e}")
