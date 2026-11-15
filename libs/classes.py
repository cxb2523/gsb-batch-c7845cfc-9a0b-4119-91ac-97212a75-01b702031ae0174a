from datetime import date
import json


class SpiderURL:
    def __init__(self):
        # The original fields are kept first, in their original order, so that
        # the JSON keys the backend already reads remain stable.
        self.new_urls = []
        self.online = False
        self.url = None
        self.scan_date = date.today().strftime('%Y-%m-%d')
        self.last_node = None
        self.fault = None
        self.title = None
        self.form_dicts = []
        self.hash = None

        # Additional fields are appended only. Older backend code can keep
        # reading the keys above and simply ignore these.
        #
        # The redirect target discovered for this url, even when it cannot be
        # followed (for example, when it is not a valid .onion address).
        self.redirect = None
        # A human-readable reason explaining why the redirect target was not
        # fetched, or None when no redirect problem occurred.
        self.redirect_fault = None
        # The content type detected for the fetched page. 'unknown' means the
        # server did not tell us, and the page was parsed as text anyway.
        self.content_type = None
        # True when the content type could not be confirmed and the page was
        # parsed as text on a best-effort basis.
        self.type_unknown = False
        # The HTTP status code observed for the scan, when one was received.
        self.status_code = None
        # The redirect status code that led to the redirect target, or None.
        self.redirect_status = None
        # The furthest stage the scan reached: 'init', 'request', 'head',
        # 'redirect', 'content_type', 'parse', 'unchanged', 'complete' or
        # 'error'.
        self.scan_stage = 'init'
        # A human-readable explanation of the fault (or of why the scan
        # stopped early). This is additive detail; 'fault' keeps its original
        # meaning.
        self.fault_reason = None

    def to_json(self):
        return json.dumps(self.__dict__)
