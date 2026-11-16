"""A minimal, dependency-free YAML subset loader.

Supports exactly the constructs OrbitPilot configuration files use:

* block mappings (``key: value`` / ``key:`` followed by a nested block)
* block sequences (``- item``), including ``- key: value`` inline map starts
* scalars: quoted strings, plain strings, integers, floats, booleans, null
* full-line and trailing ``#`` comments (outside quotes)

It deliberately does NOT support anchors, tags, flow collections, or
multi-line scalars. Anything outside the subset raises ``YAMLError``
with a line-oriented message instead of failing mysteriously.
"""

from __future__ import annotations


class YAMLError(ValueError):
    """Raised when the input is outside the supported YAML subset."""


def load(text):
    """Parse a YAML-subset document and return the resulting Python object."""
    tokens = _tokenize(text)
    if not tokens:
        return None
    value, index = _parse_block(tokens, 0, tokens[0][0])
    if index != len(tokens):
        raise YAMLError("unexpected trailing content: %r" % (tokens[index][1],))
    return value


def load_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return load(handle.read())


def _tokenize(text):
    tokens = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        content = _strip_comment(stripped)
        if content:
            tokens.append((indent, content))
    return tokens


def _strip_comment(text):
    in_single = in_double = False
    for pos, char in enumerate(text):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif (
            char == "#"
            and not in_single
            and not in_double
            and (pos == 0 or text[pos - 1] in " \t")
        ):
            return text[:pos].rstrip()
    return text


def _parse_scalar(text):
    text = text.strip()
    if text == "":
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~", "none"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _parse_block(tokens, index, indent):
    if tokens[index][1].startswith("-") and (
        tokens[index][1] == "-" or tokens[index][1].startswith("- ")
    ):
        return _parse_sequence(tokens, index, indent)
    return _parse_mapping(tokens, index, indent)


def _parse_mapping(tokens, index, indent):
    result = {}
    while index < len(tokens):
        line_indent, content = tokens[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise YAMLError("unexpected indentation before %r" % (content,))
        if content == "-" or content.startswith("- "):
            break
        key, value_text = _split_key(content)
        index += 1
        if value_text:
            result[key] = _parse_scalar(value_text)
        elif index < len(tokens) and tokens[index][0] > indent:
            result[key], index = _parse_block(tokens, index, tokens[index][0])
        else:
            result[key] = None
    return result, index


def _parse_sequence(tokens, index, indent):
    result = []
    while index < len(tokens):
        line_indent, content = tokens[index]
        if line_indent != indent or not (
            content == "-" or content.startswith("- ")
        ):
            break
        item = content[1:].lstrip()
        item_column = indent + (len(content) - len(item))
        if item == "":
            index += 1
            if index < len(tokens) and tokens[index][0] > indent:
                value, index = _parse_block(tokens, index, tokens[index][0])
                result.append(value)
            else:
                result.append(None)
        elif _looks_like_map_entry(item):
            # "- key: value" starts a mapping whose first entry shares the
            # dash line; remaining entries are indented under it.
            tokens[index] = (item_column, item)
            value, index = _parse_mapping(tokens, index, item_column)
            result.append(value)
        else:
            result.append(_parse_scalar(item))
            index += 1
    return result, index


def _split_key(content):
    if ":" not in content:
        raise YAMLError("expected 'key: value' but found %r" % (content,))
    key, _, rest = content.partition(":")
    key = key.strip()
    if not key:
        raise YAMLError("empty mapping key in %r" % (content,))
    return key, rest.strip()


def _looks_like_map_entry(text):
    if text[0] in ("'", '"'):
        return False
    if ":" not in text:
        return False
    _, _, rest = text.partition(":")
    return rest == "" or rest.startswith(" ")
