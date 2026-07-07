"""Compact per-minute Gdansk GPS files on S3 into one gzipped JSONL file per day.

The Lambda fetcher stores ~1,440 small files per day under
raw/YYYY/MM/DD/YYYY-MM-DD-HH-MM.txt. Pinot's standalone ingestion job maps
input files to segments 1:1, so ingesting a raw day produces ~1,440 micro
segments and needs a lot of memory. This script concatenates each day's files
in chronological order into a single daily/YYYY/YYYY-MM-DD.json.gz object,
which Pinot's JSON record reader consumes directly - one segment per day.
It also gathers files from the older bucket layouts (hourly files under
raw/YYYY/MM/YYYY-MM-DD/ and the Oct 2025 - Jun 2026 era when the Lambda wrote
to the bucket root without the raw/ prefix).

The raw/ files are never modified or deleted.

Usage:
    # one day
    python gdansk_public_transport_s3_compaction.py --date 2025-09-01

    # full backfill range (inclusive)
    python gdansk_public_transport_s3_compaction.py --start 2025-09-01 --end 2026-07-06

    # list what would be done without downloading/uploading
    python gdansk_public_transport_s3_compaction.py --start 2025-09-01 --end 2026-07-06 --dry-run
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
SRC_PREFIX = 'raw'
DEST_PREFIX = 'daily'
DOWNLOAD_WORKERS = 16
DOWNLOAD_BATCH = 64  # keys fetched in parallel per batch; bounds memory use

s3 = boto3.client('s3', config=Config(connect_timeout=5, read_timeout=30,
                                      retries={'max_attempts': 5, 'mode': 'adaptive'}))


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
        f"{SRC_PREFIX}/{day.strftime('%Y/%m/%d')}/",
        f"{SRC_PREFIX}/{month}/{iso}/",
        f"{SRC_PREFIX}/{month}/{day.strftime('%Y-%m')}-00/",
        f"{day.strftime('%Y/%m/%d')}/",
    ]
    by_name = {}
    paginator = s3.get_paginator('list_objects_v2')
    for prefix in prefixes:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                name = obj['Key'].rsplit('/', 1)[-1]
                if obj['Key'].endswith('.txt') and name.startswith(iso):
                    by_name.setdefault(name, obj['Key'])
    # file names embed the timestamp, so sorting by name is chronological
    return [by_name[name] for name in sorted(by_name)]


def fetch(bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
    if body and not body.endswith(b'\n'):
        body += b'\n'
    return body


def dest_key(day):
    return f"{DEST_PREFIX}/{day.strftime('%Y')}/{day.isoformat()}.json.gz"


def dest_exists(bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except s3.exceptions.ClientError:
        return False


def compact_day(bucket, day, force=False, dry_run=False):
    """Returns (status, detail) where status is one of ok/skipped/empty."""
    out_key = dest_key(day)
    if not force and dest_exists(bucket, out_key):
        return 'skipped', f'{out_key} already exists (use --force to overwrite)'

    keys = list_day_keys(bucket, day)
    if not keys:
        return 'empty', 'no source files'
    if dry_run:
        return 'ok', f'{len(keys)} files -> {out_key} (dry run)'

    lines = 0
    raw_bytes = 0
    tmp = tempfile.NamedTemporaryFile(suffix='.json.gz', delete=False)
    try:
        # filename= sets the name stored in the gzip header, so decompressing
        # yields e.g. 2025-08-01.json instead of the temp file's random name
        with gzip.GzipFile(filename=f'{day.isoformat()}.json', fileobj=tmp,
                           mode='wb', compresslevel=6) as gz:
            with concurrent.futures.ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
                # batched map keeps chronological order while bounding
                # how many downloaded files are held in memory at once
                for i in range(0, len(keys), DOWNLOAD_BATCH):
                    batch = keys[i:i + DOWNLOAD_BATCH]
                    for body in pool.map(lambda k: fetch(bucket, k), batch):
                        gz.write(body)
                        lines += body.count(b'\n')
                        raw_bytes += len(body)
        tmp.close()
        gz_bytes = os.path.getsize(tmp.name)
        s3.upload_file(tmp.name, bucket, out_key)
        return 'ok', (f'{len(keys)} files, {lines} lines, '
                      f'{raw_bytes >> 20} MB -> {gz_bytes >> 20} MB, s3://{bucket}/{out_key}')
    finally:
        tmp.close()
        os.unlink(tmp.name)


def day_range(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--bucket', default=DEFAULT_BUCKET)
    parser.add_argument('--date', help='single day, YYYY-MM-DD')
    parser.add_argument('--start', help='range start, YYYY-MM-DD (inclusive)')
    parser.add_argument('--end', help='range end, YYYY-MM-DD (inclusive)')
    parser.add_argument('--force', action='store_true', help='overwrite existing daily files')
    parser.add_argument('--dry-run', action='store_true', help='list source files only')
    args = parser.parse_args()

    if args.date:
        start = end = date.fromisoformat(args.date)
    elif args.start and args.end:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        if start > end:
            parser.error('--start must not be after --end')
    else:
        parser.error('provide either --date or both --start and --end')

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


if __name__ == '__main__':
    main()
