from datetime import datetime
from rich.table import Table
from rich.console import Console
import dotenv
import logging
import os
import sys

dotenv.load_dotenv()


def _make_stdout_encoding_lenient():
    """Never let the console codec abort a run.

    On Windows terminals (and when piping output) stdout may use a legacy code
    page such as GBK that cannot encode the status glyphs rich prints. Encoding
    errors then raise ``UnicodeEncodeError`` in the middle of a scrape. Replace
    such characters instead of crashing; the user still sees the status text.
    """
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="replace")
    except Exception:  # noqa: BLE001 - best effort only
        pass


_make_stdout_encoding_lenient()

logger = logging.getLogger('skyffel')

ch = logging.StreamHandler()

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

logger.setLevel(LOG_LEVEL)
ch.setLevel(LOG_LEVEL)
logger.addHandler(ch)
