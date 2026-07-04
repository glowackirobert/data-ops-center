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

# Build zips from YAML source dirs, substituting the MapTiler API key, then import.
# metadata.yaml type is forced to "assets": UI exports say "type: Dashboard", but
# ImportAssetsCommand only accepts "assets" and fails validation otherwise.
python3 << 'PYEOF'
import os, re, shutil

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
                if fname == 'metadata.yaml':
                    content = re.sub(r'^type: .*$', 'type: assets', content, flags=re.M)
                open(fpath, 'w').write(content)
    shutil.make_archive(f'/tmp/{entry}', 'zip', '/tmp', entry)
    shutil.rmtree(tmp_dir)
    print(f'Prepared /tmp/{entry}.zip')
PYEOF

python3 << 'IMPORTEOF'
import os, zipfile, yaml, traceback
from superset.app import create_app

app = create_app()
with app.app_context():
    from superset.commands.importers.v1.assets import ImportAssetsCommand
    from superset import security_manager
    from superset.utils.core import override_user
    from superset.models.dashboard import Dashboard
    from superset import db

    admin_username = open('/run/secrets/superset_admin_username').read().strip()
    admin = security_manager.find_user(username=admin_username)

    for zip_path in sorted(f for f in os.listdir('/tmp') if f.endswith('.zip')):
        full_path = f'/tmp/{zip_path}'
        contents = {}
        dashboard_uuids = []
        with zipfile.ZipFile(full_path) as zf:
            for name in zf.namelist():
                if not name.endswith('.yaml'):
                    continue
                parts = name.split('/', 1)
                key = parts[1] if len(parts) > 1 else name
                raw = zf.read(name).decode('utf-8')
                contents[key] = raw
                if key.startswith('dashboards/'):
                    parsed = yaml.safe_load(raw)
                    if 'uuid' in parsed:
                        dashboard_uuids.append(str(parsed['uuid']))

        already_imported = all(
            db.session.query(Dashboard).filter_by(uuid=u).first()
            for u in dashboard_uuids
        ) if dashboard_uuids else False

        if already_imported:
            print(f'Skipping {zip_path} — dashboards already exist: {dashboard_uuids}')
            os.remove(full_path)
            continue

        print(f'Importing {zip_path} ({len(contents)} files)...')
        try:
            with override_user(admin):
                ImportAssetsCommand(contents, overwrite=True).run()
            os.remove(full_path)
            print(f'Imported {zip_path} successfully')
        except Exception as e:
            cause = getattr(e, '__cause__', None) or e
            print(f'ERROR: {type(e).__name__}: {e}')
            nm = getattr(cause, 'normalized_messages', None)
            if nm:
                print(f'  validation errors: {nm()}')
            for sub in getattr(cause, '_exceptions', []) or []:
                print(f'  validation error: {type(sub).__name__}: {repr(sub)}')
            traceback.print_exc()
            raise
IMPORTEOF

superset set_database_uri \
  -d "Apache Pinot" \
  -u "${SUPERSET_PINOT_URI}"
