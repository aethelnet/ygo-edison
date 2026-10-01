import os

script_dirs = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "script")),
    "/home/ubuntu/ygo_service/apps/script",
]

for card_id in [48686504, 15341821, 20932152]:
    lua_name = f"c{card_id}.lua"
    found = False
    for sdir in script_dirs:
        p = os.path.join(sdir, lua_name)
        if os.path.exists(p):
            print(f"Found {lua_name} in {sdir} (size: {os.path.getsize(p)})")
            found = True
            break
    if not found:
        print(f"NOT FOUND: {lua_name}")
