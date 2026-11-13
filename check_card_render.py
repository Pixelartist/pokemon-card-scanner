with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    js = f.read()

# Find where holo-card is created
import re
matches = re.findall(r'holo-card[^"]*', js)
if matches:
    print("Found holo-card references:")
    for m in matches[:10]:
        print(f"  {m}")

# Find the createGalleryItem function
if "function createGalleryItem" in js:
    print("\nFound createGalleryItem function")
    # Find the holo-card creation
    idx = js.find("function createGalleryItem")
    snippet = js[idx:idx+1000]
    if "holo-card" in snippet:
        print("  Creates holo-card elements")
        # Show the relevant part
        holo_idx = snippet.find("holo-card")
        print(f"  Context: ...{snippet[max(0,holo_idx-50):holo_idx+200]}...")
