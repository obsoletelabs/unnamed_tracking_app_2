"""Run inside the production container after startup has completed."""

import json
import re
import shutil
import urllib.request
from pathlib import Path

html = Path("/srv/frontend/index.html").read_text(encoding="utf-8")
assets = re.findall(r'<script[^>]+src="([^"]+)"', html)
assert assets and all(
    path.startswith("/assets/") and path.endswith(".js") for path in assets
)
assert "@vite/client" not in html and "/src/main.ts" not in html
assert all(shutil.which(program) is None for program in ("node", "npm", "vite"))

for path in Path("/proc").glob("[0-9]*/cmdline"):
    try:
        executable = path.read_bytes().split(b"\0")[0].decode(errors="replace")
    except (OSError, ProcessLookupError):
        continue
    assert Path(executable).name not in {"node", "npm", "vite"}, executable

with urllib.request.urlopen("http://127.0.0.1/", timeout=10) as response:
    live = response.read().decode()
assert "@vite/client" not in live and "/src/main.ts" not in live
assert all(asset in live for asset in assets)
print(
    json.dumps(
        {
            "compiled_entry_scripts": assets,
            "development_processes": 0,
            "serving": "Nginx static production assets",
        }
    )
)
