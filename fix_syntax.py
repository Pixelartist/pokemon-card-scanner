# Read the file
with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    content = f.read()

# Fix the duplicate closing brace issue
content = content.replace("return 'common';\n}", "return 'common';\n}")
content = content.replace("return 'common';\n}\n}", "return 'common';\n}")

# Verify the fix
if "function classifyHoloEffect" in content:
    # Find the function and check its structure
    import re
    pattern = r'function classifyHoloEffect\(card\) \{[^}]*\n\}'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        print("Found classifyHoloEffect function")
        func_text = match.group(0)
        # Count braces
        open_braces = func_text.count('{')
        close_braces = func_text.count('}')
        print(f"  Open braces: {open_braces}, Close braces: {close_braces}")
        
        if open_braces != close_braces:
            print("WARNING: Mismatched braces!")
        else:
            print("Braces are balanced - good!")
            
        # Save
        with open("src/static/js/main.js", "w", encoding="utf-8") as f:
            f.write(content)
        print("Saved!")
    else:
        print("Could not find function with regex")
else:
    print("classifyHoloEffect function not found!")
