import json
import os

# Load rarities from all available JSON sources
all_rarities = {}

json_files = [
    "data/clip_card_index.json",
    "src/static/cards_meta.json"
]

for filepath in json_files:
    if os.path.exists(filepath):
        print(f"Loading from {filepath}...")
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        cards = data.get("cards", data) if isinstance(data, dict) else data
        
        for card in cards:
            if isinstance(card, dict):
                ptcg_id = card.get("ptcg_id") or f"{card.get('set_code', '')}-{card.get('number', '')}"
                rarity = card.get("rarity", "")
                
                if ptcg_id and rarity:
                    all_rarities[ptcg_id] = rarity

print(f"Loaded {len(all_rarities)} mappings")

# Save to a lookup file
output_file = "data/rarity_lookup.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(all_rarities, f, indent=2, ensure_ascii=False)
print(f"Saved to {output_file}")
