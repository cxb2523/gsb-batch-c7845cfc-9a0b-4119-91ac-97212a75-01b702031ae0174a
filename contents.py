import time
from typing import NamedTuple

import PyPDF2
import docx
import readability
import requests
import xxhash
from langdetect import detect
from newspaper import fulltext, Article
from selenium import webdriver

# How the raw HTML for an article was obtained.
SOURCE_BROWSER = 'browser'
SOURCE_STATIC = 'static'

SOURCE_LABELS = {
    SOURCE_BROWSER: 'browser rendering',
    SOURCE_STATIC: 'static HTTP request',
}


def describe_source(source: str) -> str:
    """Return a human-readable description of how the web content was obtained."""
    return SOURCE_LABELS.get(source, source)


def hash_contents(contents: list[str]) -> str:
    """Stable hash used to identify an article; works with both xxhash 3.x and 4.x."""
    return xxhash.xxh3_128_hexdigest('\n'.join(contents).encode('utf-8'))


_USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
               'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36')


class CrawlResult(NamedTuple):
    """The result of crawling a web page."""
    contents: list[str]
    lang: str
    source: str  # SOURCE_BROWSER or SOURCE_STATIC


def _brief_error(error: Exception) -> str:
    """One-line, length-limited description of an exception for user-facing messages."""
    message = ' '.join(str(error).split())
    if not message:
        message = error.__class__.__name__
    elif not message.startswith(error.__class__.__name__):
        message = f'{error.__class__.__name__}: {message}'
    return message if len(message) <= 300 else message[:297] + '...'


class WebCrawlError(RuntimeError):
    """Raised when both the browser rendering and the static request failed."""

    def __init__(self, url: str, browser_error: Exception, static_error: Exception):
        self.url = url
        self.browser_error = browser_error
        self.static_error = static_error
        super().__init__(
            f'Failed to crawl {url}. '
            f'Browser rendering stage failed: {_brief_error(browser_error)}. '
            f'Static request stage failed: {_brief_error(static_error)}.'
        )


def web_crawler_newspaper(url: str) -> CrawlResult:
    """Crawl a web page, falling back to a plain HTTP request when rendering fails.

    The returned ``source`` field tells which path produced the contents
    (``SOURCE_BROWSER`` for a rendered page, ``SOURCE_STATIC`` for a plain request).
    Raises ``WebCrawlError`` when both paths fail, reporting each stage's reason.
    """
    try:
        contents, lang = _crawl(url, _get_readable_html_from_browser)
        return CrawlResult(contents, lang, SOURCE_BROWSER)
    except Exception as browser_error:
        print(f"Browser rendering failed ({_brief_error(browser_error)}); "
              f"falling back to a static HTTP request.")
        try:
            contents, lang = _crawl(url, _get_readable_html_static)
        except Exception as static_error:
            raise WebCrawlError(url, browser_error, static_error) from None
        return CrawlResult(contents, lang, SOURCE_STATIC)


def _crawl(url: str, fetch_readable_html) -> tuple[list[str], str]:
    """Fetch readable HTML with one strategy and extract the article text.

    Both strategies share the same extraction rules; an empty result is treated
    as a failure so that the caller can report it instead of returning nothing.
    """
    readable_html, lang = fetch_readable_html(url)
    contents = _extract_contents(readable_html, lang, url)
    if not contents:
        raise RuntimeError(f'{fetch_readable_html.__name__} produced no article content')
    return contents, lang


def _extract_contents(readable_html: str, lang: str, url: str) -> list[str]:
    """Extract article text from the readable HTML using the same rules for both sources."""
    text = ''
    try:
        text = fulltext(readable_html, language=lang) or ''
    except Exception:
        text = ''
    if not text.strip():
        article = Article(url)
        article.set_html(readable_html)
        article.parse()
        text = article.text or ''
    return [line.strip() for line in text.splitlines() if line.strip()]


def _get_readable_html_from_browser(url: str) -> tuple[str, str]:
    """Render the page in headless Chrome and return its readable HTML and language."""
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument(f'--user-agent={_USER_AGENT}')

    driver = webdriver.Chrome(options=chrome_options)
    try:
        driver.get(url)
        print("Please wait for 5 seconds until the webpage finishes loading.")
        time.sleep(5)
        html = driver.page_source
    finally:
        try:
            driver.quit()
        except Exception:
            # A failing teardown must not discard an otherwise successful render.
            pass

    if not html or not html.strip():
        raise RuntimeError('browser rendered an empty page')
    return _readable_html_and_lang(html)


def _get_readable_html_static(url: str) -> tuple[str, str]:
    """Fetch the page with a plain HTTP request and return its readable HTML and language."""
    response = requests.get(url, headers={'User-Agent': _USER_AGENT}, timeout=30)
    response.raise_for_status()
    # requests defaults to ISO-8859-1 when the server omits the charset; let it guess instead.
    if not response.encoding or response.encoding.lower() == 'iso-8859-1':
        response.encoding = response.apparent_encoding
    html = response.text
    if not html or not html.strip():
        raise RuntimeError('static request returned an empty response body')
    return _readable_html_and_lang(html)


def _readable_html_and_lang(html: str) -> tuple[str, str]:
    """Reduce the HTML to the main article body and detect its language."""
    doc = readability.Document(html)
    readable_html = doc.summary()
    if not readable_html or not readable_html.strip():
        raise RuntimeError('readability produced an empty document')
    lang = detect(readable_html)
    return readable_html, lang[0:2]


def extract_text_from_pdf(file_path: str) -> tuple[list[str], str]:
    """Extract text content from a PDF file."""
    with open(file_path, 'rb') as f:
        pdf_reader = PyPDF2.PdfReader(f)
        contents = []
        for page in pdf_reader.pages:
            page_text = page.extract_text().strip()
            raw_text = [text.strip() for text in page_text.splitlines() if text.strip()]
            new_text = ''
            for text in raw_text:
                new_text += text
                if text[-1] in ['.', '!', '?', '。', '！', '？', '…', ';', '；', ':', '：', '”', '’', '）', '】', '》', '」',
                                '』', '〕', '〉', '》', '〗', '〞', '〟', '»', '"', "'", ')', ']', '}']:
                    contents.append(new_text)
                    new_text = ''
            if new_text:
                contents.append(new_text)
        lang = detect('\n'.join(contents))
        return contents, lang[0:2]


def extract_text_from_txt(file_path: str) -> tuple[list[str], str]:
    """Extract text content from a TXT file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        contents = [text.strip() for text in f.readlines() if text.strip()]
        lang = detect('\n'.join(contents))
        return contents, lang[0:2]


def extract_text_from_docx(file_path: str) -> tuple[list[str], str]:
    """Extract text content from a DOCX file."""
    document = docx.Document(file_path)
    contents = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    lang = detect('\n'.join(contents))
    return contents, lang[0:2]
