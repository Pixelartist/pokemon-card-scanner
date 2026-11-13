import re

# Read the current file
with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    content = f.read()

# Find and replace the classifyHoloEffect function
old_function = '''// Map our (multilingual + symbol) DB rarity values to the poke-holo.simey.me
// data-rarity effect keys that holo-effects.css selects on.
function classifyHoloEffect(card) {
    const r = (card.rarity || '').trim().toLowerCase();
    if (card.is_reverse_holo) return 'reverse holo';
    if (r.includes('vmax')) return 'rare holo vmax';
    if (r.includes('vstar')) return 'rare holo vstar';
    if (r.includes('crown') || r.includes('secret') || r === '☆☆☆' || r === '♛♛') return 'rare secret';
    if (r.includes('radiant')) return 'radiant rare';
    if (r.includes('rainbow') || r === '♛') return 'rare rainbow';
    if (r.includes('ultra') || r.includes('illustration') || r === '☆' || r === '☆☆') return 'rare ultra';
    if (/ selten v$|rare holo v$|\\bdoppelselten\\b|◇◇◇◇/.test(r)) return 'rare holo v';
    if (/holograf|rare holo|lv\\.x/.test(r)) return 'rare holo';
    return 'common';
}'''

new_function = '''// Map our (multilingual + symbol) DB rarity values to the poke-holo.simey.me
// data-rarity effect keys that holo-effects.css selects on.
function classifyHoloEffect(card) {
    const r = (card.rarity || '').trim().toLowerCase();
    if (card.is_reverse_holo) return 'reverse holo';
    if (r.includes('vmax')) return 'rare holo vmax';
    if (r.includes('vstar')) return 'rare holo vstar';
    
    // Full Art / Ultra Rare variants (includes Hidden Brilliance, Illustration Rare, etc.)
    if (r.includes('ultra') || r.includes('illustration') || r.includes('hidden brill') || 
        r.includes('full art') || r.includes('seltene illustration') || r.includes('besondere illustration') ||
        r === '☆' || r === '☆☆' || r.includes('selten, illustration')) return 'rare ultra';
    
    // Secret Rare / Crown Rare variants
    if (r.includes('crown') || r.includes('secret') || r.includes('sauer') || r.includes('holo secret') ||
        r === '☆☆☆' || r === '♛♛' || r === '♛' || r.includes('selten, besondere')) return 'rare secret';
    
    // Rainbow Rare variants
    if (r.includes('rainbow')) return 'rare rainbow';
    
    // Radiant variants
    if (r.includes('radiant') || r.includes('strahlend')) return 'radiant rare';
    
    // Pokemon V variants (German and English)
    if (/ selten v$|rare holo v$|\\bdoppelselten\\b|◇◇◇◇/.test(r)) return 'rare holo v';
    
    // Regular Holo variants
    if (/holograf|rare holo|lv\\.x/.test(r)) return 'rare holo';
    
    // Common/Uncommon/Promo (no holo effect)
    return 'common';
}'''

if old_function in content:
    content = content.replace(old_function, new_function)
    with open("src/static/js/main.js", "w", encoding="utf-8") as f:
        f.write(content)
    print("Updated classifyHoloEffect function successfully!")
else:
    print("Could not find exact match for old function. Checking...")
    # Try a simpler approach - just find the start and end
    start_idx = content.find("// Map our (multilingual")
    if start_idx != -1:
        end_idx = content.find("return 'common';\n}", start_idx)
        if end_idx != -1:
            end_idx += len("\n}")
            new_content = content[:start_idx] + new_function + content[end_idx:]
            with open("src/static/js/main.js", "w", encoding="utf-8") as f:
                f.write(new_content)
            print("Updated classifyHoloEffect function successfully!")
        else:
            print("Could not find end of function")
    else:
        print("Could not find start of function")
