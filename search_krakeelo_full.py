import json
import os
import glob

found = []

# Search all JSON files
for pattern in ["data/*.json", "src/**/*.json", "*.json"]:
    for filepath in glob.glob(pattern, recursive=True):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            # Helper to search recursively
            def search(obj, path=""):
                if isinstance(obj, dict):
                    if obj.get("name", "").lower() == "krakeelo":
                        return [obj]
                    results = []
                    for k, v in obj.items():
                        results.extend(search(v, f"{path}.{k}"))
                    return results
                elif isinstance(obj, list):
                    results = []
                    for i, item in enumerate(obj):
                        results.extend(search(item, f"{path}[{i}]"))
                    return results
                return []
            
            results = search(data)
            if results:
                found.append((filepath, results))
        except Exception as e:
            pass

if found:
    print("Found Krakeelo in:\n")
    for filepath, cards in found[:3]:  # Limit output
        print(f"File: {filepath}")
        for card in cards:
            print(f"  name: {card.get('name')}")
            print(f"  ptcg_id: {card.get('ptcg_id')}")
            print(f"  set_code: {card.get('set_code')}")
            print(f"  number: {card.get('number')}")
            print(f"  rarity: {card.get('rarity')}")
            print(f"  types: {card.get('types', [])}")
            print(f"  supertype: {card.get('supertype')}")
            print(f"  subtypes: {card.get('subtypes', [])}")
            print()
else:
    print("Krakeelo not found in any JSON file. Checking raw text...")
    
    # Check raw JSON files
    import re
    for pattern in ["data/*.json"]:
        for filepath in glob.glob(pattern, recursive=True):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    if "Krakeelo" in content:
                        print(f"\nFound 'Krakeelo' in {filepath}")
                        # Extract surrounding context
                        lines = content.split('\n')
                        for i, line in enumerate(lines):
                            if "Krakeelo" in line:
                                start = max(0, i-5)
                                end = min(len(lines), i+15)
                                print("\nContext:")
                                for j in range(start, end):
                                    print(f"  {j}: {lines[j][:200]}")
                                break
            except:
                pass
