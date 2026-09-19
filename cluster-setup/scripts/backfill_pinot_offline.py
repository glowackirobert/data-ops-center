"""Backfill the gdansk_public_transport OFFLINE table one day at a time.

Rationale, the manifest, and --force are documented in README-docker.md's
"Backfilling a range of days" section — read that before using this script.

Usage:
  python cluster-setup/scripts/backfill_pinot_offline.py [--env dev|prod]
      [--start-date 2026-01-01] [--end-date 2026-01-31] [--force] [--dry-run]
      [--continue-on-error] [--parallelism 4] [--insecure]

Requires the AWS CLI (already a project prerequisite) and Docker Compose.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "cluster-setup" / "container" / "container-compose.yml"
VERSIONS_ENV = REPO_ROOT / "cluster-setup" / "env" / "versions.env"
DEFAULT_AWS_CREDENTIALS_FILE = REPO_ROOT / "cluster-setup" / "container" / "secrets" / "aws_credentials"
INGESTED_MANIFEST = REPO_ROOT / "cluster-setup" / "volumes" / "pinot" / "ingested_dates.json"

DAILY_FILE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\.json\.gz$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def aws_env():
    run_env = dict(os.environ)
    if DEFAULT_AWS_CREDENTIALS_FILE.exists():
        run_env.setdefault("AWS_SHARED_CREDENTIALS_FILE", str(DEFAULT_AWS_CREDENTIALS_FILE))
    return run_env


def list_daily_dates(bucket, prefix, insecure):
    cmd = ["aws", "s3", "ls", f"s3://{bucket}/{prefix}", "--recursive"]
    if insecure:
        cmd.append("--no-verify-ssl")
    result = subprocess.run(cmd, capture_output=True, text=True, env=aws_env())
    if result.returncode != 0:
        sys.exit(f"aws s3 ls failed:\n{result.stderr}")
    dates = {}
    for line in result.stdout.splitlines():
        m = DAILY_FILE_RE.search(line)
        if m:
            dates[m.group(1)] = line.split()[-1]  # key, e.g. squashed/2026/2026-01-30.json.gz
    return dates


def load_manifest():
    if not INGESTED_MANIFEST.exists():
        return {}
    return json.loads(INGESTED_MANIFEST.read_text())


def save_manifest(manifest):
    INGESTED_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = INGESTED_MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    tmp.replace(INGESTED_MANIFEST)


def run_one_day(env_name, day, parallelism, continue_on_error):
    env_file = REPO_ROOT / "cluster-setup" / "env" / f"env.{env_name}"
    cmd = [
        "docker", "compose",
        "--env-file", str(VERSIONS_ENV),
        "--env-file", str(env_file),
        "-f", str(COMPOSE_FILE),
        "--profile", "init",
        "run", "--rm", "--no-deps", "pinot-ingestion-runner",
    ]
    run_env = aws_env()
    run_env["INGESTION_DATE"] = day
    if parallelism:
        run_env["INGESTION_JOB_PARALLELISM"] = str(parallelism)

    print(f"\n=== Ingesting {day} ===")
    result = subprocess.run(cmd, cwd=REPO_ROOT, env=run_env)
    if result.returncode != 0:
        print(f"=== FAILED: {day} (exit {result.returncode}) ===", file=sys.stderr)
        if not continue_on_error:
            sys.exit(1)
        return False
    print(f"=== Done: {day} ===")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", choices=["dev", "prod"], default="dev",
                         help="which env file to use (default: dev)")
    parser.add_argument("--bucket", default="gdansk-public-transport")
    parser.add_argument("--prefix", default="squashed/")
    parser.add_argument("--start-date", type=str, default=None, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--parallelism", type=int, default=None,
                         help="overrides INGESTION_JOB_PARALLELISM for each day's run")
    parser.add_argument("--force", action="store_true",
                         help="re-ingest dates already recorded in the manifest "
                              "(can double-count already-merged days)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    parser.add_argument("--continue-on-error", action="store_true",
                         help="keep going after a day fails instead of stopping immediately")
    parser.add_argument("--insecure", action="store_true",
                         help="pass --no-verify-ssl to the aws s3 ls call (local TLS-intercepting proxy)")
    args = parser.parse_args()

    if args.start_date and not DATE_RE.match(args.start_date):
        sys.exit(f"--start-date must be YYYY-MM-DD, got {args.start_date}")
    if args.end_date and not DATE_RE.match(args.end_date):
        sys.exit(f"--end-date must be YYYY-MM-DD, got {args.end_date}")

    print(f"Listing s3://{args.bucket}/{args.prefix} ...")
    daily_files = list_daily_dates(args.bucket, args.prefix, args.insecure)
    all_dates = set(daily_files)
    if args.start_date:
        all_dates = {d for d in all_dates if d >= args.start_date}
    if args.end_date:
        all_dates = {d for d in all_dates if d <= args.end_date}

    manifest = load_manifest()
    already_done = set(manifest) & all_dates
    todo = sorted(all_dates if args.force else all_dates - already_done)
    skipped = sorted(all_dates & already_done) if not args.force else []

    print(f"Found {len(all_dates)} daily file(s) in range, "
          f"{len(skipped)} already in manifest ({INGESTED_MANIFEST}).")
    print(f"To ingest: {len(todo)}")
    if todo:
        print("  " + ", ".join(todo))

    if args.dry_run:
        print("\n--dry-run: not invoking docker compose.")
        return

    succeeded, failed = [], []
    for day in todo:
        ok = run_one_day(args.env, day, args.parallelism, args.continue_on_error)
        if ok:
            succeeded.append(day)
            manifest[day] = {
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "source_file": f"s3://{args.bucket}/{daily_files[day]}",
            }
            save_manifest(manifest)
        else:
            failed.append(day)

    print(f"\nDone: {len(succeeded)} succeeded, {len(failed)} failed, {len(skipped)} already in manifest (skipped).")
    if failed:
        print("Failed: " + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
