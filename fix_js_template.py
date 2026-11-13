with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    js = f.read()

# Replace the createGalleryItem function to set data-supertype and data-subtypes
old_template = '''item.innerHTML = `
        <div class="holo-card ${typeCls}" data-rarity="${effect}">
            <div class="holo-card__translater">
                <div class="holo-card__rotator">
                    <img src="${imageUrl}" alt="${cardName}" loading="lazy">
                    <div class="holo-card__shine"></div>
                    <div class="holo-card__glare"></div>
                </div>
            </div>
        </div>
        <div class="gallery-item-info">
            <div class="gallery-item-name">${cardName}</div>
            <div class="gallery-item-meta">${setInfo || setId || 'Unknown Set'}</div>
        </div>
    `;'''

new_template = '''const supertype = (card.supertype || 'Pokémon').toLowerCase();
    const subtypes = (card.subtypes || []).join(' ').toLowerCase();

    item.innerHTML = `
        <div class="holo-card ${typeCls}" data-rarity="${effect}" data-supertype="${supertype}" data-subtypes="${subtypes}">
            <div class="holo-card__translater">
                <div class="holo-card__rotator">
                    <img src="${imageUrl}" alt="${cardName}" loading="lazy">
                    <div class="holo-card__shine"></div>
                    <div class="holo-card__glare"></div>
                </div>
            </div>
        </div>
        <div class="gallery-item-info">
            <div class="gallery-item-name">${cardName}</div>
            <div class="gallery-item-meta">${setInfo || setId || 'Unknown Set'}</div>
        </div>
    `;'''

if old_template in js:
    js = js.replace(old_template, new_template)
    with open("src/static/js/main.js", "w", encoding="utf-8") as f:
        f.write(js)
    print("Updated createGalleryItem to set data-supertype and data-subtypes!")
else:
    print("Could not find exact template to replace. Trying broader search...")
    # Try to find just the holo-card div
    if 'class="holo-card ${typeCls}" data-rarity="${effect}"' in js:
        js = js.replace(
            'class="holo-card ${typeCls}" data-rarity="${effect}"',
            'class="holo-card ${typeCls}" data-rarity="${effect}" data-supertype="${supertype}" data-subtypes="${subtypes}"'
        )
        # Add the variable definitions before the template
        js = js.replace(
            'const effect = classifyHoloEffect(card);',
            'const effect = classifyHoloEffect(card);\n    const supertype = (card.supertype || \'Pokémon\').toLowerCase();\n    const subtypes = (card.subtypes || []).join(\' \').toLowerCase();'
        )
        with open("src/static/js/main.js", "w", encoding="utf-8") as f:
            f.write(js)
        print("Updated with broader search!")
    else:
        print("Could not find holo-card template in JS")
