import uuid, re, os, errno, time, threading, tempfile
from crawlbase.base_api import BaseAPI

#
# A Python class that acts as wrapper for Crawlbase Screenshots API.
#
# Read Crawlbase API documentation https://crawlbase.com/docs/screenshots-api/
#
# Copyright Crawlbase
# Licensed under the Apache License 2.0
#
class ScreenshotsAPI(BaseAPI):
    base_path = 'screenshots'

    # Headers that the Screenshots API reports on a successful response. They
    # are kept on failures too so that callers can rely on a stable structure.
    __response_headers = ('success', 'url', 'remaining_requests', 'screenshot_url')

    # Striped locks that serialize saves to the same path inside one process.
    # Cross-process writers are still safe thanks to the atomic replacement.
    __save_locks = [threading.Lock() for _ in range(32)]

    def get(self, url, options = None):
        options = dict(options) if options else {}
        screenshotPath = options.pop('save_to_path') if 'save_to_path' in options else self.__generateFilepath()
        if not re.match(r".+\.(jpg|JPG|jpeg|JPEG)$", screenshotPath):
            raise Exception('save_to_path must end with .jpg or .jpeg')
        options['url'] = url
        response = self.request(options)
        if str(response.get('status_code')) == '200':
            self.__saveScreenshot(screenshotPath, response.get('body'))
            response['file'] = screenshotPath
        else:
            self.__markFailure(response)
        return response

    def post(self, url, data, options = {}):
        raise Exception('Only GET is allowed on the Screenshots API')

    def parseRegularResponse(self, handler):
        headers = handler.headers
        BaseAPI.parseRegularResponse(self, handler)
        self.response['headers']['success'] = str(headers.get('success'))
        self.response['headers']['remaining_requests'] = str(headers.get('remaining_requests'))
        self.response['headers']['screenshot_url'] = str(headers.get('screenshot_url'))

    def __markFailure(self, response):
        """Keep the regular headers and record why the screenshot was not saved."""
        headers = response.get('headers')
        if not isinstance(headers, dict):
            headers = response['headers'] = {}
        for header in self.__response_headers:
            headers.setdefault(header, 'None')
        headers['failure_reason'] = 'Request failed with status code %s' % response.get('status_code')

    def __saveScreenshot(self, screenshotPath, body):
        """Write the screenshot atomically so concurrent saves never interleave."""
        if body is None:
            body = b''
        elif not isinstance(body, bytes):
            body = body.encode('utf-8')
        directory = os.path.dirname(screenshotPath) or os.getcwd()
        with self.__pathLock(screenshotPath):
            fd, temporaryPath = tempfile.mkstemp(prefix='.screenshot-', suffix='.tmp', dir=directory)
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(body)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        # Flushing the buffer is enough for the atomic replace;
                        # some filesystems do not support fsync on temp files.
                        pass
                self.__replaceFile(temporaryPath, screenshotPath)
            except Exception:
                try:
                    os.remove(temporaryPath)
                except OSError:
                    pass
                raise

    def __pathLock(self, screenshotPath):
        key = os.path.normcase(os.path.abspath(screenshotPath))
        return self.__save_locks[hash(key) % len(self.__save_locks)]

    def __replaceFile(self, source, target):
        # os.replace is atomic and overwrites the target on Python 3.3+.
        replace = getattr(os, 'replace', None)
        if replace is None:
            # Fallback for Python 2, which lacks os.replace.
            if os.path.exists(target):
                os.remove(target)
            os.rename(source, target)
            return
        # Cross-process replacements of the same target can transiently fail on
        # Windows with a sharing/permission error, so retry briefly before giving up.
        attempts = 10
        for attempt in range(attempts):
            try:
                replace(source, target)
                return
            except OSError as error:
                if attempt == attempts - 1 or not self.__isRetryableReplaceError(error):
                    raise
                time.sleep(0.01 * (attempt + 1))

    def __isRetryableReplaceError(self, error):
        # errno EACCES/EPERM/EBUSY, plus Windows ERROR_SHARING_VIOLATION (32)
        # and ERROR_ACCESS_DENIED (5) reported through winerror.
        if error.errno in (errno.EACCES, errno.EPERM, errno.EBUSY):
            return True
        return getattr(error, 'winerror', None) in (5, 32)

    def __generateFilename(self):
        return str(uuid.uuid4()) + '.jpg'

    def __generateFilepath(self):
        return os.path.join(tempfile.gettempdir(), self.__generateFilename())
