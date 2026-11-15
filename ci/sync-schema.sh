#!/bin/sh
# Copies the schema (agentric-os-schema, next to this repository or at $SCHEMA) into schema/ and its checker into
# server/, so the plugin can build configurations without an installation. Run after the schema changes, and commit
# what it copied with the change that needs it.
set -eu
cd "$(dirname "$0")/.."
from="${SCHEMA:-../agentric-os-schema}"
[ -f "$from/src/main/resources/agentric-os-schema/index.json" ] || { echo "xx the schema is not at $from: set SCHEMA"; exit 1; }
rm -rf schema
mkdir -p schema
cp -R "$from/src/main/resources/agentric-os-schema/." schema/
cp "$from/LICENSE" "$from/NOTICE" schema/
cp "$from/python/schema_check.py" server/schema_check.py
echo "== schema $(python3 -c 'import json;print(json.load(open("schema/index.json"))["version"])') copied"
