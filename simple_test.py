#!/usr/bin/env python3
"""Simple authentication test script."""
import requests
import json

BASE = "http://localhost:5005"

# Test 1: Register a user
print("Test 1: Registering user...")
register_data = {
    "username": "simple_test_user",
    "email": "simple@test.com",
    "password": "password123",
    "full_name": "Simple Test User"
}

r = requests.post(f"{BASE}/api/auth/register", json=register_data, timeout=10)
print(f"   Status: {r.status_code}")
if r.status_code != 200:
    print(f"   Error: {r.text}")
    exit(1)

token_data = r.json()
token = token_data['access_token']
print(f"   ✓ Registration successful")
print(f"   Token: {token[:50]}...")

# Test 2: Test auth/me endpoint
print("\nTest 2: Testing auth/me endpoint...")
headers = {"Authorization": f"Bearer {token}"}
r = requests.get(f"{BASE}/api/auth/me", headers=headers, timeout=10)
print(f"   Status: {r.status_code}")
if r.status_code == 200:
    user_data = r.json()
    print(f"   ✓ Authenticated user: {user_data['username']}")
else:
    print(f"   ✗ Error: {r.text}")

# Test 3: Test login with same credentials
print("\nTest 3: Testing login endpoint...")
login_data = {
    "username": "simple_test_user",
    "password": "password123"
}
r = requests.post(f"{BASE}/api/auth/login", json=login_data, timeout=10)
print(f"   Status: {r.status_code}")
if r.status_code == 200:
    login_result = r.json()
    print(f"   ✓ Login successful")
    print(f"   Token: {login_result['access_token'][:50]}...")
else:
    print(f"   ✗ Error: {r.text}")

# Test 4: Test batch collection add
print("\nTest 4: Testing batch collection add...")
# First, need to create some cards
print("   Creating test cards...")
card_ids = []
for cid in ['xy1-124', 'xy2-124']:
    r = requests.post(f"{BASE}/api/cards/{cid}/create-or-get", timeout=10)
    if r.status_code == 200:
        card_ids.append(r.json()['card_id'])
        print(f"   Card {cid}: created (ID: {r.json()['card_id']})")
    else:
        print(f"   Card {cid}: failed - {r.text}")

if len(card_ids) >= 2:
    # Add to collection
    batch_items = [
        {"card_id": card_ids[0], "condition": "Near Mint", "quantity": 2},
        {"card_id": card_ids[1], "condition": "Light Play", "quantity": 1},
    ]
    
    r = requests.post(f"{BASE}/api/collection/add-batch", 
        json=batch_items, headers=headers, timeout=10)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        result = r.json()
        print(f"   ✓ Batch add successful: {result}")
    else:
        print(f"   ✗ Error: {r.text}")
else:
    print("   ⚠ Skipping batch add - not enough cards created")

print("\n=== ALL TESTS COMPLETED ===")