#coding=utf-8
import datetime
from datetime import datetime
from datetime import timedelta
import json
import os
from urllib.parse import quote


# ---------------------------------------------------------------------------
# Per-word outcomes used by the daily mission and its report
# ---------------------------------------------------------------------------
OUTCOME_COMPLETED = u"完成"                 # reached the two-days-ago boundary
OUTCOME_NO_DATA = u"无数据"                 # the search returned no weibo cards
OUTCOME_REQUEST_ABANDONED = u"请求异常放弃"   # requests kept failing, gave up on this word
OUTCOME_WRITE_FAILED = u"写入失败"           # page could not be written after one retry

# Words that count as "failed" in the daily report.  "无数据" is a normal
# (non-error) result, so it is not included here.
FAILED_OUTCOMES = (OUTCOME_REQUEST_ABANDONED, OUTCOME_WRITE_FAILED)

# The search API answers "this word has no content" with ok=0 plus one of
# these messages.  They mean the word genuinely has no result, unlike other
# ok=0 replies (rate limiting, refusal) which must not end the word silently.
NO_DATA_MESSAGES = (u"还没有内容", u"没有内容", u"暂无内容", u"内容为空")


class NoDataError(Exception):
    """Raised when a valid search page contains no weibo card at all."""
    pass


class InvalidPageError(Exception):
    """Raised when a response is not a usable search page.

    This covers truncated JSON, a rejected request (ok == 0), or a changed
    JSON structure.  It is deliberately *not* treated as "no data": the page
    is unusable, so it must be retried/reported rather than recorded as an
    empty result.
    """
    pass


class EmptyContentError(Exception):
    """Raised when a page body is empty and therefore must not be saved."""
    pass



def today():
    time = str(datetime.now())
    # print time
    date = time.split(" ")[0]
    day = date[5:]
    # print day
    return day


def yesterday():
    yest = datetime.now() - timedelta(days=1)
    date = str(yest).split(" ")[0]
    day = date[5:]
    # print day
    return day

def days_ago(n):
    yest = datetime.now() - timedelta(days=n)
    date = str(yest).split(" ")[0]
    day = date[5:]
    print(day)
    return day


def days_ago_text(n, now=None):
    """Return the [MM-DD] text of *n* days before *now* without printing."""
    if now is None:
        now = datetime.now()
    return (now - timedelta(days=n)).strftime("%m-%d")


def url_encoding(list):
    url_word_list = []
    for country in list:
        url_word_list.append(quote(country))
    return url_word_list



def create_url_list(list):
    url_list = []
    for country in list:
        this_url = "http://m.weibo.cn/container/getIndex?type=wb&queryVal=" + country + \
          "&luicode=10000011&lfid=106003type%3D1&title=" + country + \
          "&containerid=100103type%3D2%26q%3D" + country + "&page="
        url_list.append(this_url)
    return url_list



def format_datetime(time):
    this_date = time.split(' ')[0]
    this_time = time.split(' ')[1]
    mon = this_date.split('-')[0]
    day = this_date.split('-')[1]
    hor = this_time.split(':')[0]
    min = this_time.split(':')[1]
    return datetime(2017, int(mon), int(day), int(hor), int(min))


def get_this_endtime_text(content):
    """Return the created_at text of the last weibo on this page.

    Raises NoDataError only when the response is a genuine "no result" from
    the search API.  A truncated body, a rate-limit refusal or a changed
    structure raises InvalidPageError / ValueError instead, so the caller
    treats it as a bad page rather than an empty result.
    """
    if isinstance(content, bytes):
        content = content.decode('utf-8')
    json_data = json.loads(content)

    if not isinstance(json_data, dict):
        raise InvalidPageError("response is not a JSON object")

    if json_data.get("ok") == 0:
        # The API uses ok=0 both for "this search has no content" and for
        # refusals such as rate limiting.  Only the former is a real no-data
        # result; a refusal must not silently end the word.
        message = json_data.get("msg")
        if not isinstance(message, str):
            message = u""
        if any(hint in message for hint in NO_DATA_MESSAGES):
            raise NoDataError("search returned no content: " + message)
        raise InvalidPageError("request rejected: " + message)

    data = json_data.get("data")
    if not isinstance(data, dict) or "cards" not in data:
        raise InvalidPageError("response has no data.cards")

    cards = data["cards"]
    if not cards:
        raise NoDataError("search returned no card")

    card_group = cards[0].get("card_group") if isinstance(cards[0], dict) else None
    if not card_group:
        # Same as the original crawler's IndexError branch: this response
        # carries no weibo item to read.
        raise NoDataError("search card has no weibo item")

    try:
        this_endtime_text = card_group[-1]['mblog']['created_at']
    except (KeyError, IndexError, TypeError):
        raise InvalidPageError("weibo item has no created_at")
    return this_endtime_text


def sleep_how_long(lag_hour, starttime, endtime):
    during = (endtime - starttime).total_seconds()
    lag_seconds = lag_hour * 3600
    during = lag_seconds - int(during)
    return during


# ---------------------------------------------------------------------------
# Page file helpers
# ---------------------------------------------------------------------------
def ensure_folder(folder):
    """Make sure *folder* exists (safe when another process created it)."""
    if os.path.isdir(folder):
        return
    try:
        os.makedirs(folder)
    except OSError:
        if not os.path.isdir(folder):
            raise


def page_file_path(data_root, word, page_number, now=None):
    """Build the page path from the exact time the page is written.

    Both the date folder and the date inside the file name come from *now*,
    so a mission that crosses midnight writes its later pages into the new
    day's folder instead of the day the program started.
    """
    if now is None:
        now = datetime.now()
    date_text = now.strftime("%m-%d")
    file_name = word + date_text + "page" + str(page_number) + ".txt"
    return os.path.join(data_root, date_text, file_name)


def write_page_file(data_root, word, page_number, content, now=None):
    """Write one complete page and return its path.

    An empty body raises EmptyContentError.  A partially written file raises
    IOError and is removed, so a damaged page is never mistaken for a
    successful page.  The caller is responsible for retrying.
    """
    if content is None or len(content) == 0:
        raise EmptyContentError("empty page content")
    if not isinstance(content, bytes):
        content = content.encode("utf-8")

    path = page_file_path(data_root, word, page_number, now=now)
    ensure_folder(os.path.dirname(path))

    try:
        with open(path, "wb") as f:
            f.write(content)
        if os.path.getsize(path) != len(content):
            raise IOError("incomplete page write: " + path)
    except Exception:
        # Do not leave an empty/half page behind: it must not look like data.
        try:
            if os.path.exists(path) and not os.path.isdir(path):
                os.remove(path)
        except OSError:
            pass
        raise
    return path


def append_report(logfile, report_text):
    """Append the daily report to the log as UTF-8."""
    if not isinstance(report_text, bytes):
        report_text = report_text.encode("utf-8")
    with open(logfile, "ab") as f:
        f.write(report_text)
