# Read the main.js file
with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    content = f.read()

# Find and replace the classifyHoloEffect function
import re

# Pattern to match the entire function
pattern = r'(// Map our.*?function classifyHoloEffect\(card\) \{.*?return \'common\';\s*\})'

match = re.search(pattern, content, re.DOTALL)
if match:
    old_func = match.group(1)
    
    new_func = '''// Map our (multilingual + symbol) DB rarity values to the poke-holo.simey.me
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
    
    content = content.replace(old_func, new_func)
    
    with open("src/static/js/main.js", "w", encoding="utf-8") as f:
        f.write(content)
    
    print("Updated classifyHoloEffect function!")
else:
    print("Could not find function to replace")
    print("Checking if function exists...")
    if "function classifyHoloEffect" in content:
        print("Function exists but pattern didn't match")
