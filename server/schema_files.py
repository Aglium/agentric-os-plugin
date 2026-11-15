"""The schema (what a configuration may say), from the installation when there is one, else the copy this plugin carries.

An installation gives the schema it runs on at /api/schema; building against it means building for that version. The
copy in schema/ (ci/sync-schema.sh) is used when no installation is set up or it cannot be reached, and the answer
says so, so that nobody takes a check against the carried copy for a check against their installation.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CARRIED = os.path.join(os.path.dirname(HERE), "schema")


class Schema:
    def __init__(self, fetch=None):
        """fetch(name) reads a file of the installation's schema, or raises when there is no installation to ask."""
        self.fetch = fetch
        self.files = {}
        self.source = None

    def file(self, name):
        if name in self.files:
            return self.files[name]
        value = None
        if self.fetch is not None and self.source != "carried":
            try:
                value = self.fetch(name)
                self.source = "installation"
            except Exception:  # no installation, or it does not give its schema: build with the carried copy
                if self.source == "installation":
                    # an installation of an older schema lacks a file a newer plugin uses: take that one carried
                    self.older = sorted(set(getattr(self, "older", [])) | {name})
                else:
                    self.source = "carried"
        if value is None:
            path = os.path.join(CARRIED, *name.split("/"))
            if not os.path.isfile(path):
                raise LookupError("the schema has no file %s" % name)
            with open(path, encoding="utf-8") as f:
                value = json.load(f)
            self.source = self.source or "carried"
        self.files[name] = value
        return value

    def where(self):
        """In words, which schema the answers are built on."""
        index = self.file("index.json")
        if self.source == "installation":
            said = "the schema of the installation (version %s)" % index.get("version")
            if getattr(self, "older", None):
                said += "; the installation does not have %s, so the plugin's own copy was used for it" % ", ".join(self.older)
            return said
        return ("the schema this plugin carries (version %s): no installation was reached, so what is built is not yet "
                "checked against the installation it is for" % index.get("version"))
