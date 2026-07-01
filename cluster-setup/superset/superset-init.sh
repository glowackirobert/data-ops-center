#!/bin/sh
set -e

superset db upgrade

superset fab create-admin \
  --username "$(cat /run/secrets/superset_admin_username)" \
  --firstname Admin \
  --lastname Admin \
  --email "$(cat /run/secrets/superset_admin_email)" \
  --password "$(cat /run/secrets/superset_admin_password)" || true

superset init

superset set_database_uri \
  -d "Apache Pinot" \
  -u "${SUPERSET_PINOT_URI}"

# Build zips from YAML source dirs, substituting the MapTiler API key, then import
python3 << 'PYEOF'
import os, shutil

maptiler_key = open('/run/secrets/superset_maptiler_api_key').read().strip()
src = '/app/pythonpath/dashboards'

for entry in sorted(os.listdir(src)):
    src_dir = os.path.join(src, entry)
    if not os.path.isdir(src_dir):
        continue
    tmp_dir = f'/tmp/{entry}'
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    shutil.copytree(src_dir, tmp_dir)
    for root, _, files in os.walk(tmp_dir):
        for fname in files:
            if fname.endswith('.yaml'):
                fpath = os.path.join(root, fname)
                content = open(fpath).read().replace('__MAPTILER_API_KEY__', maptiler_key)
                open(fpath, 'w').write(content)
    shutil.make_archive(f'/tmp/{entry}', 'zip', '/tmp', entry)
    shutil.rmtree(tmp_dir)
    print(f'Prepared /tmp/{entry}.zip')
PYEOF

for zip_file in /tmp/*.zip; do
  [ -f "$zip_file" ] || continue
  superset import_dashboards -p "$zip_file" --username "$(cat /run/secrets/superset_admin_username)" || true
  rm -f "$zip_file"
done
