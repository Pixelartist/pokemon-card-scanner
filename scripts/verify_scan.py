#!/usr/bin/env python3
"""Verify cloud /api/scan returns the correct top-1 after the index rebuild."""
import json, sys, urllib.request, urllib.error

BASE = "http://localhost:5005"
USER = "scanverify_user"
PW = "ScanVerify!2026"

def post_json(path, data, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}",
                                 data=json.dumps(data).encode(), headers=headers)
    return json.load(urllib.request.urlopen(req))

try:
    tok = post_json("/api/auth/login", {"username": USER, "password": PW})["access_token"]
except urllib.error.HTTPError:
    tok = post_json("/api/auth/register", {"username": USER, "email": f"{USER}@test.local", "password": PW})["access_token"]

def scan(path, expected):
    with open(path, "rb") as f:
        body = f.read()
    # multipart upload
    boundary = "----scanverify"
    parts = (f"--{boundary}\r\n"
             'Content-Disposition: form-data; name="image"; filename="card.png"\r\n'
             "Content-Type: image/png\r\n\r\n").encode() + body + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{BASE}/api/scan", data=parts, headers={
        "Authorization": f"Bearer {tok}",
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    d = json.load(urllib.request.urlopen(req))
    matches = d.get("candidates") or []
    top = matches[0] if matches else {}
    ok = "OK      " if top.get("id") == expected else "MISMATCH"
    print(f"{ok} {expected}: top={top.get('id')} "
          f"({top.get('name','?')}) sim={top.get('similarity', 0):.4f}")
    for m in matches[1:3]:
        print(f"          next: {m.get('id','?')} sim={m.get('similarity',0):.4f}")
    return ok == "OK      "

results = [
    scan("data/images/official/en/gym2-1.png", "gym2-1"),
    scan("data/german_cards/images/de_sm12-155.png", "de_sm12-155"),
    scan("data/images/official/en/bw4-13.png", "bw4-13"),
]
print("ALL PASS" if all(results) else "FAILURES PRESENT")
sys.exit(0 if all(results) else 1)
