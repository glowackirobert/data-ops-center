"""Compact Gdansk GPS files on S3: per-minute raw/ -> daily/, daily/ -> monthly/.

Pinot's standalone ingestion job maps input files to segments 1:1. Two
compaction stages keep segment count sane as history grows:

  daily   The Lambda fetcher stores ~1,440 small files per day under
          raw/YYYY/MM/DD/YYYY-MM-DD-HH-MM.txt - ingesting those directly
          would give ~1,440 micro segments per day. This subcommand
          concatenates one day's files in chronological order into a single
          daily/YYYY/YYYY-MM-DD.json.gz object - one segment per day. It
          also gathers files from the older bucket layouts (hourly files
          under raw/YYYY/MM/YYYY-MM-DD/ and the Oct 2025 - Jun 2026 era when
          the Lambda wrote to the bucket root without the raw/ prefix).
          Intended to run nightly, once the previous day is complete.

  monthly Even at one segment per day, the OFFLINE table's segment count (and
          the broker's per-query planning overhead) grows without bound as
          history accumulates. This subcommand re-gzips a full month's worth
          of daily/ files into a single monthly/YYYY/YYYY-MM.json.gz object,
          so re-ingesting that range collapses it down to one segment per
          month instead of ~30. Only run this against months that are fully
          elapsed: run it for the current, still-growing month and every new
          day's file forces re-ingesting (overwriting) that month's segment
          for no benefit. Intended to run once at the start of each month,
          against the month that just ended.

Neither subcommand modifies or deletes its source files (raw/ or daily/).
Both skip an already-compacted day/month unless --force is given.

Switching a range of days over to its monthly rollup is a two-step change on
the Pinot side, not handled by this script: (1) batch-ingest the new
monthly/ file, (2) delete the OFFLINE segments for the individual days it
now covers. Skipping step 2 double-counts those days, since the monthly
segment has a different name and won't overwrite the old daily ones.

Usage:
    # one day
    python gdansk_public_transport_s3_compaction.py daily --date 2025-09-01

    # full daily backfill range (inclusive)
    python gdansk_public_transport_s3_compaction.py daily --start 2025-09-01 --end 2026-07-06

    # one month
    python gdansk_public_transport_s3_compaction.py monthly --month 2025-09

    # full monthly backfill range (inclusive)
    python gdansk_public_transport_s3_compaction.py monthly --start 2025-09 --end 2026-06

    # list what would be done without downloading/uploading (either subcommand)
    python gdansk_public_transport_s3_compaction.py daily --start 2025-09-01 --end 2026-07-06 --dry-run
"""

import argparse
import concurrent.futures
import gzip
import os
import sys
import tempfile
from datetime import date, timedelta

import boto3
from botocore.config import Config

DEFAULT_BUCKET = 'gdansk-public-transport'
# AWS account that owns DEFAULT_BUCKET (same account as the S3AccessRole
# assumed in compact-s3.yml). Pinned on every S3 call so a request can never
# silently succeed against a same-named bucket in a different account (S3
# bucket names are globally unique, so bucket-name-squatting is possible).
EXPECTED_BUCKET_OWNER = '689522024200'
RAW_PREFIX = 'raw'
DAILY_PREFIX = 'daily'
MONTHLY_PREFIX = 'monthly'
JSON_GZ_SUFFIX = '.json.gz'
DAILY_DOWNLOAD_WORKERS = 16
DAILY_DOWNLOAD_BATCH = 64  # keys fetched in parallel per batch; bounds memory use
MONTHLY_DOWNLOAD_WORKERS = 8  # a month has at most ~31 source files - no batching needed

s3 = boto3.client('s3', config=Config(connect_timeout=5, read_timeout=30,
                                      retries={'max_attempts': 5, 'mode': 'adaptive'}))


def dest_exists(bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key, ExpectedBucketOwner=EXPECTED_BUCKET_OWNER)
        return True
    except s3.exceptions.ClientError as e:
        # only "not found" means the day/month is uncompacted; anything else
        # (403, throttling, ...) must not be mistaken for that
        if e.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
            return False
        raise


# --------------------------------------------------------------------------
# daily: raw/ (per-minute) -> daily/ (one file per day)
# --------------------------------------------------------------------------

def list_day_keys(bucket, day):
    # The bucket holds several historical layouts: the current Lambda writes
    # per-minute files under raw/YYYY/MM/DD/; data up to Sep 2025 has hourly
    # files under raw/YYYY/MM/YYYY-MM-DD/; a -00 day folder exists from an
    # early Lambda bug; and from Oct 2025 to Jun 2026 the Lambda wrote to the
    # BUCKET ROOT (YYYY/MM/DD/, no raw/ prefix). Files are matched to their
    # real day via the filename date, and deduplicated by filename (raw/
    # locations win over the root layout).
    iso = day.isoformat()
    month = day.strftime('%Y/%m')
    prefixes = [
        f"{RAW_PREFIX}/{day.strftime('%Y/%m/%d')}/",
        f"{RAW_PREFIX}/{month}/{iso}/",
        f"{RAW_PREFIX}/{month}/{day.strftime('%Y-%m')}-00/",
        f"{day.strftime('%Y/%m/%d')}/",
    ]
    by_name = {}
    paginator = s3.get_paginator('list_objects_v2')
    for prefix in prefixes:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix,
                                       ExpectedBucketOwner=EXPECTED_BUCKET_OWNER):
            for obj in page.get('Contents', []):
                name = obj['Key'].rsplit('/', 1)[-1]
                if obj['Key'].endswith('.txt') and name.startswith(iso):
                    by_name.setdefault(name, obj['Key'])
    # file names embed the timestamp, so sorting by name is chronological
    return [by_name[name] for name in sorted(by_name)]


def fetch_raw(bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key,
                         ExpectedBucketOwner=EXPECTED_BUCKET_OWNER)['Body'].read()
    if body and not body.endswith(b'\n'):
        body += b'\n'
    return body


def daily_dest_key(day):
    return f"{DAILY_PREFIX}/{day.strftime('%Y')}/{day.isoformat()}{JSON_GZ_SUFFIX}"


def compact_day(bucket, day, force=False, dry_run=False):
    """Returns (status, detail) where status is one of ok/skipped/empty."""
    out_key = daily_dest_key(day)
    if not force and dest_exists(bucket, out_key):
        return 'skipped', f'{out_key} already exists (use --force to overwrite)'

    keys = list_day_keys(bucket, day)
    if not keys:
        return 'empty', 'no source files'
    if dry_run:
        return 'ok', f'{len(keys)} files -> {out_key} (dry run)'

    lines = 0
    raw_bytes = 0
    tmp = tempfile.NamedTemporaryFile(suffix=JSON_GZ_SUFFIX, delete=False)
    try:
        # filename= sets the name stored in the gzip header, so decompressing
        # yields e.g. 2025-08-01.json instead of the temp file's random name
        with gzip.GzipFile(filename=f'{day.isoformat()}.json', fileobj=tmp,
                           mode='wb', compresslevel=6) as gz:
            with concurrent.futures.ThreadPoolExecutor(DAILY_DOWNLOAD_WORKERS) as pool:
                # batched map keeps chronological order while bounding
                # how many downloaded files are held in memory at once
                for i in range(0, len(keys), DAILY_DOWNLOAD_BATCH):
                    batch = keys[i:i + DAILY_DOWNLOAD_BATCH]
                    for body in pool.map(lambda k: fetch_raw(bucket, k), batch):
                        gz.write(body)
                        lines += body.count(b'\n')
                        raw_bytes += len(body)
        tmp.close()
        gz_bytes = os.path.getsize(tmp.name)
        s3.upload_file(tmp.name, bucket, out_key,
                       ExtraArgs={'ExpectedBucketOwner': EXPECTED_BUCKET_OWNER})
        return 'ok', (f'{len(keys)} files, {lines} lines, '
                      f'{raw_bytes / 1048576:.1f} MB -> {gz_bytes / 1048576:.1f} MB, '
                      f's3://{bucket}/{out_key}')
    finally:
        tmp.close()
        os.unlink(tmp.name)


def day_range(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def run_daily(args):
    if args.date:
        start = end = date.fromisoformat(args.date)
    elif args.start and args.end:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        if start > end:
            args.error('--start must not be after --end')
    else:
        args.error('provide either --date or both --start and --end')

    failures = []
    for day in day_range(start, end):
        try:
            status, detail = compact_day(args.bucket, day, args.force, args.dry_run)
            print(f'{day} {status}: {detail}', flush=True)
        except Exception as e:
            failures.append((day, e))
            print(f'{day} FAILED: {e}', file=sys.stderr, flush=True)

    if failures:
        print(f'\n{len(failures)} day(s) failed: '
              + ', '.join(str(d) for d, _ in failures), file=sys.stderr)
        sys.exit(1)
    print('\nAll days processed.')


# --------------------------------------------------------------------------
# monthly: daily/ (one file per day) -> monthly/ (one file per month)
# --------------------------------------------------------------------------

def list_month_keys(bucket, year, month):
    prefix = f"{DAILY_PREFIX}/{year:04d}/{year:04d}-{month:02d}-"
    keys = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix,
                                   ExpectedBucketOwner=EXPECTED_BUCKET_OWNER):
        for obj in page.get('Contents', []):
            if obj['Key'].endswith(JSON_GZ_SUFFIX):
                keys.append(obj['Key'])
    # filenames embed the date, so sorting by key is chronological
    return sorted(keys)


def fetch_daily_decompressed(bucket, key):
    body = gzip.decompress(s3.get_object(Bucket=bucket, Key=key,
                                         ExpectedBucketOwner=EXPECTED_BUCKET_OWNER)['Body'].read())
    if body and not body.endswith(b'\n'):
        body += b'\n'
    return body


def monthly_dest_key(year, month):
    return f"{MONTHLY_PREFIX}/{year:04d}/{year:04d}-{month:02d}{JSON_GZ_SUFFIX}"


def compact_month(bucket, year, month, force=False, dry_run=False):
    """Returns (status, detail) where status is one of ok/skipped/empty."""
    out_key = monthly_dest_key(year, month)
    if not force and dest_exists(bucket, out_key):
        return 'skipped', f'{out_key} already exists (use --force to overwrite)'

    keys = list_month_keys(bucket, year, month)
    if not keys:
        return 'empty', 'no source files'
    if dry_run:
        return 'ok', f'{len(keys)} files -> {out_key} (dry run)'

    lines = 0
    raw_bytes = 0
    label = f'{year:04d}-{month:02d}'
    tmp = tempfile.NamedTemporaryFile(suffix=JSON_GZ_SUFFIX, delete=False)
    try:
        # filename= sets the name stored in the gzip header, so decompressing
        # yields e.g. 2026-01.json instead of the temp file's random name
        with gzip.GzipFile(filename=f'{label}.json', fileobj=tmp,
                           mode='wb', compresslevel=6) as gz:
            with concurrent.futures.ThreadPoolExecutor(MONTHLY_DOWNLOAD_WORKERS) as pool:
                for body in pool.map(lambda k: fetch_daily_decompressed(bucket, k), keys):
                    gz.write(body)
                    lines += body.count(b'\n')
                    raw_bytes += len(body)
        tmp.close()
        gz_bytes = os.path.getsize(tmp.name)
        s3.upload_file(tmp.name, bucket, out_key,
                       ExtraArgs={'ExpectedBucketOwner': EXPECTED_BUCKET_OWNER})
        return 'ok', (f'{len(keys)} files, {lines} lines, '
                      f'{raw_bytes / 1048576:.1f} MB -> {gz_bytes / 1048576:.1f} MB, '
                      f's3://{bucket}/{out_key}')
    finally:
        tmp.close()
        os.unlink(tmp.name)


def month_range(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def parse_month(s):
    d = date.fromisoformat(f'{s}-01')
    return d.year, d.month


def run_monthly(args):
    if args.month:
        start = end = parse_month(args.month)
    elif args.start and args.end:
        start, end = parse_month(args.start), parse_month(args.end)
        if start > end:
            args.error('--start must not be after --end')
    else:
        args.error('provide either --month or both --start and --end')

    failures = []
    for year, month in month_range(start, end):
        label = f'{year:04d}-{month:02d}'
        try:
            status, detail = compact_month(args.bucket, year, month, args.force, args.dry_run)
            print(f'{label} {status}: {detail}', flush=True)
        except Exception as e:
            failures.append((label, e))
            print(f'{label} FAILED: {e}', file=sys.stderr, flush=True)

    if failures:
        print(f'\n{len(failures)} month(s) failed: '
              + ', '.join(label for label, _ in failures), file=sys.stderr)
        sys.exit(1)
    print('\nAll months processed.')


# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    daily = sub.add_parser('daily', help='raw/ (per-minute) -> daily/ (one file per day)')
    daily.add_argument('--bucket', default=DEFAULT_BUCKET)
    daily.add_argument('--date', help='single day, YYYY-MM-DD')
    daily.add_argument('--start', help='range start, YYYY-MM-DD (inclusive)')
    daily.add_argument('--end', help='range end, YYYY-MM-DD (inclusive)')
    daily.add_argument('--force', action='store_true', help='overwrite existing daily files')
    daily.add_argument('--dry-run', action='store_true', help='list source files only')
    daily.set_defaults(func=run_daily, error=daily.error)

    monthly = sub.add_parser('monthly', help='daily/ (one file per day) -> monthly/ (one file per month)')
    monthly.add_argument('--bucket', default=DEFAULT_BUCKET)
    monthly.add_argument('--month', help='single month, YYYY-MM')
    monthly.add_argument('--start', help='range start, YYYY-MM (inclusive)')
    monthly.add_argument('--end', help='range end, YYYY-MM (inclusive)')
    monthly.add_argument('--force', action='store_true', help='overwrite existing monthly files')
    monthly.add_argument('--dry-run', action='store_true', help='list source files only')
    monthly.set_defaults(func=run_monthly, error=monthly.error)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
