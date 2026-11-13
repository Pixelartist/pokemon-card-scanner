with open("src/templates/index.html", "r", encoding="utf-8") as f:
    content = f.read()

# Fix the scan-overlay div
old = '<div id="scan-overlay" class="scan-overlay">'
new = '<div id="scan-overlay" class="scan-overlay hidden">'

if old in content:
    content = content.replace(old, new)
    with open("src/templates/index.html", "w", encoding="utf-8") as f:
        f.write(content)
    print("Fixed scan-overlay - added hidden class")
else:
    print("scan-overlay div not found with expected pattern")
    # Try to find it
    if "scan-overlay" in content:
        import re
        match = re.search(r'<div id="scan-overlay"[^>]*>', content)
        if match:
            print(f"Found: {match.group(0)}")
