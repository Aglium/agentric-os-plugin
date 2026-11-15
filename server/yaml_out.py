"""Writes values as YAML, with the standard library only.

The plugin builds configurations as data and writes them out at the end, so it only ever writes YAML, never reads it.
What it writes reads back as exactly the value it was given: a text that YAML would take for something else (yes,
no, on, 007, 1e3, null, a colon followed by a space...) is quoted, a text of several lines is a literal block.
"""
import json
import re

PLAIN = re.compile(r"^[A-Za-z_/$][A-Za-z0-9_ ./()$'{}\[\]-]*$")
# words YAML 1.1 (which Camel and the platform read with) takes for something other than a text
RESERVED = {"y", "n", "yes", "no", "true", "false", "on", "off", "null", "~"}


def dump(value):
    """The value as a YAML document."""
    lines = []
    _write(value, 0, lines, None)
    return "\n".join(lines) + "\n"


def _write(value, indent, lines, prefix):
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            _line(lines, prefix, pad, "{}")
            return
        first = True
        for key, item in value.items():
            head = (prefix if first and prefix is not None else pad) + _scalar(str(key)) + ":"
            first = False
            if isinstance(item, (dict, list)) and item:
                lines.append(head)
                _write(item, indent + 2, lines, None)
            else:
                lines.append(head + " " + _inline(item, indent + 2))
        return
    if isinstance(value, list):
        if not value:
            _line(lines, prefix, pad, "[]")
            return
        first = True
        for item in value:
            lead = (prefix if first and prefix is not None else pad) + "- "
            first = False
            if isinstance(item, dict) and item:
                _write(item, indent + 2, lines, lead)
            elif isinstance(item, list) and item:
                lines.append(lead.rstrip())
                _write(item, indent + 2, lines, None)
            else:
                lines.append(lead + _inline(item, indent + 2))
        return
    _line(lines, prefix, pad, _inline(value, indent))


def _line(lines, prefix, pad, text):
    lines.append((prefix if prefix is not None else pad) + text)


def _inline(value, indent):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, dict) and not value:
        return "{}"
    if isinstance(value, list) and not value:
        return "[]"
    text = str(value)
    if "\n" in text and "\r" not in text and "\t" not in text and not text.startswith((" ", "\n")) \
            and all(line == line.rstrip() for line in text.split("\n")):
        if text.endswith("\n\n"):
            keep, body = "+", text[:-1]
        elif text.endswith("\n"):
            keep, body = "", text[:-1]
        else:
            keep, body = "-", text
        inner = " " * indent
        return "|" + keep + "\n" + "\n".join(inner + line if line else "" for line in body.split("\n"))
    return _scalar(text)


def _scalar(text):
    if (PLAIN.match(text) and text.lower() not in RESERVED and not _looks_like_number(text)
            and not text.endswith((" ", ":")) and ": " not in text and " #" not in text and not text.startswith("- ")):
        return text
    return json.dumps(text, ensure_ascii=False)


def _looks_like_number(text):
    return bool(re.match(r"^[-+]?(\.[0-9]|[0-9])", text)) or text.lower() in (".inf", "-.inf", ".nan")
