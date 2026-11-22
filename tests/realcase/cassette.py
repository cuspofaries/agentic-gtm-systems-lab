"""Record real provider exchanges once, replay them for free.

A cassette wraps the transport of each connector (People Data Labs, FullEnrich,
ZeroBounce, HubSpot). In `record` mode it calls the real API and stores each exchange;
in `replay` mode it answers from the file and never touches the network. A request the
cassette has not seen fails loudly instead of silently reaching the network.

What is stored: provider, method, URL, request body (without API keys), response status,
response body and the few response headers the connectors read. Request headers are
never stored, since they carry the keys. Real exchanges contain real names and emails,
so a cassette belongs in a private directory, never in the repository.
"""

from __future__ import annotations

import json
import urllib.parse
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

KEPT_RESPONSE_HEADERS = ("x-call-credits-spent", "x-totallimit-remaining", "retry-after")
SECRET_FORM_FIELDS = ("api_key",)


def _plain(obj):
    return obj.model_dump(exclude_none=True) if hasattr(obj, "model_dump") else vars(obj)


def _response_to_dict(r) -> dict:
    blocks = []
    for b in r.content:
        if b.type == "tool_use":
            blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif b.type == "text":
            blocks.append({"type": "text", "text": b.text})
    usage = {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}
    return {"content": blocks, "stop_reason": r.stop_reason, "usage": usage}


def _dict_to_response(d: dict):
    return SimpleNamespace(content=[SimpleNamespace(**b) for b in d["content"]], stop_reason=d["stop_reason"],
                           usage=SimpleNamespace(**d["usage"]))


class CassetteMissError(AssertionError):
    """Replay met a request that was not recorded."""


def _clean_body(body: bytes | None) -> str | None:
    if body is None:
        return None
    text = body.decode()
    try:
        return json.dumps(json.loads(text), sort_keys=True)
    except ValueError:
        pass
    form = urllib.parse.parse_qsl(text, keep_blank_values=True)
    if form:
        kept = [(k, v) for k, v in form if k not in SECRET_FORM_FIELDS]
        return urllib.parse.urlencode(sorted(kept))
    return text


def _kept_headers(headers: dict | None) -> dict:
    low = {k.lower(): v for k, v in (headers or {}).items()}
    return {k: low[k] for k in KEPT_RESPONSE_HEADERS if k in low}


class Cassette:
    def __init__(self, path: Path, mode: str, *, secrets: tuple[str, ...] = ()):
        if mode not in ("record", "replay"):
            raise ValueError(f"unknown cassette mode {mode!r}")
        self.path, self.mode = Path(path), mode
        self.secrets = tuple(s for s in secrets if s)
        self.exchanges: list[dict] = []
        self.misses: list[str] = []
        self._queues: dict[str, list[dict]] = defaultdict(list)
        # When the exchanges were recorded. A replay runs "as of" this moment, so date
        # windows (signal freshness) give the same answer on any later day.
        self.recorded_at: datetime = datetime.now(UTC)
        if mode == "replay":
            data = json.loads(self.path.read_text())
            self.exchanges = data["exchanges"]
            if data.get("recorded_at"):
                self.recorded_at = datetime.fromisoformat(data["recorded_at"])
            for ex in self.exchanges:
                self._queues[self._key(ex["provider"], ex["method"], ex["url"], ex["body"])].append(ex)

    @staticmethod
    def _key(provider, method, url, body) -> str:
        return f"{provider} {method} {url} {body}"

    def _exchange(self, provider, method, url, body, real):
        clean = _clean_body(body)
        key = self._key(provider, method, url, clean)
        if self.mode == "replay":
            if not self._queues[key]:
                # Kept as well as raised: a caller may catch the error and fail closed.
                self.misses.append(f"{provider} {method} {url}")
                raise CassetteMissError(f"not recorded: {provider} {method} {url}")
            ex = self._queues[key].pop(0)
            return ex["status"], ex["response"], dict(ex["headers"])
        status, payload, headers = real()
        self.exchanges.append({"provider": provider, "method": method, "url": url, "body": clean,
                               "status": status, "response": payload,
                               "headers": _kept_headers(headers)})
        return status, payload, headers or {}

    # One adapter per transport signature used in the package.

    def pdl(self, real_transport):
        def transport(url, headers, body):
            return self._exchange("pdl", "POST", url, body, lambda: real_transport(url, headers, body))
        return transport

    def hubspot(self, real_transport):
        def transport(method, url, headers, body):
            return self._exchange("hubspot", method, url, body,
                                  lambda: real_transport(method, url, headers, body))
        return transport

    def email(self, provider, real_transport):
        def transport(method, url, headers, body):
            def real():
                status, payload = real_transport(method, url, headers, body)
                return status, payload, {}
            status, payload, _ = self._exchange(provider, method, url, body, real)
            return status, payload
        return transport

    def anthropic(self, real_client):
        """A stand-in for `anthropic.Anthropic()` that records `messages.create` calls.

        A request is keyed by its model, its tools and its last message (the prompt, or
        the tool results the loop just built); in a replay the same code path rebuilds
        the same last message. Only the parts of a response the loop reads are stored.
        """
        def create(**kw):
            last = kw["messages"][-1]["content"]
            body = json.dumps({"model": kw.get("model"), "tools": [t["name"] for t in kw.get("tools", [])],
                               "last": last}, sort_keys=True, default=_plain).encode()

            def real():
                r = real_client.messages.create(**kw)
                return 200, _response_to_dict(r), {}

            _status, payload, _ = self._exchange("anthropic", "POST", "messages.create", body, real)
            return _dict_to_response(payload)

        return SimpleNamespace(messages=SimpleNamespace(create=create))

    # ------------------------------------------------------------------------------

    def unused(self) -> list[str]:
        """Recorded exchanges a replay did not consume: the code changed what it asks."""
        return [k for k, q in self._queues.items() for _ in q]

    def save(self) -> None:
        text = json.dumps({"recorded_at": self.recorded_at.isoformat(), "exchanges": self.exchanges},
                          indent=1, sort_keys=True)
        leaked = [i for i, s in enumerate(self.secrets) if s in text]
        if leaked:
            raise AssertionError("an API key would be written to the cassette; nothing saved")
        self.path.write_text(text)

    def writes(self) -> list[str]:
        """CRM calls that would change data. Planning must not make any."""
        out = []
        for ex in self.exchanges:
            if ex["provider"] != "hubspot":
                continue
            if ex["method"] == "GET" or (ex["method"] == "POST" and ex["url"].endswith("/search")):
                continue
            out.append(f"{ex['method']} {ex['url']}")
        return out

    def credits(self) -> dict[str, int]:
        """Credits each provider reported spending, read from the recorded responses."""
        c = {"pdl_company": 0, "pdl_person": 0, "fullenrich": 0, "zerobounce": 0}
        for ex in self.exchanges:
            r = ex["response"] if isinstance(ex["response"], dict) else {}
            if ex["provider"] == "pdl":
                spent = int(ex["headers"].get("x-call-credits-spent") or 0)
                c["pdl_person" if "/person/" in ex["url"] else "pdl_company"] += spent
            elif ex["provider"] == "fullenrich" and ex["method"] == "GET" and r.get("status") == "FINISHED":
                c["fullenrich"] += int((r.get("cost") or {}).get("credits") or 0)
            elif ex["provider"] == "zerobounce" and "error" not in r and r.get("status") not in (None, "unknown"):
                c["zerobounce"] += 1
            elif ex["provider"] == "anthropic":
                u = r.get("usage") or {}
                c["anthropic_input_tokens"] = c.get("anthropic_input_tokens", 0) + int(u.get("input_tokens", 0))
                c["anthropic_output_tokens"] = c.get("anthropic_output_tokens", 0) + int(u.get("output_tokens", 0))
            elif ex["provider"] == "exa":
                c["exa_millidollars"] = c.get("exa_millidollars", 0) + round(
                    float((r.get("costDollars") or {}).get("total") or 0) * 1000)
        return c
