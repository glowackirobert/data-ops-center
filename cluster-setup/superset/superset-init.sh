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

python3 << 'IMPORTEOF'
import os, io, zipfile
from superset.app import create_app

app = create_app()
with app.app_context():
    from superset.commands.importers.v1.assets import ImportAssetsCommand
    from superset import security_manager
    from superset.utils.core import override_user

    admin_username = open('/run/secrets/superset_admin_username').read().strip()
    admin = security_manager.find_user(username=admin_username)

    for zip_path in sorted(f for f in os.listdir('/tmp') if f.endswith('.zip')):
        full_path = f'/tmp/{zip_path}'
        contents = {}
        with zipfile.ZipFile(full_path) as zf:
            for name in zf.namelist():
                if name.endswith('.yaml'):
                    contents[name] = zf.read(name).decode('utf-8')
        with override_user(admin):
            ImportAssetsCommand(contents).run()
        os.remove(full_path)
        print(f'Imported {zip_path} ({len(contents)} files, overwrite=True)')
IMPORTEOF
