#!/usr/bin/env bash
# Assemble the static screens into ``dist/`` for a file host (Cloudflare Pages,
# Netlify, GitHub Pages). The app is normally served by FastAPI, where absolute
# ``/static/...`` paths resolve; a bare file host has no such prefix, so the
# entry page is rewritten to relative paths and the API origin is read from
# ``api-config.js`` at runtime.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dist="${DIST:-$root/dist}"

rm -rf "$dist"
mkdir -p "$dist"

cp -r "$root/static" "$dist/static"
cp -r "$root/legacy" "$dist/app"
cp "$root/legacy/api-config.js" "$dist/api-config.js"
cp "$root/static/sw.js" "$dist/sw.js"
cp "$root/static/showcase.html" "$dist/showcase.html"

# The legacy screens live under /app/ but must read the one config file at the
# host root, so their relative reference climbs one level.
for page in "$dist"/app/index.html "$dist"/app/auth.html "$dist"/app/change-password.html; do
    sed -i 's#"\?\./api-config\.js"#"../api-config.js"#g' "$page"
done
rm -f "$dist/app/api-config.js"

# A file host has no server to substitute the cache-buster, so stamp it here.
version="$(date -u +%Y%m%d%H%M%S)"

# The entry page is served at the host root, so its absolute asset paths become
# relative to that root.
sed -e 's#"/static/#"./static/#g' \
    -e 's#"/api-config.js"#"./api-config.js"#g' \
    -e "s#__ASSET_VERSION__#$version#g" \
    "$root/static/index.html" > "$dist/index.html"

sed -e 's#"/static/#"./static/#g' \
    -e 's#"/api-config.js"#"./api-config.js"#g' \
    "$root/static/showcase.html" > "$dist/showcase.html"

# The manifest is fetched relative to the page, so its own paths must be too.
python3 - "$root/static/manifest.webmanifest" "$dist/static/manifest.webmanifest" <<'PYMANIFEST'
import json, sys
source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["start_url"] = "./"
data["scope"] = "./"
for icon in data.get("icons", []):
    icon["src"] = icon["src"].replace("/static/", "./")
json.dump(data, open(target, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYMANIFEST

echo "dist/ ready — upload its contents, then set the API address in api-config.js"
