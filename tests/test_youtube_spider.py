#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local, offline tests for module/YoutubeSpider.py.

Run with:
    python -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from module import YoutubeSpider as youtube_module  # noqa: E402
from module.YoutubeSpider import YoutubeSpider  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is None:
            text = json.dumps(payload if payload is not None else {})
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload


def quota_error(key):
    return FakeResponse(403, {
        'error': {
            'code': 403,
            'message': f'quota exceeded for {key}',
            'errors': [{'reason': 'quotaExceeded', 'domain': 'youtube.quota'}],
        }
    })


def invalid_key_error():
    return FakeResponse(400, {
        'error': {
            'code': 400,
            'message': 'API key not valid. Please pass a valid API key.',
            'errors': [{'reason': 'keyInvalid', 'domain': 'usageLimits'}],
            'status': 'INVALID_ARGUMENT',
        }
    })


def playlist_payload(video_ids):
    items = []
    for video_id in video_ids:
        items.append({
            'snippet': {
                'title': f'title-{video_id}',
                'resourceId': {'videoId': video_id},
                'publishedAt': '2026-11-15T10:00:00Z',
            }
        })
    return {'items': items}


def channel_payload(uploads_id):
    return {'items': [{'contentDetails': {'relatedPlaylists': {'uploads': uploads_id}}}]}


class FakeRequests:
    """Replaces requests.get; routes by URL (channel vs playlist) and key."""

    def __init__(self, handlers):
        # handlers: dict api_key -> list, pop one response per call
        self.handlers = {key: list(responses) for key, responses in handlers.items()}
        self.calls = []

    def get(self, url, headers=None):
        api_key = None
        for part in url.split('&'):
            if part.startswith('key=') or '?key=' in part:
                api_key = part.split('key=', 1)[1]
        self.calls.append((api_key, url))
        responses = self.handlers.get(api_key)
        if not responses:
            raise AssertionError(f'unexpected request for key {api_key!r}: {url}')
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class YoutubeSpiderTest(unittest.TestCase):
    def setUp(self):
        self._orig_get = youtube_module.requests.get

    def tearDown(self):
        youtube_module.requests.get = self._orig_get

    def make_spider(self, keys, authors):
        spider = YoutubeSpider()
        spider.api_key_list = [{'api_key': key} for key in keys]
        spider.authors_list = authors
        return spider

    def test_full_key_list_is_loaded_and_tried_in_order(self):
        spider = self.make_spider(
            ['bad-key-1', 'good-key-2'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        fake = FakeRequests({
            'bad-key-1': [quota_error('bad-key-1')],
            'good-key-2': [FakeResponse(200, playlist_payload(['v1']))],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        self.assertEqual([call[0] for call in fake.calls], ['bad-key-1', 'good-key-2'])
        articles = json.loads(spider.articles_json)
        self.assertEqual([a['article_id'] for a in articles], ['v1'])
        self.assertEqual(spider.author_results[0]['status'], 'success')
        self.assertEqual(spider.author_results[0]['article_count'], 1)

    def test_failed_author_does_not_block_others_and_produces_no_fake_articles(self):
        spider = self.make_spider(
            ['key-1'],
            [
                {'author_id': 'UC_FAIL', 'author_name': 'Failing', 'uploads_id': 'UU_FAIL'},
                {'author_id': 'UC_OK', 'author_name': 'Working', 'uploads_id': 'UU_OK'},
            ],
        )
        # The failing author's playlist does not exist; that is author specific and
        # must not disable the key or abort the other author.
        fake = FakeRequests({
            'key-1': [
                FakeResponse(404, {
                    'error': {
                        'code': 404,
                        'message': 'The playlist identified with the request cannot be found.',
                        'errors': [{'reason': 'playlistNotFound'}],
                    }
                }),
                FakeResponse(200, playlist_payload(['good-1', 'good-2'])),
            ],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        articles = json.loads(spider.articles_json)
        self.assertEqual([a['article_id'] for a in articles], ['good-1', 'good-2'])
        self.assertTrue(all(a['author_id'] == 'UC_OK' for a in articles))

        results = {r['author_id']: r for r in spider.author_results}
        self.assertEqual(results['UC_FAIL']['status'], 'failed')
        self.assertIsNotNone(results['UC_FAIL']['error'])
        self.assertIsNone(results['UC_FAIL']['article_count'])
        self.assertEqual(results['UC_OK']['status'], 'success')
        self.assertEqual(results['UC_OK']['article_count'], 2)

    def test_all_keys_unusable_marks_author_failed_and_all_fail_returns_empty_list(self):
        spider = self.make_spider(
            ['key-1', 'key-2'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        fake = FakeRequests({
            'key-1': [quota_error('key-1')],
            'key-2': [invalid_key_error()],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        self.assertEqual(spider.articles_json, [])
        self.assertEqual(len(spider.author_results), 1)
        self.assertEqual(spider.author_results[0]['status'], 'failed')
        self.assertIn('quota', spider.author_results[0]['error'].lower())
        # Both keys were tried exactly once.
        self.assertEqual([call[0] for call in fake.calls], ['key-1', 'key-2'])

    def test_quota_exhausted_key_is_not_reused_for_later_authors(self):
        spider = self.make_spider(
            ['key-1', 'key-2'],
            [
                {'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'},
                {'author_id': 'UC_B', 'author_name': 'B', 'uploads_id': 'UU_B'},
            ],
        )
        fake = FakeRequests({
            'key-1': [quota_error('key-1')],
            'key-2': [
                FakeResponse(200, playlist_payload(['a1'])),
                FakeResponse(200, playlist_payload(['b1'])),
            ],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        self.assertEqual(
            [call[0] for call in fake.calls], ['key-1', 'key-2', 'key-2'])
        self.assertEqual([r['status'] for r in spider.author_results], ['success', 'success'])

    def test_transient_error_retries_and_then_succeeds(self):
        spider = self.make_spider(
            ['key-1'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        fake = FakeRequests({
            'key-1': [
                FakeResponse(503, {'error': {'code': 503, 'message': 'backend error'}}),
                FakeResponse(200, playlist_payload(['v1'])),
            ],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list(retry_times=3)

        self.assertEqual(json.loads(spider.articles_json)[0]['article_id'], 'v1')
        self.assertEqual(spider.author_results[0]['status'], 'success')

    def test_network_exception_is_isolated_per_author(self):
        spider = self.make_spider(
            ['key-1'],
            [
                {'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'},
                {'author_id': 'UC_B', 'author_name': 'B', 'uploads_id': 'UU_B'},
            ],
        )
        fake = FakeRequests({
            'key-1': [
                ConnectionError('boom'),
                FakeResponse(200, playlist_payload(['b1'])),
            ],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list(retry_times=1)

        results = {r['author_id']: r for r in spider.author_results}
        self.assertEqual(results['UC_A']['status'], 'failed')
        self.assertIn('boom', results['UC_A']['error'])
        self.assertEqual(results['UC_B']['status'], 'success')
        self.assertEqual([a['article_id'] for a in json.loads(spider.articles_json)], ['b1'])

    def test_uploads_id_resolution_rotates_keys(self):
        spider = self.make_spider(
            ['key-1', 'key-2'],
            [{'author_id': 'UC_A', 'author_name': 'A'}],
        )
        fake = FakeRequests({
            'key-1': [quota_error('key-1')],
            'key-2': [
                FakeResponse(200, channel_payload('UU_A')),
                FakeResponse(200, playlist_payload(['v1'])),
            ],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        self.assertEqual(spider.authors_list[0]['uploads_id'], 'UU_A')
        self.assertEqual(spider.author_results[0]['status'], 'success')
        self.assertIn('id=UC_A', fake.calls[1][1])
        self.assertIn('playlistId=UU_A', fake.calls[2][1])

    def test_article_fields_are_strings(self):
        spider = self.make_spider(
            ['key-1'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        fake = FakeRequests({'key-1': [FakeResponse(200, playlist_payload(['v1']))]})
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        article = json.loads(spider.articles_json)[0]
        for field in ('article_id', 'author_id', 'channel_name', 'link',
                      'creation_time', 'snapshot_time'):
            self.assertIsInstance(article[field], str, field)
        self.assertEqual(article['channel_name'], 'Youtube')

    def test_malformed_item_does_not_discard_valid_videos(self):
        spider = self.make_spider(
            ['key-1'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        broken_payload = {'items': [
            {'snippet': {}},
            {'snippet': {
                'title': 'ok',
                'resourceId': {'videoId': 'v1'},
                'publishedAt': '2026-11-15T10:00:00Z',
            }},
        ]}
        fake = FakeRequests({'key-1': [FakeResponse(200, broken_payload)]})
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        self.assertEqual([a['article_id'] for a in json.loads(spider.articles_json)], ['v1'])
        self.assertEqual(spider.author_results[0]['status'], 'success')
        self.assertEqual(spider.author_results[0]['article_count'], 1)

    def test_empty_playlist_is_success_with_zero_count(self):
        spider = self.make_spider(
            ['key-1'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        fake = FakeRequests({'key-1': [FakeResponse(200, {'items': []})]})
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        self.assertEqual(spider.articles_json, [])
        self.assertEqual(spider.author_results[0]['status'], 'success')
        self.assertEqual(spider.author_results[0]['article_count'], 0)

    def test_non_json_403_key_is_disabled_and_next_key_used(self):
        spider = self.make_spider(
            ['key-1', 'key-2'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        fake = FakeRequests({
            'key-1': [FakeResponse(403, None, text='<html>forbidden</html>')],
            'key-2': [FakeResponse(200, playlist_payload(['v1']))],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list(retry_times=1)

        self.assertEqual([a['article_id'] for a in json.loads(spider.articles_json)], ['v1'])
        self.assertEqual(spider.author_results[0]['status'], 'success')

    def test_author_specific_4xx_does_not_disable_key(self):
        spider = self.make_spider(
            ['key-1'],
            [
                {'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'},
                {'author_id': 'UC_B', 'author_name': 'B', 'uploads_id': 'UU_B'},
            ],
        )
        fake = FakeRequests({
            'key-1': [
                FakeResponse(400, {
                    'error': {
                        'code': 400,
                        'message': 'playlist id is malformed',
                        'errors': [{'reason': 'invalidPlaylistId'}],
                    }
                }),
                FakeResponse(200, playlist_payload(['b1'])),
            ],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list(retry_times=3)

        results = {r['author_id']: r for r in spider.author_results}
        self.assertEqual(results['UC_A']['status'], 'failed')
        self.assertEqual(results['UC_B']['status'], 'success')
        # The key was not retried for the same author and stayed enabled.
        self.assertEqual([call[0] for call in fake.calls], ['key-1', 'key-1'])

    def test_missing_key_or_author_is_reported_not_silently_dropped(self):
        spider = self.make_spider([], [
            {'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'},
        ])
        youtube_module.requests.get = lambda url, headers=None: (_ for _ in ()).throw(
            AssertionError('no HTTP request expected without a key'))
        spider.get_articles_list()

        self.assertEqual(spider.articles_json, [])
        self.assertEqual(spider.author_results[0]['status'], 'failed')
        self.assertIn('no usable API key', spider.author_results[0]['error'])

    def test_invalid_author_entry_is_isolated(self):
        spider = self.make_spider(
            ['key-1'],
            [
                {'author_name': 'NoId', 'uploads_id': 'UU_X'},
                {'author_id': 'UC_B', 'author_name': 'B', 'uploads_id': 'UU_B'},
            ],
        )
        fake = FakeRequests({'key-1': [FakeResponse(200, playlist_payload(['b1']))]})
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        self.assertEqual(spider.author_results[0]['status'], 'failed')
        self.assertEqual(spider.author_results[0]['error'], 'missing author_id')
        self.assertEqual(spider.author_results[1]['status'], 'success')
        self.assertEqual([a['author_id'] for a in json.loads(spider.articles_json)], ['UC_B'])

    def test_403_message_only_quota_error_disables_key(self):
        """Some responses carry no 'errors' list, only a message."""
        spider = self.make_spider(
            ['key-1', 'key-2'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        fake = FakeRequests({
            'key-1': [FakeResponse(403, {
                'error': {'code': 403, 'message': 'The request cannot be completed because you have exceeded your quota.'}
            })],
            'key-2': [FakeResponse(200, playlist_payload(['v1']))],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list(retry_times=1)

        self.assertEqual([a['article_id'] for a in json.loads(spider.articles_json)], ['v1'])
        self.assertIn('key-1', spider._disabled_api_keys)

    def test_plain_403_is_author_specific_and_keeps_key_enabled(self):
        spider = self.make_spider(
            ['key-1'],
            [
                {'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'},
                {'author_id': 'UC_B', 'author_name': 'B', 'uploads_id': 'UU_B'},
            ],
        )
        fake = FakeRequests({
            'key-1': [
                FakeResponse(403, {
                    'error': {'code': 403, 'message': 'The request is not authorized for this channel.'}
                }),
                FakeResponse(200, playlist_payload(['b1'])),
            ],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list(retry_times=1)

        results = {r['author_id']: r for r in spider.author_results}
        self.assertEqual(results['UC_A']['status'], 'failed')
        self.assertEqual(results['UC_B']['status'], 'success')
        self.assertEqual(spider._disabled_api_keys, set())

    def test_author_specific_400_does_not_burn_remaining_keys(self):
        spider = self.make_spider(
            ['key-1', 'key-2'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        fake = FakeRequests({
            'key-1': [FakeResponse(400, {
                'error': {'code': 400, 'errors': [{'reason': 'invalidPlaylistId'}]}
            })],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list(retry_times=3)

        self.assertEqual([call[0] for call in fake.calls], ['key-1'])
        self.assertEqual(spider._disabled_api_keys, set())
        self.assertEqual(spider.author_results[0]['status'], 'failed')

    def test_per_author_outcome_is_logged(self):
        spider = self.make_spider(
            ['key-1'],
            [
                {'author_id': 'UC_A', 'author_name': 'Good', 'uploads_id': 'UU_A'},
                {'author_id': 'UC_B', 'author_name': 'Bad', 'uploads_id': 'UU_B'},
            ],
        )

        def fake_get(url, headers=None):
            if 'playlistId=UU_B' in url:
                return FakeResponse(404, {
                    'error': {'code': 404, 'errors': [{'reason': 'playlistNotFound'}]}
                })
            return FakeResponse(200, playlist_payload(['v1']))

        youtube_module.requests.get = fake_get

        with self.assertLogs(level='INFO') as captured:
            spider.get_articles_list(retry_times=1)

        logged = '\n'.join(captured.output)
        self.assertIn("author 'Good' (UC_A) succeeded: 1 video(s)", logged)
        self.assertIn("author 'Bad' (UC_B) failed:", logged)

    def test_start_loads_all_keys_from_conf_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = os.path.join(tmpdir, 'youtube_apikey_list.json')
            author_path = os.path.join(tmpdir, 'youtube_authors_list.json')
            with open(key_path, 'w', encoding='utf-8') as f:
                json.dump([{'api_key': 'key-1'}, {'api_key': 'key-2'}], f)
            with open(author_path, 'w', encoding='utf-8') as f:
                json.dump([
                    {'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'},
                ], f)

            fake = FakeRequests({
                'key-1': [quota_error('key-1')],
                'key-2': [FakeResponse(200, playlist_payload(['v1']))],
            })
            youtube_module.requests.get = fake.get

            spider = YoutubeSpider()
            spider.start(authors_list_path=author_path, api_key_path=key_path)

        self.assertEqual([call[0] for call in fake.calls], ['key-1', 'key-2'])
        self.assertEqual([a['article_id'] for a in json.loads(spider.articles_json)], ['v1'])

    def test_empty_key_entries_are_skipped(self):
        spider = YoutubeSpider()
        spider.api_key_list = [{'api_key': ''}, {'api_key': 'key-1'}]
        spider.authors_list = [
            {'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'},
        ]
        fake = FakeRequests({'key-1': [FakeResponse(200, playlist_payload(['v1']))]})
        youtube_module.requests.get = fake.get

        spider.get_articles_list()

        self.assertEqual([call[0] for call in fake.calls], ['key-1'])
        self.assertEqual(spider.author_results[0]['status'], 'success')

    def test_duplicate_keys_are_tried_once(self):
        spider = self.make_spider(
            ['key-1', 'key-1'],
            [{'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'}],
        )
        fake = FakeRequests({
            'key-1': [quota_error('key-1')],
        })
        youtube_module.requests.get = fake.get

        spider.get_articles_list(retry_times=1)

        self.assertEqual([call[0] for call in fake.calls], ['key-1'])
        self.assertEqual(spider.author_results[0]['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
