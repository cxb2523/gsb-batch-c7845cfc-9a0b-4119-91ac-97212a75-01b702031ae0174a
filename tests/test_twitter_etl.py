"""Offline tests for the incremental Twitter ETL.

No network access, tweepy or boto3 is required: the Twitter client and the S3
client are replaced by in-memory fakes that mimic the small surface of the
real clients used by ``twitter_etl``.
"""

import io
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from twitter_etl import (
    COLUMNS,
    DEFAULT_MAX_TWEETS,
    EtlResult,
    OBJECT_KEY,
    STATUS_PARTIAL_FAILURE,
    STATUS_SKIPPED_EMPTY,
    STATUS_UPLOADED,
    fetch_tweets,
    merge_tweets,
    read_existing_tweets,
    run_twitter_etl,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeClientError(Exception):
    """Mimics botocore.exceptions.ClientError closely enough for the ETL."""

    def __init__(self, code, message):
        self.response = {'Error': {'Code': code, 'Message': message}}
        super().__init__(f'An error occurred ({code}): {message}')


class FakeUser:
    def __init__(self, user_id, name, username):
        self.id = user_id
        self.name = name
        self.username = username


class FakeUserResponse:
    def __init__(self, user):
        self.data = user


class FakeTweet:
    def __init__(self, tweet_id, text, created_at, metrics=None):
        self.id = tweet_id
        self.text = text
        self.created_at = created_at
        self.public_metrics = metrics or {
            'like_count': 1,
            'reply_count': 2,
            'retweet_count': 3,
        }


class FakeTweetsResponse:
    def __init__(self, tweets, next_token=None):
        self.data = tweets
        self.meta = {'next_token': next_token} if next_token else {}


class FakeTwitterClient:
    """Serves canned pages; pages can be programmed to raise on page number."""

    def __init__(self, user, pages, fail_on_page=None, page_error=None):
        self._user = user
        # ``pages`` is a list of tweet lists, one per API page.
        self._pages = pages
        self._fail_on_page = fail_on_page
        self._page_error = page_error or RuntimeError('simulated twitter outage')
        self.calls = []

    def get_user(self, username):
        return FakeUserResponse(self._user)

    def get_users_tweets(self, **kwargs):
        page_number = len(self.calls) + 1
        self.calls.append(kwargs)
        if self._fail_on_page == page_number:
            raise self._page_error
        tweets = self._pages[page_number - 1]
        next_token = f'next-{page_number}' if page_number < len(self._pages) else None
        return FakeTweetsResponse(tweets, next_token=next_token)


class FakeS3Client:
    """In-memory S3 storing object bodies as raw bytes."""

    def __init__(self, objects=None, get_error=None):
        self.objects = dict(objects or {})
        self.get_error = get_error
        self.put_calls = []

    def get_object(self, Bucket, Key):
        if self.get_error is not None:
            raise self.get_error
        if (Bucket, Key) not in self.objects:
            raise FakeClientError('NoSuchKey', 'The specified key does not exist.')

        class _Body:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return self._payload

        return {'Body': _Body(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body):
        self.put_calls.append({'Bucket': Bucket, 'Key': Key, 'Body': Body})
        self.objects[(Bucket, Key)] = Body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2023, 1, 21, 9, 0, 0, tzinfo=timezone.utc)


def make_tweet(tweet_id, offset_minutes=0, text=None):
    return FakeTweet(
        tweet_id=tweet_id,
        text=text or f'tweet {tweet_id}',
        created_at=BASE_TIME + timedelta(minutes=offset_minutes),
    )


def make_record(tweet_id, offset_minutes=0):
    tweet = make_tweet(tweet_id, offset_minutes=offset_minutes)
    return {
        'id': str(tweet.id),
        'user': 'Elon Musk',
        'username': 'elonmusk',
        'text': tweet.text,
        'like_count': tweet.public_metrics['like_count'],
        'reply_count': tweet.public_metrics['reply_count'],
        'retweet_count': tweet.public_metrics['retweet_count'],
        'created_at': tweet.created_at,
    }


def csv_with_ids(rows):
    """Build a canonical CSV (with the id column) from record dicts."""

    dataframe = pd.DataFrame(rows, columns=COLUMNS)
    return dataframe.to_csv(index=False).encode('utf-8')


def legacy_csv_without_ids(rows):
    """Build a legacy-style CSV (pandas index column, no id column)."""

    columns = [c for c in COLUMNS if c != 'id']
    dataframe = pd.DataFrame(rows, columns=columns)
    return dataframe.to_csv().encode('utf-8')


def stored_dataframe(s3_client, bucket, key=OBJECT_KEY):
    body = s3_client.objects[(bucket, key)]
    return pd.read_csv(io.BytesIO(body), dtype={'id': 'string'})


USER = FakeUser(44196397, 'Elon Musk', 'elonmusk')
BUCKET = 'test-bucket'


# ---------------------------------------------------------------------------
# read_existing_tweets
# ---------------------------------------------------------------------------

def test_read_existing_tweets_missing_object_returns_empty():
    s3 = FakeS3Client()

    dataframe, existed = read_existing_tweets(s3, BUCKET, OBJECT_KEY)

    assert existed is False
    assert list(dataframe.columns) == COLUMNS
    assert dataframe.empty


def test_read_existing_tweets_other_s3_error_aborts():
    s3 = FakeS3Client(get_error=FakeClientError('AccessDenied', 'denied'))

    with pytest.raises(FakeClientError):
        read_existing_tweets(s3, BUCKET, OBJECT_KEY)

    # No object must be created as a side effect of a failed read.
    assert s3.put_calls == []


def test_read_existing_tweets_migrates_legacy_csv_without_id_column():
    rows = [make_record(100), make_record(101)]
    s3 = FakeS3Client(objects={
        (BUCKET, OBJECT_KEY): legacy_csv_without_ids(rows),
    })

    dataframe, existed = read_existing_tweets(s3, BUCKET, OBJECT_KEY)

    assert existed is True
    assert list(dataframe.columns) == COLUMNS
    assert len(dataframe) == 2
    assert dataframe['id'].isna().all()


# ---------------------------------------------------------------------------
# fetch_tweets
# ---------------------------------------------------------------------------

def test_fetch_tweets_pages_until_exhausted():
    pages = [[make_tweet(i) for i in range(3)],
             [make_tweet(i) for i in range(3, 5)]]
    twitter = FakeTwitterClient(USER, pages)

    records, errors, pages_fetched = fetch_tweets(
        twitter, USER.id, USER.name, USER.username
    )

    assert [r['id'] for r in records] == ['0', '1', '2', '3', '4']
    assert errors == []
    assert pages_fetched == 2
    # The second call must carry the pagination token returned by page one.
    assert twitter.calls[0].get('pagination_token') is None
    assert twitter.calls[1]['pagination_token'] == 'next-1'


def test_fetch_tweets_respects_max_tweets_cap():
    pages = [[make_tweet(i) for i in range(100)],
             [make_tweet(i) for i in range(100, 150)]]
    twitter = FakeTwitterClient(USER, pages)

    records, errors, pages_fetched = fetch_tweets(
        twitter, USER.id, USER.name, USER.username, max_tweets=120
    )

    assert len(records) == 120
    assert records[-1]['id'] == '119'
    assert pages_fetched == 2
    assert twitter.calls[1]['max_results'] == 20


def test_fetch_tweets_keeps_partial_data_when_page_fails():
    pages = [[make_tweet(i) for i in range(100)],
             [make_tweet(100)]]
    twitter = FakeTwitterClient(
        USER, pages, fail_on_page=2, page_error=RuntimeError('boom')
    )

    records, errors, pages_fetched = fetch_tweets(
        twitter, USER.id, USER.name, USER.username
    )

    assert len(records) == 100
    assert pages_fetched == 1
    assert len(errors) == 1
    assert 'page 2 failed' in errors[0]
    assert 'boom' in errors[0]


def test_fetch_tweets_first_page_failure_returns_empty_with_error():
    twitter = FakeTwitterClient(
        USER, [[make_tweet(1)]], fail_on_page=1, page_error=RuntimeError('down')
    )

    records, errors, pages_fetched = fetch_tweets(
        twitter, USER.id, USER.name, USER.username
    )

    assert records == []
    assert pages_fetched == 0
    assert len(errors) == 1
    assert 'page 1 failed' in errors[0]


# ---------------------------------------------------------------------------
# merge_tweets
# ---------------------------------------------------------------------------

def test_merge_deduplicates_against_s3_and_within_batch_and_sorts():
    existing = pd.DataFrame(
        [make_record(1, offset_minutes=10), make_record(2, offset_minutes=5)],
        columns=COLUMNS,
    )
    # id 2 already exists, ids 3 and 4 are new, id 3 is repeated in the batch.
    new_records = [
        make_record(3, offset_minutes=1),
        make_record(2, offset_minutes=99),
        make_record(4, offset_minutes=0),
        make_record(3, offset_minutes=1),
    ]

    merged, stats = merge_tweets(existing, new_records)

    assert stats == {'new': 2, 'duplicates': 2}
    assert merged['id'].tolist() == ['4', '3', '2', '1']  # created_at ascending
    assert merged['created_at'].is_monotonic_increasing


# ---------------------------------------------------------------------------
# run_twitter_etl: end-to-end scenarios
# ---------------------------------------------------------------------------

def test_first_run_uploads_all_fetched_tweets():
    s3 = FakeS3Client()
    pages = [[make_tweet(i, offset_minutes=-i) for i in range(5)]]
    twitter = FakeTwitterClient(USER, pages)

    result = run_twitter_etl(
        twitter_client=twitter, s3_client=s3,
        bucket_name=BUCKET, object_key=OBJECT_KEY,
    )

    assert isinstance(result, EtlResult)
    assert result.status == STATUS_UPLOADED
    assert result.uploaded is True
    assert result.errors == []
    assert result.fetched_tweets == 5
    assert result.new_tweets == 5
    assert result.total_tweets == 5
    assert len(s3.put_calls) == 1

    stored = stored_dataframe(s3, BUCKET)
    assert list(stored.columns) == COLUMNS
    assert len(stored) == 5
    # Sorted by created_at ascending and written without a pandas index column.
    assert stored['id'].tolist() == ['4', '3', '2', '1', '0']
    assert stored['created_at'].is_monotonic_increasing
    assert stored['username'].eq('elonmusk').all()


def test_incremental_run_appends_only_new_tweets():
    existing_rows = [make_record(100, offset_minutes=30),
                     make_record(101, offset_minutes=20)]
    s3 = FakeS3Client(objects={
        (BUCKET, OBJECT_KEY): csv_with_ids(existing_rows),
    })
    pages = [[make_tweet(200, offset_minutes=10),
              make_tweet(201, offset_minutes=0)]]
    twitter = FakeTwitterClient(USER, pages)

    result = run_twitter_etl(
        twitter_client=twitter, s3_client=s3,
        bucket_name=BUCKET, object_key=OBJECT_KEY,
    )

    assert result.status == STATUS_UPLOADED
    assert result.existing_tweets == 2
    assert result.new_tweets == 2
    assert result.duplicate_tweets == 0
    assert result.total_tweets == 4

    stored = stored_dataframe(s3, BUCKET)
    assert stored['id'].tolist() == ['201', '200', '101', '100']
    assert stored['created_at'].is_monotonic_increasing


def test_duplicate_tweets_are_not_written_twice():
    existing_rows = [make_record(100, offset_minutes=30)]
    s3 = FakeS3Client(objects={
        (BUCKET, OBJECT_KEY): csv_with_ids(existing_rows),
    })
    # id 100 is already stored, ids 100/101 are repeated within the batch too.
    pages = [[make_tweet(100, offset_minutes=29),
              make_tweet(101, offset_minutes=10),
              make_tweet(101, offset_minutes=10),
              make_tweet(102, offset_minutes=5)]]
    twitter = FakeTwitterClient(USER, pages)

    result = run_twitter_etl(
        twitter_client=twitter, s3_client=s3,
        bucket_name=BUCKET, object_key=OBJECT_KEY,
    )

    assert result.new_tweets == 2
    assert result.duplicate_tweets == 2
    stored = stored_dataframe(s3, BUCKET)
    assert stored['id'].value_counts().to_dict() == {'102': 1, '101': 1, '100': 1}
    assert stored['created_at'].is_monotonic_increasing


def test_empty_api_response_skips_upload_and_keeps_existing_object():
    original_body = csv_with_ids([make_record(100), make_record(101)])
    s3 = FakeS3Client(objects={(BUCKET, OBJECT_KEY): original_body})
    twitter = FakeTwitterClient(USER, [[]])  # one page with no tweets

    result = run_twitter_etl(
        twitter_client=twitter, s3_client=s3,
        bucket_name=BUCKET, object_key=OBJECT_KEY,
    )

    assert result.status == STATUS_SKIPPED_EMPTY
    assert result.uploaded is False
    assert result.new_tweets == 0
    assert result.total_tweets == 2
    assert s3.put_calls == []
    # The stored object is byte-for-byte untouched.
    assert s3.objects[(BUCKET, OBJECT_KEY)] == original_body


def test_all_duplicates_skip_upload():
    existing_rows = [make_record(100)]
    s3 = FakeS3Client(objects={
        (BUCKET, OBJECT_KEY): csv_with_ids(existing_rows),
    })
    twitter = FakeTwitterClient(USER, [[make_tweet(100)]])

    result = run_twitter_etl(
        twitter_client=twitter, s3_client=s3,
        bucket_name=BUCKET, object_key=OBJECT_KEY,
    )

    assert result.status == STATUS_SKIPPED_EMPTY
    assert result.duplicate_tweets == 1
    assert s3.put_calls == []


def test_empty_response_on_first_run_skips_upload_without_creating_object():
    s3 = FakeS3Client()
    twitter = FakeTwitterClient(USER, [[]])

    result = run_twitter_etl(
        twitter_client=twitter, s3_client=s3,
        bucket_name=BUCKET, object_key=OBJECT_KEY,
    )

    assert result.status == STATUS_SKIPPED_EMPTY
    assert result.uploaded is False
    assert s3.put_calls == []
    assert (BUCKET, OBJECT_KEY) not in s3.objects


def test_page_failure_mid_pagination_still_merges_and_writes_partial_data():
    existing_rows = [make_record(1000, offset_minutes=60)]
    s3 = FakeS3Client(objects={
        (BUCKET, OBJECT_KEY): csv_with_ids(existing_rows),
    })
    pages = [[make_tweet(i, offset_minutes=i) for i in range(100)],
             [make_tweet(999)]]
    twitter = FakeTwitterClient(
        USER, pages, fail_on_page=2, page_error=RuntimeError('page exploded')
    )

    result = run_twitter_etl(
        twitter_client=twitter, s3_client=s3,
        bucket_name=BUCKET, object_key=OBJECT_KEY,
    )

    # Partial data is preserved and uploaded; the failure is observable.
    assert result.status == STATUS_PARTIAL_FAILURE
    assert result.uploaded is True
    assert result.fetched_tweets == 100
    assert result.new_tweets == 100
    assert result.total_tweets == 101
    assert result.pages_fetched == 1
    assert len(result.errors) == 1
    assert 'page exploded' in result.errors[0]

    stored = stored_dataframe(s3, BUCKET)
    assert len(stored) == 101
    assert set(stored['id']) == {'1000'} | {str(i) for i in range(100)}
    assert stored['created_at'].is_monotonic_increasing


def test_page_failure_without_new_tweets_keeps_object_unchanged():
    original_body = csv_with_ids([make_record(10)])
    s3 = FakeS3Client(objects={(BUCKET, OBJECT_KEY): original_body})
    twitter = FakeTwitterClient(
        USER, [[make_tweet(999)]], fail_on_page=1,
        page_error=RuntimeError('first page down'),
    )

    result = run_twitter_etl(
        twitter_client=twitter, s3_client=s3,
        bucket_name=BUCKET, object_key=OBJECT_KEY,
    )

    assert result.status == STATUS_PARTIAL_FAILURE
    assert result.uploaded is False
    assert result.new_tweets == 0
    assert len(result.errors) == 1
    assert s3.put_calls == []
    assert s3.objects[(BUCKET, OBJECT_KEY)] == original_body


def test_s3_read_failure_aborts_before_calling_twitter_or_writing():
    s3 = FakeS3Client(get_error=FakeClientError('InternalError', 's3 is on fire'))
    twitter = FakeTwitterClient(USER, [[make_tweet(1)]])

    with pytest.raises(FakeClientError):
        run_twitter_etl(
            twitter_client=twitter, s3_client=s3,
            bucket_name=BUCKET, object_key=OBJECT_KEY,
        )

    assert twitter.calls == []
    assert s3.put_calls == []


def test_default_max_tweets_matches_single_page_contract():
    # The API page size remains capped at 100 tweets per request.
    s3 = FakeS3Client()
    pages = [[make_tweet(i) for i in range(100)]]
    twitter = FakeTwitterClient(USER, pages)

    run_twitter_etl(
        twitter_client=twitter, s3_client=s3,
        bucket_name=BUCKET, object_key=OBJECT_KEY,
    )

    assert twitter.calls[0]['max_results'] == 100
    assert DEFAULT_MAX_TWEETS >= 100
