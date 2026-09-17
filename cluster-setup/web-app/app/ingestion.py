"""S3 ingestion-gap detection for the offline gdansk_public_transport table
— see AI_PLATFORM_PLAN.md Track 1's ingestion_gaps tool. The Lambda
(cluster-setup/py/gdansk_public_transport_aws.py) writes one file per minute
to raw/YYYY/MM/DD/YYYY-MM-DD-HH-MM.txt; a missing minute is a missing file.
"""
import datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import S3_BUCKET_NAME

# Same constant gdansk_public_transport_s3_compaction.py pins on every S3
# call, guarding against the bucket name existing in a different AWS
# account than the one this cluster's credentials belong to.
_EXPECTED_BUCKET_OWNER = '689522024200'


class IngestionUnavailableError(Exception):
    """S3 never answered (or answered with an error) — same one-exception-
    per-dependency shape as pinot.PinotUnavailableError and
    observability.ObservabilityUnavailableError. BotoCoreError covers
    SDK-level failures (no region, no credentials, connection refused);
    ClientError covers ones AWS's API itself rejected (bad bucket,
    access denied)."""


def _present_minutes(day):
    """{(HH, MM) for every raw/ file that exists for this day} via a
    paginated S3 listing."""
    s3 = boto3.client('s3')
    prefix = f"raw/{day.strftime('%Y/%m/%d')}/"
    present = set()
    try:
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(
                Bucket=S3_BUCKET_NAME, Prefix=prefix,
                ExpectedBucketOwner=_EXPECTED_BUCKET_OWNER):
            for obj in page.get('Contents', []):
                name = obj['Key'].rsplit('/', 1)[-1]
                if name.endswith('.txt') and len(name) == 20:  # YYYY-MM-DD-HH-MM.txt
                    _, _, _, hh, mm = name[:-4].split('-')
                    present.add((hh, mm))
    except (BotoCoreError, ClientError) as e:
        raise IngestionUnavailableError(f'S3 listing failed: {e}') from e
    return present


def _minute_range(start, end):
    def fmt(m):
        return '%02d:%02d' % divmod(m, 60)
    return {'start': fmt(start), 'end': fmt(end - 1), 'missingMinutes': end - start}


def _find_gaps(present, up_to_minute):
    """Contiguous missing-minute runs among the day's first `up_to_minute`
    expected minutes. Pure function (see tests/test_ingestion.py) so the
    gap-merging logic is checked without a real S3 call.
    """
    gaps = []
    run_start = None
    for minute in range(up_to_minute):
        hh, mm = divmod(minute, 60)
        missing = ('%02d' % hh, '%02d' % mm) not in present
        if missing and run_start is None:
            run_start = minute
        elif not missing and run_start is not None:
            gaps.append(_minute_range(run_start, minute))
            run_start = None
    if run_start is not None:
        gaps.append(_minute_range(run_start, up_to_minute))
    return gaps


def ingestion_gaps(day):
    """day: a datetime.date. Returns {date, expectedMinutes, missingMinutes,
    gaps: [{start, end, missingMinutes}]} — minutes the Lambda missed, the
    offline table's data-quality signal.
    """
    today = datetime.datetime.now(datetime.timezone.utc).date()
    now = datetime.datetime.now(datetime.timezone.utc)
    if day > today:
        up_to_minute = 0
    elif day < today:
        up_to_minute = 1440
    else:
        up_to_minute = now.hour * 60 + now.minute
    gaps = _find_gaps(_present_minutes(day), up_to_minute) if up_to_minute else []
    return {
        'date': day.isoformat(),
        'expectedMinutes': up_to_minute,
        'missingMinutes': sum(g['missingMinutes'] for g in gaps),
        'gaps': gaps,
    }
