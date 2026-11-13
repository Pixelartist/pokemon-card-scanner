with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    js = f.read()

# Update createGalleryItem to set data-supertype and data-subtypes
old_html_start = 'item.innerHTML = `'
new_html_start = '''
    const supertype = (card.supertype || 'Pokémon').toLowerCase();
    const subtypes = (card.subtypes || []).join(' ').toLowerCase();

    item.innerHTML = `
        <div class="holo-card ${typeCls}" data-rarity="${effect}" data-supertype="${supertype}" data-subtypes="${subtypes}">'''

if old_html_start in js:
    # Find the function and update it
    js = js.replace('item.innerHTML = `', new_html_start)
    
    with open("src/static/js/main.js", "w", encoding="utf-8") as f:
        f.write(js)
    print("Updated createGalleryItem successfully!")
else:
    print("Could not find createGalleryItem template")
