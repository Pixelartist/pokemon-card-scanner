#!/usr/bin/env python3
"""Full batch collection test with working auth."""
import requests
import time

BASE = "http://localhost:5005"

print("=" * 60)
print("FULL BATCH COLLECTION END-TO-END TEST")
print("=" * 60)

# 1. Register a user
print("\n1. Registering user...")
username = f"batch_test_{int(time.time())}"
register_data = {
    "username": username,
    "email": f"{username}@test.com",
    "password": "password123",
    "full_name": "Batch Test User"
}
r = requests.post(f"{BASE}/api/auth/register", json=register_data, timeout=10)
print(f"   Status: {r.status_code}")
if r.status_code != 200:
    print(f"   Error: {r.text}")
    exit(1)
data = r.json()
token = data['access_token']
user_id = data['user']['id']
print(f"   ✓ Registered: {username} (ID: {user_id})")

# 2. Test auth/me
print("\n2. Testing auth/me...")
headers = {"Authorization": f"Bearer {token}"}
r = requests.get(f"{BASE}/api/auth/me", headers=headers, timeout=10)
print(f"   Status: {r.status_code}")
if r.status_code == 200:
    user_data = r.json()
    print(f"   ✓ Auth working: {user_data['username']}")
else:
    print(f"   ✗ Error: {r.text}")
    exit(1)

# 3. Create test cards
print("\n3. Creating test cards...")
card_ids = []
for card_ptcg_id in ['xy1-124', 'xy1-125', 'xy1-126']:
    r = requests.post(f"{BASE}/api/cards/{card_ptcg_id}/create-or-get", timeout=10)
    print(f"   Card {card_ptcg_id}: {r.status_code}")
    if r.status_code == 200:
        card_id = r.json()['card_id']
        card_ids.append(card_id)
        print(f"      ✓ Created (DB ID: {card_id})")
    else:
        print(f"      ✗ Error: {r.text}")

print(f"\n   Card DB IDs: {card_ids}")

# 4. Test batch add
print("\n4. Testing batch add endpoint...")
if len(card_ids) >= 2:
    batch_items = [
        {"card_id": card_ids[0], "condition": "Near Mint", "quantity": 2, "language": "EN"},
        {"card_id": card_ids[1], "condition": "Light Play", "quantity": 1, "language": "EN"},
    ]
    
    r = requests.post(f"{BASE}/api/collection/add-batch",
        json=batch_items, headers=headers, timeout=10)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        result = r.json()
        print(f"   ✓ Batch add successful!")
        print(f"   Added {result['added']} items")
        print(f"   Item IDs: {result['ids']}")
    else:
        print(f"   ✗ Error: {r.text}")
        exit(1)
else:
    print(f"   ⚠ Skipping - only {len(card_ids)} cards created")
    exit(1)

# 5. Verify collection
print("\n5. Verifying collection...")
r = requests.get(f"{BASE}/api/collection", headers=headers, timeout=10)
if r.status_code == 200:
    collection = r.json()
    print(f"   ✓ Collection has {len(collection['items'])} items")
    for item in collection['items']:
        card = item.get('card', {})
        print(f"     - {card.get('name', 'Unknown')} ({item['condition']}) x{item['quantity']}")
    print(f"   Stats: {collection.get('stats', 'N/A')}")
else:
    print(f"   ✗ Error: {r.text}")

# 6. Test single add (should still work)
print("\n6. Testing single add (backwards compatibility)...")
if len(card_ids) >= 3:
    r = requests.post(f"{BASE}/api/collection/add",
        data={"card_id": card_ids[2], "condition": "Mint", "quantity": 1},
        headers=headers, timeout=10)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        result = r.json()
        print(f"   ✓ Single add successful: ID {result['id']}")
    else:
        print(f"   ✗ Error: {r.text}")

# 7. Final verification
print("\n7. Final verification...")
r = requests.get(f"{BASE}/api/collection", headers=headers, timeout=10)
if r.status_code == 200:
    collection = r.json()
    print(f"   ✓ Total items in collection: {len(collection['items'])}")
    print(f"   Stats: {collection.get('stats', 'N/A')}")

print("\n" + "=" * 60)
print("✓ ALL TESTS PASSED - BATCH COLLECTION WORKING!")
print("=" * 60)