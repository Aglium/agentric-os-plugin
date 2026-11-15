"""Checks a value against a JSON Schema, the part of JSON Schema the Agentric OS schema uses.

Standard library only, so that the plugin can carry it. oneOf is taken as anyOf: a value is right when one of the ways fits. Supported: type, enum, const, pattern, minimum, minItems,
required, properties, additionalProperties, items, oneOf, anyOf, not, and $ref within the same schema (draft 2020-12
and draft 4, which Camel's YAML DSL schema is written in).
Each problem is said with where it is (a path such as operations[2].kind) and what is wrong, in plain words.
"""
import re

TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def problems(schema, value, scalars_as_text=False):
    """Every problem of value against schema, as a list of (path, message).

    scalars_as_text: a number or true/false is taken where a text is asked for, as a reader of YAML that turns
    every value into the type it needs does (Camel's YAML DSL does; the platform's own files do not).
    """
    found = []
    _check(schema, schema, value, "", found, scalars_as_text)
    return found


def _where(path):
    return path or "the whole file"


def _resolve(root, ref):
    if not ref.startswith("#/"):
        raise ValueError("only references within the same schema are supported: " + ref)
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(root, schema, value, path, found, lenient=False):
    if "$ref" in schema:
        _check(root, _resolve(root, schema["$ref"]), value, path, found, lenient)
    kind = schema.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        fits = any(TYPES[k](value) for k in kinds)
        if not fits and lenient and "string" in kinds and isinstance(value, (int, float, bool)):
            fits = True
        if not fits:
            found.append((path, "has to be " + " or ".join(_type_words(k) for k in kinds) + ", not " + _what(value)))
            return
    if "enum" in schema and value not in schema["enum"]:
        found.append((path, "is “%s”; it can be one of: %s" % (value, ", ".join(str(e) for e in schema["enum"]))))
    if "const" in schema and value != schema["const"]:
        found.append((path, "has to be “%s”" % schema["const"]))
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        found.append((path, "“%s” is not written as it should be (%s)" % (value, _short(schema))))
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        found.append((path, "has to be at least %s" % schema["minimum"]))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            found.append((path, "has to list at least %d" % schema["minItems"]))
        if "items" in schema:
            for i, item in enumerate(value):
                _check(root, schema["items"], item, "%s[%d]" % (path, i), found, lenient)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value or value[key] is None:
                found.append((path, "lacks “%s”" % key))
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            at = (path + "." if path else "") + str(key)
            if key in properties:
                _check(root, properties[key], item, at, found, lenient)
            elif extra is False:
                found.append((at, "is not something this can have; the ones it can have: " + ", ".join(properties)))
            elif isinstance(extra, dict):
                _check(root, extra, item, at, found, lenient)
    if "not" in schema and not problems_within(root, schema["not"], value, lenient):
        found.append((path, "is written in a way it cannot be (%s)" % _short(schema)))
    for combine in ("oneOf", "anyOf"):
        if combine in schema:
            tried = [(problems_within(root, s, value, lenient), s) for s in schema[combine]]
            fits = [s for p, s in tried if not p]
            if not fits:
                # say what is wrong with the way that came closest: what it lacks is more use than "none fits"
                closest = min(tried, key=lambda t: _distance(t[0]))[0]
                for at, message in closest:
                    found.append((_join(path, at), message))


def _distance(found):
    # a way of another type altogether is farther than one of the right type with a mistake inside
    return sum(100 if not at and message.startswith("has to be") else 1 for at, message in found)


def _join(path, at):
    if not at:
        return path
    if not path:
        return at
    return path + ("" if at.startswith("[") else ".") + at


def problems_within(root, schema, value, lenient=False):
    found = []
    _check(root, schema, value, "", found, lenient)
    return found


def _short(schema):
    text = schema.get("description", "")
    return text.split(". ")[0] if text else "see the schema"


def _type_words(kind):
    return {"object": "a set of named values", "array": "a list", "string": "a text", "number": "a number",
            "integer": "a whole number", "boolean": "true or false", "null": "empty"}[kind]


def _what(value):
    for kind in ("boolean", "integer", "number", "string", "array", "object", "null"):
        if TYPES[kind](value):
            return _type_words(kind)
    return type(value).__name__
