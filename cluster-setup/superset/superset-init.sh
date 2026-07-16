#!/bin/sh
set -e

superset db upgrade

superset fab create-admin \
  --username "$(cat /run/secrets/superset_admin_username)" \
  --firstname Robert \
  --lastname Glowacki \
  --email "$(cat /run/secrets/superset_admin_email)" \
  --password "$(cat /run/secrets/superset_admin_password)" || true

superset init

# Build zips from YAML source dirs, then import. The Mapbox API key is NOT baked
# into the charts — Superset reads it at startup via MAPBOX_API_KEY in
# superset_config.py (from the superset_mapbox_api_key secret).
# metadata.yaml type is forced to "assets": UI exports say "type: Dashboard", but
# ImportAssetsCommand only accepts "assets" and fails validation otherwise.
python3 << 'PYEOF'
import os, re, shutil

src = '/app/pythonpath/dashboards'

for entry in sorted(os.listdir(src)):
    src_dir = os.path.join(src, entry)
    if not os.path.isdir(src_dir):
        continue
    tmp_dir = f'/tmp/{entry}'
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    shutil.copytree(src_dir, tmp_dir)
    # Reset mtimes: Windows bind mounts can surface pre-1980 timestamps,
    # which the ZIP format cannot encode.
    for root, dirs, files in os.walk(tmp_dir):
        for name in dirs + files:
            os.utime(os.path.join(root, name))
        for fname in files:
            if fname == 'metadata.yaml':
                fpath = os.path.join(root, fname)
                content = re.sub(r'^type: .*$', 'type: assets', open(fpath).read(), flags=re.M)
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

        # The skip keeps routine init re-runs from clobbering runtime state.
        # After changing the dashboard/chart YAMLs, force a re-import with
        # SUPERSET_IMPORT_OVERWRITE=1 (see README-docker.md).
        if already_imported and os.environ.get('SUPERSET_IMPORT_OVERWRITE') != '1':
            print(f'Skipping {zip_path} — dashboards already exist: {dashboard_uuids}'
                  ' (set SUPERSET_IMPORT_OVERWRITE=1 to re-import)')
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

# Embedded dashboards: create the EmbeddedGuest role (Gamma perms + datasource
# access) used for guest tokens, and register every dashboard for embedding so
# the web-app (port 3001) can iframe it via the Embedded SDK.
python3 << 'EMBEDEOF'
from superset.app import create_app

app = create_app()
with app.app_context():
    from superset import db, security_manager
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.dashboard import Dashboard
    from superset.daos.dashboard import EmbeddedDashboardDAO

    role = security_manager.find_role('EmbeddedGuest') or security_manager.add_role('EmbeddedGuest')
    gamma = security_manager.find_role('Gamma')
    for pvm in gamma.permissions:
        security_manager.add_permission_role(role, pvm)
    for table in db.session.query(SqlaTable).all():
        pvm = security_manager.find_permission_view_menu('datasource_access', table.perm)
        if pvm:
            security_manager.add_permission_role(role, pvm)

    for dash in db.session.query(Dashboard).all():
        embedded = EmbeddedDashboardDAO.upsert(dash, [])
        db.session.flush()  # uuid is generated at flush; without it the print shows None
        print(f'Registered for embedding: {dash.dashboard_title} -> {embedded.uuid}')
    db.session.commit()
EMBEDEOF

superset set_database_uri \
  -d "Apache Pinot" \
  -u "${SUPERSET_PINOT_URI}"
