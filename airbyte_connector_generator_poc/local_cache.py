import os
import json
import tempfile


class LocalCache():
    def __init__(self, cache_file_path):
        if not cache_file_path.endswith('.json'):
            raise ValueError("Cache file path must end with '.json'")
        self.cache_file_path = cache_file_path
        if not os.path.exists(self.cache_file_path):
            with open(self.cache_file_path, 'w', encoding='utf-8') as cache_file:
                # Create an empty JSON object in the file
                cache_file.write('{}')

    def _read_all(self) -> dict:
        with open(self.cache_file_path, 'r', encoding='utf-8') as cache_file:
            file_content = cache_file.read()
        try:
            return json.loads(file_content) if file_content.strip() else {}
        except json.JSONDecodeError:
            # A corrupted cache must not abort the run; start from scratch.
            return {}

    def get(self, key):
        return self._read_all().get(key)

    def set(self, key: str, value: dict):
        cache_data = self._read_all()
        cache_data[key] = value
        self._write_all(cache_data)

    def _write_all(self, cache_data: dict):
        """Write atomically so an interrupted run cannot corrupt the cache."""
        directory = os.path.dirname(self.cache_file_path) or "."
        fd, temp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as cache_file:
                cache_file.write(json.dumps(cache_data, indent=2))
            os.replace(temp_path, self.cache_file_path)
        except BaseException:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
