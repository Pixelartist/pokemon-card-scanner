with open("src/static/css/holo-effects.css", "r", encoding="utf-8") as f:
    css = f.read()

# Find all selectors with "rare ultra"
import re
matches = re.findall(r'\.holo-card[^}]*rare ultra[^}]*\}', css, re.DOTALL)
if matches:
    print(f"Found {len(matches)} CSS selectors with 'rare ultra':")
    for i, match in enumerate(matches[:5]):
        print(f"\n  Selector {i+1}:")
        print(f"  {match[:200]}...")
else:
    print("No CSS selectors found with 'rare ultra'")
    # Try broader search
    if "rare ultra" in css:
        idx = css.find("rare ultra")
        print(f"\nFound 'rare ultra' at position {idx}")
        print(f"Context: {css[max(0,idx-200):idx+200]}")
    else:
        print("'rare ultra' NOT found in CSS file at all!")
