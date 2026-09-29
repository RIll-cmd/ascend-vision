"""Trusted evidence-coverage checks for routine completion proposals."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit


@dataclass(frozen=True)
class VerificationResult:
    verdict: str
    reason_code: str
    observation_ids: tuple[str, ...]

    def __post_init__(self):
        if self.verdict not in {'passed', 'failed', 'inconclusive'}:
            raise ValueError('verification verdict is invalid')
        if not isinstance(self.reason_code, str) or not self.reason_code or len(self.reason_code) > 64:
            raise ValueError('verification reason code is invalid')
        if (not isinstance(self.observation_ids, tuple) or len(self.observation_ids) > 20
                or any(not isinstance(item, str) or not item or len(item) > 128 for item in self.observation_ids)):
            raise ValueError('verification observation IDs are invalid')


def _observation_map(task, observations):
    task_id = task.task_id
    current = {}
    for item in observations:
        observation_id = getattr(item, 'observation_id', None)
        if not isinstance(observation_id, str) or not observation_id or len(observation_id) > 128:
            continue
        observed_task = getattr(item, 'task_id', None)
        if task_id is not None and observed_task != task_id:
            continue
        current[observation_id] = item
    return current


def _expected_path(url: str, definition, input_name: str) -> bool:
    parsed = urlsplit(url)
    origin = f'{parsed.scheme.lower()}://{(parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()}'
    path = parsed.path or '/'
    return (origin in definition.allowed_origins
            and any(path.startswith(prefix) for prefix in definition.input_path_prefixes[input_name]))


def _finish_fields(prepared, observations, result):
    current = _observation_map(prepared, observations)
    cited = result.get('source_observation_ids') if isinstance(result, dict) else None
    sources = result.get('sources') if isinstance(result, dict) else None
    findings = result.get('findings') if isinstance(result, dict) else None
    if not isinstance(cited, list) or not isinstance(sources, list) or not isinstance(findings, list):
        return current, None, set(), False
    if any(not isinstance(item, str) or not item for item in cited):
        return current, None, set(), False
    if any(item not in current for item in cited):
        return current, tuple(cited[:20]), set(), False
    if any(not isinstance(item, str) for item in sources):
        return current, tuple(cited[:20]), set(), False
    finding_text = [item.get('text') for item in findings if isinstance(item, dict)]
    has_finding = bool(finding_text) and all(isinstance(text, str) and text.strip() for text in finding_text)
    return current, tuple(cited[:20]), set(sources), has_finding


def cited_document_v1(prepared, observations, result) -> VerificationResult:
    expected = prepared.input_values.get('url')
    if not isinstance(expected, str):
        return VerificationResult('failed', 'required_url_missing', ())
    current, cited, sources, has_finding = _finish_fields(prepared, observations, result)
    if cited is None:
        return VerificationResult('inconclusive', 'source_citations_missing', ())
    if any(item not in current for item in cited):
        return VerificationResult('failed', 'foreign_observation_reference', cited)
    if not has_finding:
        return VerificationResult('inconclusive', 'finding_missing', cited)
    matched = [item for item in current.values()
               if getattr(item, 'url', None) == expected and _expected_path(expected, prepared.definition, 'url')]
    if not matched:
        return VerificationResult('inconclusive', 'requested_page_not_observed', cited)
    observation = matched[-1]
    if not getattr(observation, 'visible_text', '').strip() or getattr(observation, 'truncated', True):
        return VerificationResult('inconclusive', 'requested_page_evidence_incomplete', (observation.observation_id,))
    if observation.observation_id not in cited or expected not in sources:
        return VerificationResult('failed', 'requested_page_not_cited', cited)
    return VerificationResult('passed', 'required_evidence_covered', (observation.observation_id,))


def two_documents_v1(prepared, observations, result) -> VerificationResult:
    names = ('url_a', 'url_b')
    expected = [prepared.input_values.get(name) for name in names]
    if any(not isinstance(value, str) for value in expected) or expected[0] == expected[1]:
        return VerificationResult('failed', 'required_distinct_urls_missing', ())
    current, cited, sources, has_finding = _finish_fields(prepared, observations, result)
    if cited is None:
        return VerificationResult('inconclusive', 'source_citations_missing', ())
    if any(item not in current for item in cited):
        return VerificationResult('failed', 'foreign_observation_reference', cited)
    if not has_finding:
        return VerificationResult('inconclusive', 'finding_missing', cited)
    required = []
    for name, url in zip(names, expected):
        if not _expected_path(url, prepared.definition, name):
            return VerificationResult('failed', 'requested_url_outside_manifest', cited)
        matches = [item for item in current.values() if getattr(item, 'url', None) == url]
        if not matches:
            return VerificationResult('inconclusive', 'requested_page_not_observed', cited)
        observation = matches[-1]
        if not getattr(observation, 'visible_text', '').strip() or getattr(observation, 'truncated', True):
            return VerificationResult('inconclusive', 'requested_page_evidence_incomplete', (observation.observation_id,))
        required.append(observation)
    ids = tuple(item.observation_id for item in required)
    if len(set(ids)) != 2 or any(item not in cited for item in ids) or any(url not in sources for url in expected):
        return VerificationResult('failed', 'both_requested_pages_not_cited', tuple(cited))
    return VerificationResult('passed', 'both_requested_pages_covered', ids)


VERIFIERS = MappingProxyType({
    'cited_document_v1': cited_document_v1,
    'two_documents_v1': two_documents_v1,
})


def verify_completion(verifier_id: str, prepared, observations: tuple, result: dict) -> VerificationResult:
    verifier = VERIFIERS.get(verifier_id)
    if verifier is None:
        return VerificationResult('failed', 'verifier_not_registered', ())
    return verifier(prepared, observations, result)
