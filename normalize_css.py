with open("src/static/css/holo-effects.css", "r", encoding="utf-8") as f:
    content = f.read()

# Replace accented pokémon with plain pokemon to match JS normalization
# Note: The file might have encoding issues, so we'll look for both
import re
new_content = content.replace('data-supertype="pokémon"', 'data-supertype="pokemon"')
new_content = new_content.replace('data-supertype="pokmon"', 'data-supertype="pokemon"')

if new_content != content:
    with open("src/static/css/holo-effects.css", "w", encoding="utf-8") as f:
        f.write(new_content)
    print("Normalized CSS selectors (pokémon -> pokemon)")
else:
    print("No changes needed in CSS or could not find accented selectors")
