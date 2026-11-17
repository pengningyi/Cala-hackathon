"""Run-scoped Cala observations, separate from generated chat text and saved scores."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from .models import Source


MAX_OBSERVATIONS = 32
MAX_EVIDENCE_BYTES = 60_000
MAX_OBSERVATION_BYTES = 8_000
REFERENCE_FIELDS = {"source_ids", "country_source_ids"}


@dataclass(frozen=True)
class ResearchEvidenceSnapshot:
    revision: int
    observations: list[dict]
    sources: dict[str, Source]
    updated_at: datetime | None
    truncated: bool = False


def source_references(value):
    """Read explicit source-reference fields without interpreting arbitrary prose."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in REFERENCE_FIELDS and isinstance(child, list):
                yield from (identifier for identifier in child if isinstance(identifier, str))
            else:
                yield from source_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from source_references(child)


def remap_references(value, identifiers):
    if isinstance(value, dict):
        return {key: [identifiers[identifier] for identifier in child]
                if key in REFERENCE_FIELDS and isinstance(child, list)
                else remap_references(child, identifiers)
                for key, child in value.items()}
    if isinstance(value, list):
        return [remap_references(child, identifiers) for child in value]
    return value


def _valid_reference_fields(value):
    if isinstance(value, dict):
        return all((isinstance(child, list) and all(isinstance(identifier, str) for identifier in child))
                   if key in REFERENCE_FIELDS else _valid_reference_fields(child)
                   for key, child in value.items())
    if isinstance(value, list):
        return all(_valid_reference_fields(child) for child in value)
    return True


def _source_key(source: Source):
    # A refetch's timestamp alone is not a new finding. Changed facts still are.
    return str(source.url), source.published_at, source.label


class ResearchEvidenceJournal:
    """Atomically merge server-recorded tool output and return isolated snapshots.

    This is not an HTTP input contract: only the tool executor supplies observations.
    One journal belongs to a RunRecord and shares its in-memory retention lifetime.
    No await occurs during record/snapshot, so concurrent requests cannot lose merges.
    """

    def __init__(self):
        self.revision = 0
        self.updated_at: datetime | None = None
        self._observations: list[dict] = []
        self._sources: dict[str, Source] = {}
        self._next_source = 1
        self._truncated = False

    def record(self, observations: list[dict], sources: dict[str, Source]) -> int:
        changed = False
        source_ids = {_source_key(value): key for key, value in self._sources.items()}
        known = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in self._observations}
        for observation in observations:
            if (not isinstance(observation, dict) or "result" not in observation or "error" in observation
                    or not isinstance(observation.get("tool"), str)):
                continue
            # Never retain the request-local source map or a model's research summary.
            packet = {key: deepcopy(observation[key]) for key in
                      ("tool", "arguments", "result", "truncated", "notice") if key in observation}
            if not _valid_reference_fields(packet):
                continue
            refs = list(dict.fromkeys(source_references(packet)))
            if not refs or any(identifier not in sources or not isinstance(sources[identifier], Source)
                               for identifier in refs):
                continue
            try:
                size = len(json.dumps(packet, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            except (TypeError, ValueError):
                continue
            if size > MAX_OBSERVATION_BYTES:
                continue
            remapping = {}
            for identifier in refs:
                source = sources[identifier]
                key = _source_key(source)
                if key not in source_ids:
                    stable = f"r{self._next_source}"
                    self._next_source += 1
                    source_ids[key] = stable
                    self._sources[stable] = source.model_copy(deep=True)
                remapping[identifier] = source_ids[key]
            packet = remap_references(packet, remapping)
            signature = json.dumps(packet, sort_keys=True, ensure_ascii=False)
            if signature in known:
                continue
            self._observations.append(packet)
            known.add(signature)
            changed = True

        if changed:
            while (len(self._observations) > MAX_OBSERVATIONS or self._size() > MAX_EVIDENCE_BYTES):
                self._observations.pop(0)
                self._prune_sources()
                self._truncated = True
            self.revision += 1
            self.updated_at = datetime.now(timezone.utc)
        self._prune_sources()
        return self.revision

    def _prune_sources(self):
        refs = set(source_references(self._observations))
        self._sources = {key: value for key, value in self._sources.items() if key in refs}

    def _size(self):
        return len(json.dumps({"observations": self._observations,
                               "sources": {key: value.model_dump(mode="json")
                                           for key, value in self._sources.items()}},
                              ensure_ascii=False).encode("utf-8"))

    def snapshot(self) -> ResearchEvidenceSnapshot:
        return ResearchEvidenceSnapshot(self.revision, deepcopy(self._observations),
                                        {key: value.model_copy(deep=True) for key, value in self._sources.items()},
                                        self.updated_at, self._truncated)

    def status(self) -> dict:
        return {"revision": self.revision, "observation_count": len(self._observations),
                "updated_at": self.updated_at, "truncated": self._truncated}
