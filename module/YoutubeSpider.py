#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Function: Youtube designated channel crawl videos, obtain from the official api - YouTube Data API v3
# Note: Need API Key :https://console.cloud.google.com/apis/api/youtube.googleapis.com
# Author: 10935336
# Creation date: 2023-05-12
# Modified date: 2026-11-16

import json
import logging
import os
from datetime import datetime

import requests

# YouTube Data API error reasons meaning that the key itself cannot be used any
# more for this run (quota exhausted, invalid/unauthorized key, blocked key...).
_KEY_LEVEL_FAILURE_REASONS = {
    'quotaxceeded',
    'dailylimitexceeded',
    'ratelimitexceeded',
    'userratelimitexceeded',
    'keyinvalid',
    'api_key_invalid',
    'iprefererblocked',
    'accessnotconfigured',
}
_KEY_LEVEL_FAILURE_STATUS_CODES = {401, 429}
# Message fragments used when an error carries no machine readable reason.
_KEY_LEVEL_FAILURE_MESSAGE_KEYWORDS = (
    'quota',
    'api key not valid',
    'api key expired',
    'invalid api key',
    'api key is invalid',
    'key is not valid',
    'keyinvalid',
)


class YoutubeSpider:
    def __init__(self):
        self.articles_json = ''
        self.api_key_list = []
        self.authors_list = []
        # Per-author outcome of the latest collection run. Every entry is a dict:
        # {
        #     'author_id': str,
        #     'author_name': str,
        #     'status': 'success' | 'failed',
        #     'article_count': int | None,  # videos collected; None when failed
        #     'error': None | str,    # failure reason, None on success
        # }
        self.author_results = []
        # Where authors_list was loaded from (used for optional uploads_id write-back).
        self.authors_list_path = None
        # Keys that returned a quota/auth error in this run; skipped afterwards.
        self._disabled_api_keys = set()
        # True when an uploads_id was resolved during this run and needs writing back.
        self._uploads_ids_resolved = False
        self.headers = {
            'user-agent': 'Mozilla/5.0 (Linux; Android 13.0; Nexus 15 Build/MRA99N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36',
        }

    def load_api_key(self, api_key_path):
        try:
            with open(api_key_path, 'r', encoding='utf-8') as r:
                self.api_key_list = json.load(r)
        except Exception as error:
            logging.exception(f'{api_key_path} read error: {error}')

    def load_authors(self, authors_list_path):
        try:
            with open(authors_list_path, 'r', encoding='utf-8') as r:
                self.authors_list = json.load(r)
            self.authors_list_path = authors_list_path
        except Exception as error:
            logging.exception(f'{authors_list_path} read error: {error}')

    @staticmethod
    def _mask_api_key(api_key):
        """Never log a full API key, but keep keys distinguishable in logs."""
        if not api_key:
            return '<empty>'
        if len(api_key) <= 4:
            return api_key[:1] + '***'
        if len(api_key) <= 8:
            return api_key[:3] + '***' + api_key[-2:]
        return api_key[:4] + '***' + api_key[-4:]

    def _get_api_keys(self):
        """Return all configured keys in configuration order, without duplicates."""
        api_keys = []
        seen = set()

        if isinstance(self.api_key_list, dict):
            entries = [self.api_key_list]
        elif isinstance(self.api_key_list, list):
            entries = self.api_key_list
        else:
            entries = []

        for entry in entries:
            if isinstance(entry, dict):
                api_key = entry.get('api_key')
            elif isinstance(entry, str):
                api_key = entry
            else:
                api_key = None
            if isinstance(api_key, str):
                api_key = api_key.strip()
            if api_key and api_key not in seen:
                seen.add(api_key)
                api_keys.append(api_key)
        return api_keys

    def _reset_api_key_state(self):
        self._disabled_api_keys = set()

    def _available_api_keys(self):
        return [key for key in self._get_api_keys() if key not in self._disabled_api_keys]

    @staticmethod
    def _get_response_payload(response):
        """Parse a response body, accepting both real requests responses and simple fakes."""
        json_method = getattr(response, 'json', None)
        if callable(json_method):
            try:
                return json_method()
            except Exception:
                pass
        text = getattr(response, 'text', None)
        if text is None:
            raise ValueError('response has no body')
        return json.loads(text)

    @staticmethod
    def _describe_http_error(status_code, payload):
        """Build a readable error description and collect YouTube error reasons."""
        descriptions = []
        reasons = []
        messages = []

        if isinstance(payload, dict):
            error = payload.get('error')
            if isinstance(error, dict):
                message = error.get('message')
                if message:
                    messages.append(str(message))
                    descriptions.append(str(message))
                if error.get('status'):
                    reasons.append(str(error['status']))
                error_items = error.get('errors')
                if isinstance(error_items, list):
                    for error_item in error_items:
                        if isinstance(error_item, dict) and error_item.get('reason'):
                            reasons.append(str(error_item['reason']))

        description = f'HTTP {status_code}'
        unique_reasons = list(dict.fromkeys(reasons))
        if unique_reasons:
            description += ' ' + ','.join(unique_reasons)
        if descriptions:
            description += ': ' + '; '.join(descriptions)
        return description, unique_reasons, messages

    @staticmethod
    def _is_key_level_failure(status_code, reasons, messages=None, include_403=False):
        """Whether the failure means this key should not be used again this run.

        Quota/auth reasons disable the key regardless of status. 401 and 429 are
        always key-level. A plain 403 without a recognised reason may be channel
        specific (e.g. forbidden content), so it is not treated as key-level
        unless include_403 is True (used when the body is unreadable).
        """
        if any(str(reason).lower() in _KEY_LEVEL_FAILURE_REASONS for reason in reasons):
            return True
        for message in messages or []:
            lowered = str(message).lower()
            if any(keyword in lowered for keyword in _KEY_LEVEL_FAILURE_MESSAGE_KEYWORDS):
                return True
        try:
            status_code = int(status_code)
        except (TypeError, ValueError):
            return False
        if status_code in _KEY_LEVEL_FAILURE_STATUS_CODES:
            return True
        return include_403 and status_code == 403

    def _request_api_once(self, url_builder):
        """
        Try every still-available key in order, once each.

        :return: (payload, error_description, retryable)
                 payload is None on failure. retryable is True when at least one
                 failure looked transient (network error, 5xx, bad body), i.e. a
                 later retry with the same still-enabled keys may succeed.
        """
        api_keys = self._available_api_keys()
        if not api_keys:
            return None, 'no usable API key available', False

        errors = []
        retryable = False

        for api_key in api_keys:
            masked_key = self._mask_api_key(api_key)
            url = url_builder(api_key)

            try:
                response = requests.get(url=url, headers=self.headers)
            except Exception as error:
                retryable = True
                errors.append(f'api key {masked_key}: request failed: {error}')
                logging.warning(f'YouTube API key {masked_key} request failed ({error}), trying next key')
                continue

            status_code = getattr(response, 'status_code', None)

            try:
                payload = self._get_response_payload(response)
            except Exception as error:
                if self._is_key_level_failure(
                        status_code, [], [], include_403=True):
                    # Non-JSON 401/403/429 still means this key is unusable: with a
                    # readable body a plain 403 might be playlist specific, but
                    # without one it cannot be attributed to the author.
                    self._disabled_api_keys.add(api_key)
                    errors.append(f'api key {masked_key}: HTTP {status_code} unusable key')
                    logging.warning(
                        f'YouTube API key {masked_key} returned HTTP {status_code} with an '
                        f'unparsable body, switching to the next key')
                else:
                    retryable = True
                    errors.append(f'api key {masked_key}: HTTP {status_code} invalid JSON body: {error}')
                    logging.warning(
                        f'YouTube API key {masked_key} returned an unparsable body ({error}), trying next key')
                continue

            effective_status = status_code
            error_object = payload.get('error') if isinstance(payload, dict) else None
            if isinstance(error_object, dict):
                # Some proxies/API gateways report errors with HTTP 200.
                try:
                    effective_status = int(error_object.get('code', status_code))
                except (TypeError, ValueError):
                    effective_status = status_code

            # A normal, successful response (an empty playlist is still a success).
            if status_code == 200 and not isinstance(error_object, dict):
                if isinstance(payload, dict):
                    return payload, None, False
                retryable = True
                errors.append(f'api key {masked_key}: HTTP 200 unexpected response body')
                logging.warning(
                    f'YouTube API key {masked_key} returned an unexpected response body, trying next key')
                continue

            description, reasons, messages = self._describe_http_error(effective_status, payload)
            errors.append(f'api key {masked_key}: {description}')

            if self._is_key_level_failure(effective_status, reasons, messages):
                self._disabled_api_keys.add(api_key)
                logging.warning(
                    f'YouTube API key {masked_key} hit a quota/auth failure ({description}), '
                    f'switching to the next key')
                continue

            try:
                status_code_int = int(effective_status)
            except (TypeError, ValueError):
                status_code_int = None

            if status_code_int is not None and 400 <= status_code_int < 500 and status_code_int != 403:
                # Author/playlist specific (playlistNotFound, invalid id...):
                # another key would return the same answer, stop burning requests.
                logging.warning(f'YouTube API request failed ({description})')
                break

            # 403 may be key specific (IP restriction, per-key limits) and 5xx /
            # unknown statuses may be transient, so try the remaining keys.
            if status_code_int is None or status_code_int >= 500:
                retryable = True
            logging.warning(
                f'YouTube API key {masked_key} request failed ({description}), trying next key')

        return None, '; '.join(errors), retryable

    def _request_api(self, url_builder, retry_times=1):
        """
        Request an API URL with automatic key rotation.

        Quota/auth failures disable the key for the rest of the run. Transient
        failures (network errors, 5xx) may be retried, up to retry_times rounds.

        :return: (payload, error_description)
        """
        try:
            attempts = max(1, int(retry_times))
        except (TypeError, ValueError):
            attempts = 1

        last_error = 'no usable API key available'
        for _ in range(attempts):
            payload, error, retryable = self._request_api_once(url_builder)
            if payload is not None:
                return payload, None
            last_error = error
            if not retryable or not self._available_api_keys():
                break

        return None, last_error

    def _resolve_uploads_id(self, author, retry_times=3):
        """
        Ensure author has an uploads_id, fetching it with key rotation if needed.

        :return: (uploads_id, error_description)
        """
        uploads_id = author.get('uploads_id')
        if uploads_id:
            return uploads_id, None

        author_id = author.get('author_id')
        if author_id is None or str(author_id).strip() == '':
            return None, 'missing author_id'

        author_id = str(author_id)

        def build_url(api_key):
            return "https://www.googleapis.com/youtube/v3/channels?" + \
                   "id=" + str(author_id) + \
                   "&key=" + str(api_key) + \
                   "&part=contentDetails"

        payload, error = self._request_api(build_url, retry_times=retry_times)
        if payload is None:
            return None, f'cannot get uploads_id: {error}'

        try:
            items = payload['items']
            if not items:
                return None, f'no channel found for author_id {author_id}'
            uploads_id = items[0]['contentDetails']['relatedPlaylists']['uploads']
            if not uploads_id:
                return None, f'empty uploads_id for author_id {author_id}'
        except Exception as parse_error:
            return None, f'cannot parse uploads_id response: {parse_error}'

        author['uploads_id'] = uploads_id
        self._uploads_ids_resolved = True
        return uploads_id, None

    def _parse_article(self, item, author_id, author_name, current_time):
        """Convert one playlistItem into the article dict format used by the main program."""
        video_id = item["snippet"]["resourceId"]["videoId"]
        title = item["snippet"]["title"]
        link = "https://www.youtube.com/watch?v=" + str(video_id)

        datetime_obj = datetime.strptime(item["snippet"]["publishedAt"], "%Y-%m-%dT%H:%M:%SZ")
        timestamp = int(datetime_obj.timestamp())

        return {
            "title": str(title),
            "article_id": str(video_id),
            "author_name": str(author_name),
            "author_id": str(author_id),
            "channel_name": "Youtube",
            "link": link,
            "creation_time": str(timestamp),
            "snapshot_time": str(current_time)
        }

    def _collect_articles_for_author(self, author, current_time, retry_times=3, max_results=20, part='snippet'):
        """
        Collect all videos of one author. This method never raises for an author
        specific failure; it returns an empty article list plus a failed result.

        :return: (articles, result_dict)
        """
        result = {
            'author_id': None,
            'author_name': None,
            'status': 'failed',
            # None (not 0) so a failed author can never be mistaken for an author
            # that was successfully collected with zero videos.
            'article_count': None,
            'error': None,
        }

        if not isinstance(author, dict):
            result['error'] = f'invalid author entry: {author!r}'
            return [], result

        author_id = author.get('author_id')
        author_name = author.get('author_name')
        result['author_id'] = None if author_id is None else str(author_id)
        result['author_name'] = None if author_name is None else str(author_name)

        if author_id is None or str(author_id).strip() == '':
            result['error'] = 'missing author_id'
            return [], result
        if author_name is None or str(author_name).strip() == '':
            result['error'] = 'missing author_name'
            return [], result

        uploads_id, uploads_error = self._resolve_uploads_id(author, retry_times=retry_times)
        if uploads_id is None:
            result['error'] = uploads_error
            return [], result

        def build_url(api_key):
            return "https://www.googleapis.com/youtube/v3/playlistItems?" + \
                   "playlistId=" + str(uploads_id) + \
                   "&key=" + str(api_key) + \
                   "&part=" + str(part) + \
                   "&maxResults=" + str(max_results)

        payload, error = self._request_api(build_url, retry_times=retry_times)
        if payload is None:
            result['error'] = error
            return [], result

        items = payload.get('items') if isinstance(payload, dict) else None
        if items is None:
            result['error'] = "unexpected playlistItems response: missing 'items'"
            return [], result
        if not isinstance(items, list):
            result['error'] = "unexpected playlistItems response: 'items' is not a list"
            return [], result

        articles = []
        for item in items:
            try:
                articles.append(self._parse_article(item, author_id, author_name, current_time))
            except Exception as parse_error:
                # One malformed item must not discard the author's other videos.
                logging.warning(
                    f"YouTube author '{author_name}' ({author_id}) skipping malformed item: {parse_error}")

        result['status'] = 'success'
        result['article_count'] = len(articles)
        return articles, result

    def _write_back_uploads_ids(self):
        """Persist newly resolved uploads_id values if the authors file is known."""
        if not self.authors_list_path or not self._uploads_ids_resolved:
            return
        try:
            with open(self.authors_list_path, 'w', encoding='utf-8') as w:
                json.dump(self.authors_list, w, ensure_ascii=False)
            self._uploads_ids_resolved = False
        except Exception as error:
            logging.exception(f'{self.authors_list_path} write error: {error}')

    def fill_uploads_id_for_authors_list(self, authors_list_path=None, write_back=True):
        """Resolve missing uploads_id values for every author, rotating keys on failure."""
        if authors_list_path is None:
            module_dir = os.path.dirname(os.path.abspath(__file__))
            authors_list_path = os.path.join(module_dir, '..', 'conf', 'youtube_authors_list.json')

        self._reset_api_key_state()

        authors = self.authors_list if isinstance(self.authors_list, list) else []
        for author in authors:
            if not isinstance(author, dict):
                logging.error(f'Invalid author entry: {author!r}')
                continue
            if author.get('uploads_id'):
                continue
            uploads_id, error = self._resolve_uploads_id(author, retry_times=3)
            if uploads_id:
                logging.info(
                    f"YouTube author '{author.get('author_name')}' ({author.get('author_id')}) "
                    f"resolved uploads_id {uploads_id}")
            else:
                logging.error(
                    f"YouTube author '{author.get('author_name')}' ({author.get('author_id')}) "
                    f"failed to resolve uploads_id: {error}")

        if write_back:
            try:
                with open(authors_list_path, 'w', encoding='utf-8') as w:
                    json.dump(self.authors_list, w, ensure_ascii=False)
                self._uploads_ids_resolved = False
            except Exception as error:
                logging.exception(f'{authors_list_path} write error: {error}')

    def get_articles_list(self, retry_times=3, max_results=20, part='snippet'):
        """
        Collect videos for all authors.

        Every author is processed independently: a failed author never aborts the
        run and never produces placeholder articles. Outcomes are stored in
        self.author_results (one entry per author) and written to the log.

        self.articles_json is a JSON string with all videos of successful authors;
        it is set to [] (the existing convention) when no video was collected.
        """
        new_list = []
        self.author_results = []
        self._reset_api_key_state()
        self._uploads_ids_resolved = False
        current_time = int(datetime.now().timestamp())

        if not self._get_api_keys():
            logging.warning('No usable YouTube API key found in api_key_list')
        if not isinstance(self.authors_list, list) or not self.authors_list:
            logging.warning('YouTube authors list is empty')

        authors = self.authors_list if isinstance(self.authors_list, list) else []

        for author in authors:
            try:
                articles, result = self._collect_articles_for_author(
                    author, current_time, retry_times=retry_times,
                    max_results=max_results, part=part)
            except Exception as error:
                # Last-resort isolation: never let one author break the whole run.
                logging.exception(f'Unexpected error while collecting a YouTube author: {error}')
                author_id = author.get('author_id') if isinstance(author, dict) else None
                author_name = author.get('author_name') if isinstance(author, dict) else None
                articles = []
                result = {
                    'author_id': None if author_id is None else str(author_id),
                    'author_name': None if author_name is None else str(author_name),
                    'status': 'failed',
                    'article_count': None,
                    'error': f'unexpected error: {error}',
                }

            self.author_results.append(result)
            new_list.extend(articles)

            author_key = result.get('author_id') or result.get('author_name') or '<unknown>'
            if result['status'] == 'success':
                logging.info(
                    f"YouTube author '{result.get('author_name')}' ({author_key}) "
                    f"succeeded: {result['article_count']} video(s)")
            else:
                logging.error(
                    f"YouTube author '{result.get('author_name')}' ({author_key}) "
                    f"failed: {result['error']}")

        # Persist uploads_id values resolved during this run.
        self._write_back_uploads_ids()

        if new_list:
            self.articles_json = json.dumps(new_list, ensure_ascii=False)
        else:
            self.articles_json = []
            logging.warning(f"No YouTube videos collected in {self}; articles_json is set to []")

        return self.articles_json

    def start(self, authors_list_path=None, api_key_path=None):
        if authors_list_path is None:
            module_dir = os.path.dirname(os.path.abspath(__file__))
            authors_list_path = os.path.join(module_dir, '..', 'conf', 'youtube_authors_list.json')
        if api_key_path is None:
            module_dir = os.path.dirname(os.path.abspath(__file__))
            api_key_path = os.path.join(module_dir, '..', 'conf', 'youtube_apikey_list.json')
        self.load_api_key(api_key_path)
        self.load_authors(authors_list_path)
        self.get_articles_list()


if __name__ == "__main__":
    ytb = YoutubeSpider()
    ytb.start()
    print(ytb.articles_json)
