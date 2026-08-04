"""Pluggable publish sinks.

No broker has been chosen in code yet, so the publisher is transport-agnostic:
every message is wrapped in a small envelope ``{"topic", "message"}`` and handed
to a sink. Pick the one that matches how the downstream is being fed today.

    stdout / file : newline-delimited JSON (JSONL), one envelope per line.
                    Replay into any subscriber, or pipe straight into a test.
    http          : POST each envelope as JSON to a backend endpoint, so the
                    FastAPI ingestion side can receive it live.
"""

from __future__ import annotations

import json
import sys
import urllib.request


def make_envelope(topic, message):
    return {"topic": topic, "message": message}


class StdoutSink:
    def publish(self, topic, message):
        sys.stdout.write(json.dumps(make_envelope(topic, message)) + "\n")
        sys.stdout.flush()

    def close(self):
        pass


class FileSink:
    def __init__(self, path):
        self._fh = open(path, "w", encoding="utf-8")

    def publish(self, topic, message):
        self._fh.write(json.dumps(make_envelope(topic, message)) + "\n")
        self._fh.flush()

    def close(self):
        self._fh.close()


class HttpSink:
    """POST each envelope to ``url``. The endpoint can dispatch on ``topic``."""

    def __init__(self, url, timeout=5.0):
        self.url = url
        self.timeout = timeout

    def publish(self, topic, message):
        payload = json.dumps(make_envelope(topic, message)).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            resp.read()

    def close(self):
        pass


def build_sink(target):
    """Resolve a ``--out`` value to a sink.

    ``-``           -> stdout
    ``http(s)://…`` -> HTTP POST
    anything else   -> treated as a file path (JSONL)
    """
    if target in ("-", "", None):
        return StdoutSink()
    if target.startswith("http://") or target.startswith("https://"):
        return HttpSink(target)
    return FileSink(target)
