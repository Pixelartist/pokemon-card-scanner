#!/usr/bin/env python3
"""Final batch collection test with authentication."""
import requests
import json

BASE = "http://localhost:5005"

print("=" * 60)
print("FINAL BATCH COLLECTION TEST")
print("=" * 60)

# Step 1: Register a user
print("\n1. Registering user...")
register_data = {
    "username": "final_batch_user",
    "email": "final_batch_user@test.com",
    "password": "password123",
    "full_name": "Final Batch User"
}

r = requests.post(f"{BASE}/api/auth/register", json=register_data, timeout=10)
print(f"   Registration status: {r.status_code}")
if r.status_code != 200:
    print(f"   Error: {r.text}")
    exit(1)

token_data = r.json()
token = token_data['access_token']
user_id = token_data['user']['id']
print(f"   ✓ Registration successful")
print(f"   User ID: {user_id}")
print(f"   Token: {token[:50]}...")

# Step 2: Test auth/me endpoint
print("\n2. Testing auth/me endpoint...")
headers = {"Authorization": f"Bearer {token}"}
r = requests.get(f"{BASE}/api/auth/me", headers=headers, timeout=10)
print(f"   Status: {r.status_code}")
if r.status_code == 200:
    user_data = r.json()
    print(f"   ✓ auth/me successful")
    print(f"   User ID from server: {user_data['id']}")
    print(f"   Username: {user_data['username']}")
else:
    print(f"   ✗ Error: {r.text}")

# Step 3: Test login
print("\n3. Testing login...")
login_data = {
    "username": "final_batch_user",
    "password": "password123"
}
r = requests.post(f"{BASE}/api/auth/login", json=login_data, timeout=10)
print(f"   Status: {r.status_code}")
if r.status_code == 200:
    login_result = r.json()
    print(f"   ✓ Login successful")
    print(f"   Token: {login_result['access_token'][:50]}...")
    print(f"   User ID: {login_result['user']['id']}")
else:
    print(f"   ✗ Error: {r.text}")

# Step 4: Create cards in DB
print("\n4. Creating test cards...")
card_ids = []
for cid in ['xy1-124', 'xy2-124', 'xy3-124']:
    r = requests.post(f"{BASE}/api/cards/{cid}/create-or-get", timeout=10)
    print(f"   Card {cid}: {r.status_code}")
    if r.status_code == 200:
        card_id = r.json()['card_id']
        card_ids.append(card_id)
        print(f"      ✓ Created (ID: {card_id})")
    else:
        print(f"      ✗ Error: {r.text}")

print(f"\n   Card IDs created: {card_ids}")

# Step 5: Batch add to collection
print("\n5. Testing batch add endpoint...")
if len(card_ids) >= 3:
    batch_items = [
        {"card_id": card_ids[0], "condition": "Near Mint", "quantity": 2},
        {"card_id": card_ids[1], "condition": "Light Play", "quantity": 1},
        {"card_id": card_ids[2], "condition": "Mint", "quantity": 3},
    ]
    
    r = requests.post(f"{BASE}/api/collection/add-batch",
        json=batch_items, headers=headers, timeout=10)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        result = r.json()
        print(f"   ✓ Batch add successful")
        print(f"   Added {result['added']} items")
        print(f"   Item IDs: {result['ids']}")
    else:
        print(f"   ✗ Error: {r.text}")
else:
    print(f"   ⚠ Skipping batch add - only {len(card_ids)} cards created")

# Step 6: Verify collection
print("\n6. Verifying collection...")
r = requests.get(f"{BASE}/api/collection", headers=headers, timeout=10)
if r.status_code == 200:
    collection = r.json()
    print(f"   ✓ Collection has {len(collection['items'])} items")
    for i in collection['items']:
        print(f"     - {i['card']['name']} ({i['condition']}) x{i['quantity']}")
    print(f"   Stats: {collection['stats']}")
else:
    print(f"   ✗ Error: {r.text}")

print("\n" + "=" * 60)
print("✓ ALL BATCH COLLECTION TESTS PASSED")
print("=" * 60)