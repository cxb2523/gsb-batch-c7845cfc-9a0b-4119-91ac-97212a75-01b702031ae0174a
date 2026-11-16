"""Page adapters: the injectable seam between the executor and "the page".

The executor only ever talks to the :class:`PageAdapter` interface, so a
real browser driver could be dropped in later. The default implementation,
:class:`StaticPageAdapter`, operates purely on local static HTML files —
no browser, no network access.
"""

from __future__ import annotations

import os
from html.parser import HTMLParser


class PageError(Exception):
    """An adapter operation could not be completed (missing element/page)."""


class PageAdapter:
    """Interface the executor depends on. Implementations must be
    deterministic: the same sequence of calls yields the same results."""

    def click(self, selector):
        raise NotImplementedError

    def read_text(self, selector):
        raise NotImplementedError

    def navigate(self, url):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Minimal DOM for the static adapter
# ---------------------------------------------------------------------------

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _Node:
    __slots__ = ("tag", "attrs", "children", "text_parts", "parent")

    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = attrs
        self.children = []
        self.text_parts = []
        self.parent = parent

    def text(self):
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.text())
        return " ".join("".join(parts).split())

    def iter(self):
        yield self
        for child in self.children:
            yield from child.iter()


class _DOMParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("[document]", {})
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for pos in range(len(self._stack) - 1, 0, -1):
            if self._stack[pos].tag == tag:
                del self._stack[pos:]
                return

    def handle_data(self, data):
        if data.strip():
            self._stack[-1].text_parts.append(data)


def _matches(node, selector):
    if selector.startswith("#"):
        return node.attrs.get("id") == selector[1:]
    if selector.startswith("."):
        classes = node.attrs.get("class", "").split()
        return selector[1:] in classes
    return node.tag == selector


# ---------------------------------------------------------------------------
# Static (local file) adapter
# ---------------------------------------------------------------------------


class StaticPageAdapter(PageAdapter):
    """Adapter backed by a directory of local static HTML files.

    ``navigate`` resolves the URL as a path relative to ``pages_dir`` and
    loads that file. ``click`` verifies the element exists and, if it
    carries a ``data-href`` attribute, follows it like a link. All state
    lives in memory; nothing touches the network or a browser.
    """

    def __init__(self, pages_dir, start_page=None):
        self.pages_dir = os.path.abspath(pages_dir)
        self.current_page = None
        self._root = None
        if start_page is not None:
            self.navigate(start_page)

    # -- PageAdapter interface -------------------------------------------

    def click(self, selector):
        node = self._find(selector)
        href = node.attrs.get("data-href")
        if href:
            self.navigate(href)

    def read_text(self, selector):
        return self._find(selector).text()

    def navigate(self, url):
        if "://" in str(url):
            raise PageError(
                "refusing to navigate to remote URL %r: "
                "the static adapter only loads local pages" % (url,)
            )
        path = os.path.abspath(os.path.join(self.pages_dir, url))
        if not path.startswith(self.pages_dir + os.sep) and path != self.pages_dir:
            raise PageError("refusing to navigate outside pages_dir: %r" % (url,))
        if not os.path.isfile(path):
            raise PageError("page not found: %r" % (url,))
        with open(path, "r", encoding="utf-8") as handle:
            parser = _DOMParser()
            parser.feed(handle.read())
        self.current_page = url
        self._root = parser.root

    # -- internals ---------------------------------------------------------

    def _find(self, selector):
        if self._root is None:
            raise PageError("no page loaded; navigate somewhere first")
        if not selector:
            raise PageError("step is missing a selector")
        for node in self._root.iter():
            if _matches(node, selector):
                return node
        raise PageError(
            "selector %r not found on page %r" % (selector, self.current_page)
        )
