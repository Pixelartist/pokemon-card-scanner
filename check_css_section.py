with open("src/static/css/holo-effects.css", "r", encoding="utf-8") as f:
    css = f.read()

# Find the v-full-art section
idx = css.find("rare ultra")
if idx > 0:
    start = css.rfind("/*", 0, idx)
    end = css.find("/*", idx + 100)
    if end == -1:
        end = idx + 2000
    
    section = css[start:end]
    print("CSS section for rare ultra:")
    print(section[:1500])
