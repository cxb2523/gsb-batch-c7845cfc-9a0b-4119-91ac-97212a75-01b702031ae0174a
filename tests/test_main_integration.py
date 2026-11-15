#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration checks: YoutubeSpider output is accepted by the unmodified main.py.

The DingTalk dependency is stubbed so the main program can be imported offline.
"""

import json
import os
import sys
import tempfile
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# Stub dingtalkchatbot before main.py imports module.DingTalkRobot.
if 'dingtalkchatbot' not in sys.modules:
    stub = types.ModuleType('dingtalkchatbot')
    stub_chatbot = types.ModuleType('dingtalkchatbot.chatbot')

    class DingtalkChatbot:  # pragma: no cover - never used here
        def __init__(self, *args, **kwargs):
            pass

    stub_chatbot.DingtalkChatbot = DingtalkChatbot
    stub.chatbot = stub_chatbot
    sys.modules['dingtalkchatbot'] = stub
    sys.modules['dingtalkchatbot.chatbot'] = stub_chatbot

from module import YoutubeSpider as youtube_module  # noqa: E402
from module.YoutubeSpider import YoutubeSpider  # noqa: E402

import main  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload if payload is not None else {})

    def json(self):
        return self._payload


def playlist_payload(video_ids):
    return {'items': [
        {
            'snippet': {
                'title': f'title-{video_id}',
                'resourceId': {'videoId': video_id},
                'publishedAt': '2026-11-15T10:00:00Z',
            }
        }
        for video_id in video_ids
    ]}


class MainIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._orig_get = youtube_module.requests.get

    def tearDown(self):
        youtube_module.requests.get = self._orig_get

    def test_partial_failure_output_keeps_main_flow_working(self):
        """A failing author must not produce fake entries nor break get_new_articles."""
        spider = YoutubeSpider()
        spider.api_key_list = [{'api_key': 'key-1'}, {'api_key': 'key-2'}]
        spider.authors_list = [
            {'author_id': 'UC_FAIL', 'author_name': 'Fail-1', 'uploads_id': 'UU_FAIL'},
            {'author_id': 'UC_OK', 'author_name': 'Team-A', 'uploads_id': 'UU_OK'},
        ]

        calls = []

        def fake_get(url, headers=None):
            calls.append(url)
            if 'playlistId=UU_FAIL' in url:
                # Author specific failure: the playlist does not exist. This must
                # not disable the API key for the remaining authors.
                return FakeResponse(404, {
                    'error': {
                        'code': 404,
                        'message': 'playlist not found',
                        'errors': [{'reason': 'playlistNotFound'}],
                    }
                })
            return FakeResponse(200, playlist_payload(['v1', 'v2']))

        youtube_module.requests.get = fake_get

        spider.get_articles_list()

        # Same shape main.py expects: a truthy JSON string.
        self.assertIsInstance(spider.articles_json, str)
        aggregated = json.dumps([json.loads(spider.articles_json)], ensure_ascii=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, 'previous_articles.json')
            # Seed the previous run so author_id-based dedupe is exercised.
            with open(previous_path, 'w', encoding='utf-8') as f:
                json.dump([[{
                    'title': 'old', 'article_id': 'v1', 'author_name': 'Team-A',
                    'author_id': 'UC_OK', 'channel_name': 'Youtube',
                    'link': 'https://www.youtube.com/watch?v=v1',
                    'creation_time': '1763200800', 'snapshot_time': '1763200800',
                }]], f)

            new_articles = main.get_new_articles(
                aggregated, previous_articles_file_path=previous_path)

        self.assertEqual([a['article_id'] for a in new_articles], ['v2'])
        self.assertTrue(all(a['author_id'] == 'UC_OK' for a in new_articles))
        # The failed author is visible in the per-author results, not in articles.
        failed = [r for r in spider.author_results if r['status'] == 'failed']
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]['author_id'], 'UC_FAIL')

    def test_all_authors_failed_returns_empty_list_that_main_ignores(self):
        spider = YoutubeSpider()
        spider.api_key_list = [{'api_key': 'key-1'}]
        spider.authors_list = [
            {'author_id': 'UC_A', 'author_name': 'A', 'uploads_id': 'UU_A'},
        ]
        youtube_module.requests.get = lambda url, headers=None: FakeResponse(403, {
            'error': {'code': 403, 'errors': [{'reason': 'quotaExceeded'}]}
        })

        spider.get_articles_list()

        self.assertEqual(spider.articles_json, [])
        # main.py only appends when articles_json is truthy.
        all_lists = []
        if spider.articles_json:
            all_lists.append(json.loads(spider.articles_json))
        self.assertEqual(all_lists, [])

    def test_real_conf_files_with_empty_key_do_not_crash(self):
        """The shipped conf has an empty key; start() must fail gracefully."""
        spider = YoutubeSpider()
        spider.start(
            authors_list_path=os.path.join(REPO_ROOT, 'conf', 'youtube_authors_list.json'),
            api_key_path=os.path.join(REPO_ROOT, 'conf', 'youtube_apikey_list.json'),
        )

        self.assertEqual(spider.articles_json, [])
        self.assertEqual(len(spider.author_results), len(spider.authors_list))
        self.assertEqual(spider.author_results[0]['status'], 'failed')
        self.assertIn('no usable API key', spider.author_results[0]['error'])


if __name__ == '__main__':
    unittest.main()
