import sqlite3

conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Check sv04-212
cur.execute("SELECT id, name, ptcg_id, rarity FROM cards WHERE ptcg_id = 'sv04-212'")
row = cur.fetchone()
if row:
    print(f"sv04-212 card:")
    print(f"  Name: {row[1]}")
    print(f"  PTCG_ID: {row[2]}")
    print(f"  Rarity: {row[3]}")
    
    # Simulate what classifyHoloEffect would return
    rarity = row[3].lower()
    if "ultra" in rarity or "illustration" in rarity:
        effect = "rare ultra"
        print(f"\n  → classifyHoloEffect returns: '{effect}'")
        print(f"  → CSS class will be: .holo-card.rare-ultra")
        print(f"  → This will apply the FULL ART HOLO EFFECT!")
    else:
        print(f"\n  → classifyHoloEffect would NOT return 'rare ultra'")
        print(f"  → Full art effect would NOT be applied")

# Also check how many cards now have Ultra Rare
cur.execute("SELECT COUNT(*) FROM cards WHERE rarity = 'Ultra Rare'")
ultra_count = cur.fetchone()[0]
print(f"\nTotal cards with 'Ultra Rare' rarity: {ultra_count}")

conn.close()
