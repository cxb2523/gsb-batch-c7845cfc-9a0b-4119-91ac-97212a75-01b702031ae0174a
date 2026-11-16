"""Incremental Twitter ETL.

The pipeline no longer overwrites ``refined_tweets.csv`` with the latest 100
tweets on every run.  Instead it:

1. Reads the tweets already stored in S3 and treats them as the lower bound
   for the current run.
2. Pages through the Twitter API (up to ``max_tweets`` new tweets) and keeps
   every tweet collected so far, even if a later page fails.
3. Merges the freshly fetched tweets with the existing ones, de-duplicating
   by tweet ``id``.
4. Skips the S3 upload entirely when there is nothing new so an empty API
   response can never wipe out historical data.
5. Writes the combined result sorted by ``created_at`` so repeated runs keep
   producing a stable file.

Both the Twitter client and the S3 client are injected into
:func:`run_twitter_etl`, which keeps the whole flow testable offline with
fake clients.  The real tweepy/boto3 clients are only built lazily inside the
factory helpers, so importing this module does not require tweepy or a
``config.py`` to be present.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

BUCKET_NAME = 'kishlay-airflow-twitter-project'
OBJECT_KEY = 'refined_tweets.csv'
TWITTER_HANDLE = 'elonmusk'
PAGE_SIZE = 100
DEFAULT_MAX_TWEETS = 1000

# ``id`` is the new first column used for de-duplication.  The remaining
# columns are the ones already present in the legacy CSV files.
COLUMNS = [
    'id',
    'user',
    'username',
    'text',
    'like_count',
    'reply_count',
    'retweet_count',
    'created_at',
]

TWEET_FIELDS = ['id', 'text', 'created_at', 'public_metrics']

# Statuses reported on :class:`EtlResult`.
STATUS_UPLOADED = 'uploaded'
STATUS_SKIPPED_EMPTY = 'skipped_empty'
STATUS_PARTIAL_FAILURE = 'partial_failure'

# S3 / botocore error codes that mean "the object does not exist yet".
_NOT_FOUND_CODES = {'NoSuchKey', 'NotFound', '404'}


@dataclass
class EtlResult:
    """Observable outcome of a single ETL run.

    Returned by :func:`run_twitter_etl` (Airflow stores it via XCom when
    configured) and emitted through the logger, so both successful skips and
    partial failures are visible instead of failing silently.
    """

    status: str
    uploaded: bool
    message: str
    bucket: str
    key: str
    existing_tweets: int = 0
    fetched_tweets: int = 0
    new_tweets: int = 0
    duplicate_tweets: int = 0
    total_tweets: int = 0
    pages_fetched: int = 0
    errors: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            'status': self.status,
            'uploaded': self.uploaded,
            'message': self.message,
            'bucket': self.bucket,
            'key': self.key,
            'existing_tweets': self.existing_tweets,
            'fetched_tweets': self.fetched_tweets,
            'new_tweets': self.new_tweets,
            'duplicate_tweets': self.duplicate_tweets,
            'total_tweets': self.total_tweets,
            'pages_fetched': self.pages_fetched,
            'errors': list(self.errors),
        }


def _is_not_found_error(exc: Exception) -> bool:
    """Return True when *exc* represents a missing S3 object.

    Works with real ``botocore.exceptions.ClientError`` objects as well as
    the minimal fake errors used in tests.
    """

    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        code = response.get('Error', {}).get('Code')
        if code in _NOT_FOUND_CODES:
            return True
    return str(exc) in _NOT_FOUND_CODES


def _empty_dataframe() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype='string') for column in COLUMNS})


def read_existing_tweets(s3_client: Any,
                         bucket_name: str = BUCKET_NAME,
                         object_key: str = OBJECT_KEY) -> Tuple[pd.DataFrame, bool]:
    """Read the previously stored CSV from S3.

    Returns a ``(dataframe, existed)`` tuple.  A missing object (404 /
    NoSuchKey) is treated as a first run and yields an empty dataframe with
    ``existed=False``.  Any other S3 failure is re-raised so the caller can
    abort instead of overwriting history with empty data.
    """

    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    except Exception as exc:  # noqa: BLE001 - re-raising non-404 errors below
        if _is_not_found_error(exc):
            logger.info('S3 object s3://%s/%s does not exist yet; starting fresh',
                        bucket_name, object_key)
            return _empty_dataframe(), False
        logger.error('Failed to read s3://%s/%s: %r', bucket_name, object_key, exc)
        raise

    body = response['Body'].read()
    if isinstance(body, str):
        body = body.encode('utf-8')
    if not body.strip():
        logger.warning('s3://%s/%s exists but is empty; treating as no data',
                       bucket_name, object_key)
        return _empty_dataframe(), True

    # ``id`` must stay a string so 64-bit tweet ids are never coerced into
    # lossy floats.
    dataframe = pd.read_csv(io.BytesIO(body), dtype={'id': 'string'})

    # Legacy files predate the ``id`` column (and may carry a stray pandas
    # index column).  Make sure the schema is canonical before returning.
    for column in COLUMNS:
        if column not in dataframe.columns:
            dataframe[column] = pd.NA
    return dataframe[COLUMNS], True


def _tweet_to_record(tweet: Any, user_name: str, username: str) -> dict:
    metrics = getattr(tweet, 'public_metrics', None) or {}
    return {
        'id': str(tweet.id),
        'user': user_name,
        'username': username,
        'text': tweet.text,
        'like_count': metrics.get('like_count'),
        'reply_count': metrics.get('reply_count'),
        'retweet_count': metrics.get('retweet_count'),
        'created_at': tweet.created_at,
    }


def fetch_tweets(twitter_client: Any,
                 user_id: Any,
                 user_name: str,
                 username: str,
                 max_tweets: int = DEFAULT_MAX_TWEETS,
                 page_size: int = PAGE_SIZE) -> Tuple[list, list, int]:
    """Page through ``get_users_tweets`` until exhausted or *max_tweets*.

    Returns ``(records, errors, pages_fetched)``.  A failing page is recorded
    in ``errors`` and stops pagination, but every record already collected is
    returned so the caller can still persist partial results.
    """

    records: list = []
    errors: list = []
    pages_fetched = 0
    pagination_token: Optional[str] = None

    while len(records) < max_tweets:
        request_kwargs = {
            'id': user_id,
            'max_results': min(page_size, max_tweets - len(records)),
            'tweet_fields': TWEET_FIELDS,
        }
        if pagination_token is not None:
            request_kwargs['pagination_token'] = pagination_token

        try:
            response = twitter_client.get_users_tweets(**request_kwargs)
        except Exception as exc:  # noqa: BLE001 - captured as an observable error
            message = (
                f'Twitter page {pages_fetched + 1} failed '
                f'(pagination_token={pagination_token!r}): {exc!r}'
            )
            logger.error(message)
            errors.append(message)
            break

        pages_fetched += 1
        page_data = getattr(response, 'data', None) or []
        for tweet in page_data:
            records.append(_tweet_to_record(tweet, user_name, username))
            if len(records) >= max_tweets:
                break

        meta = getattr(response, 'meta', None) or {}
        next_token = meta.get('next_token')
        if not next_token or len(records) >= max_tweets:
            break
        pagination_token = next_token

    return records, errors, pages_fetched


def merge_tweets(existing_df: pd.DataFrame,
                 new_records: list) -> Tuple[pd.DataFrame, dict]:
    """Combine existing rows with fetched records, de-duplicating by tweet id.

    Rows whose id is already in S3, as well as ids repeated within the newly
    fetched batch, are counted as duplicates and dropped.  The result is
    sorted by ``created_at`` (ascending, ``id`` as a stable tie-breaker).
    """

    combined = existing_df.copy()
    combined['id'] = combined['id'].astype('string')
    existing_ids = set(combined['id'].dropna().astype(str))

    unique_records: list = []
    seen_ids: set = set()
    duplicate_count = 0
    for record in new_records:
        tweet_id = str(record['id'])
        if tweet_id in existing_ids or tweet_id in seen_ids:
            duplicate_count += 1
            continue
        seen_ids.add(tweet_id)
        unique_records.append(record)

    if unique_records:
        new_df = pd.DataFrame(unique_records, columns=COLUMNS)
        combined = pd.concat([combined, new_df], ignore_index=True)

    # Normalise mixed tz-aware datetimes / ISO strings before sorting.
    combined['created_at'] = pd.to_datetime(
        combined['created_at'], utc=True, errors='coerce', format='ISO8601'
    )
    combined = combined.sort_values(
        ['created_at', 'id'], ascending=True, na_position='last'
    ).reset_index(drop=True)

    stats = {
        'new': len(unique_records),
        'duplicates': duplicate_count,
    }
    return combined[COLUMNS], stats


def write_tweets(s3_client: Any,
                 dataframe: pd.DataFrame,
                 bucket_name: str = BUCKET_NAME,
                 object_key: str = OBJECT_KEY) -> None:
    """Write *dataframe* to S3 as a header-only-on-empty, index-free CSV."""

    buffer = io.StringIO()
    dataframe.to_csv(buffer, index=False, columns=COLUMNS)
    s3_client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=buffer.getvalue().encode('utf-8'),
    )


def _build_twitter_client() -> Any:
    import tweepy
    import config

    return tweepy.Client(
        bearer_token=config.BearerToken,
        consumer_key=config.ConsumerKey,
        consumer_secret=config.ConsumerSecret,
        access_token=config.AccessKey,
        access_token_secret=config.AccessSecret,
    )


def _build_s3_client() -> Any:
    import boto3
    import config

    return boto3.client(
        's3',
        aws_access_key_id=config.KEY,
        aws_secret_access_key=config.SECRET,
    )


def run_twitter_etl(twitter_client: Any = None,
                    s3_client: Any = None,
                    username: str = TWITTER_HANDLE,
                    bucket_name: str = BUCKET_NAME,
                    object_key: str = OBJECT_KEY,
                    max_tweets: int = DEFAULT_MAX_TWEETS) -> EtlResult:
    """Run one incremental collection.

    Parameters
    ----------
    twitter_client:
        Object exposing ``get_user`` and ``get_users_tweets``.  Defaults to a
        real tweepy client built from ``config.py``.
    s3_client:
        Object exposing the boto3-style ``get_object`` / ``put_object``
        methods.  Defaults to a real boto3 S3 client.
    max_tweets:
        Upper bound on how many tweets this run may fetch.
    """

    twitter_client = twitter_client or _build_twitter_client()
    s3_client = s3_client or _build_s3_client()

    # Abort on any S3 read failure other than "object does not exist"; an
    # empty dataframe must never be used to overwrite a readable object.
    existing_df, _existed = read_existing_tweets(s3_client, bucket_name, object_key)
    existing_count = len(existing_df)

    user = twitter_client.get_user(username=username).data
    user_id = user.id
    user_name = user.name

    records, errors, pages_fetched = fetch_tweets(
        twitter_client,
        user_id,
        user_name,
        username,
        max_tweets=max_tweets,
    )

    merged_df, stats = merge_tweets(existing_df, records)
    new_count = stats['new']
    duplicate_count = stats['duplicates']

    if new_count == 0:
        # Nothing new to persist: leave the S3 object untouched regardless of
        # whether the API returned an empty page, only duplicates, or failed.
        if errors:
            status = STATUS_PARTIAL_FAILURE
            message = (
                f'No tweets could be collected ({len(errors)} page error(s)); '
                f's3://{bucket_name}/{object_key} left unchanged with '
                f'{existing_count} existing tweets'
            )
        else:
            status = STATUS_SKIPPED_EMPTY
            message = (
                f'No new tweets for @{username} (fetched {len(records)}, '
                f'{duplicate_count} duplicate(s)); skipped upload and kept '
                f's3://{bucket_name}/{object_key} unchanged'
            )
        result = EtlResult(
            status=status,
            uploaded=False,
            message=message,
            bucket=bucket_name,
            key=object_key,
            existing_tweets=existing_count,
            fetched_tweets=len(records),
            new_tweets=0,
            duplicate_tweets=duplicate_count,
            total_tweets=existing_count,
            pages_fetched=pages_fetched,
            errors=errors,
        )
        logger.warning('%s: %s', status, message)
        return result

    write_tweets(s3_client, merged_df, bucket_name, object_key)

    status = STATUS_PARTIAL_FAILURE if errors else STATUS_UPLOADED
    message = (
        f'Wrote {len(merged_df)} tweets ({new_count} new, '
        f'{duplicate_count} duplicate(s) skipped, {pages_fetched} page(s)) '
        f'to s3://{bucket_name}/{object_key}'
    )
    if errors:
        message += f'; {len(errors)} page error(s): {errors}'
    result = EtlResult(
        status=status,
        uploaded=True,
        message=message,
        bucket=bucket_name,
        key=object_key,
        existing_tweets=existing_count,
        fetched_tweets=len(records),
        new_tweets=new_count,
        duplicate_tweets=duplicate_count,
        total_tweets=len(merged_df),
        pages_fetched=pages_fetched,
        errors=errors,
    )
    logger.info('%s: %s', status, message)
    return result
