import json
import sys

try:
    with open("data/clip_card_index.json", "r", encoding="utf-8") as f:
        # Read line by line to find Krakeelo entries
        for line_num, line in enumerate(f, 1):
            if "Krakeelo" in line or "krakel" in line.lower():
                try:
                    data = json.loads(line)
                    print(f"\nLine {line_num}:")
                    print(f"  name: {data.get('name')}")
                    print(f"  ptcg_id: {data.get('ptcg_id')}")
                    print(f"  set_code: {data.get('set_code')}")
                    print(f"  number: {data.get('number')}")
                    print(f"  rarity: {data.get('rarity')}")
                    print(f"  types: {data.get('types')}")
                    print(f"  supertype: {data.get('supertype')}")
                    print(f"  subtypes: {data.get('subtypes', [])}")
                    break  # Just get first match
                except json.JSONDecodeError:
                    pass
except Exception as e:
    print(f"Error: {e}")
