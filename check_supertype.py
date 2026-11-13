with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    js = f.read()

# Find the createGalleryItem function
if "function createGalleryItem" in js:
    idx = js.find("function createGalleryItem")
    end_idx = js.find("function initGallery", idx)
    func_text = js[idx:end_idx]
    
    print("createGalleryItem function:")
    print(func_text[:1500])
    
    # Check for data-supertype
    if "data-supertype" in func_text:
        print("\n\nFound data-supertype attribute")
    else:
        print("\n\nWARNING: data-supertype attribute NOT found in createGalleryItem!")
        print("The CSS selectors require data-supertype='pokemon' for the full art effect to work!")
