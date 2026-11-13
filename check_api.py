import requests
import json

# Try to get Krakeelo sv04-212 from TCGdex API
endpoints = [
    "https://docs.pokemontcg.io/#operation/getCardsBySet",
    "https://pokemontcg.io/?set=sv04&number=212"
]

# Try the official API
try:
    url = "https://api.pokemontcg.io/v2/cards?set=sv04&number=212"
    response = requests.get(url, timeout=10)
    
    if response.status_code == 200:
        data = response.json()
        cards = data.get("data", [])
        
        print(f"Found {len(cards)} card(s) from API:\n")
        for card in cards:
            print(f"Name: {card.get('name')}")
            print(f"Number: {card.get('number')}")
            print(f"Rarity: {card.get('rarity')}")
            print(f"Set: {card.get('set', {}).get('name')}")
            print(f"Types: {card.get('types', [])}")
            print()
    else:
        print(f"API returned status {response.status_code}")
except Exception as e:
    print(f"Error: {e}")

# Also check what we know about sv04 set
print("\n\nNote: sv04 is SV4a (Paradox Rift) in English or SV04 in German (Temporal Forces)")
print("Krakeelo sv04-212 is likely a Full Art / Ultra Rare card")
