"""Source-backed process semantics, independent of CAD and workbook layout."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from graphlib import TopologicalSorter
import hashlib
import json
import re


def fingerprint(value: object) -> str:
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False,
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class SourceEvidence:
    file: str
    sha256: str
    sheet: str
    location: str
    text: str

    def __post_init__(self) -> None:
        if not self.file or not self.location or not self.text.strip():
            raise ValueError('source evidence requires file, location and text')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', self.sha256):
            raise ValueError('source evidence requires SHA-256')


@dataclass(frozen=True)
class TorqueClaim:
    claim_id: str
    value: str
    unit: str
    qualifier: str
    tolerance: str
    evidence: SourceEvidence

    def __post_init__(self) -> None:
        number = Decimal(self.value)
        if not number.is_finite() or number <= 0:
            raise ValueError('torque must be finite and positive')
        if self.unit not in {'N.m', 'kgf.cm'}:
            raise ValueError('unsupported torque unit')

    @property
    def text(self) -> str:
        return f'{self.value}{self.tolerance} {self.unit}'


@dataclass(frozen=True)
class ProcessOperation:
    operation_id: str
    title: str
    scope: str
    materials: tuple[str, ...]
    instructions: tuple[SourceEvidence, ...]
    torque_claims: tuple[TorqueClaim, ...] = ()
    depends_on: tuple[str, ...] = ()
    applicability: str = 'pending'

    def __post_init__(self) -> None:
        if not self.operation_id or not self.title.strip() or not self.instructions:
            raise ValueError('process operation requires identity, title and source evidence')
        if len({claim.claim_id for claim in self.torque_claims}) != len(self.torque_claims):
            raise ValueError('duplicate torque claim identity')


@dataclass(frozen=True)
class TorqueDecision:
    """An explicit, durable selection for one connection, not a diameter rule."""
    operation_id: str
    connection_id: str
    claim_id: str
    occurrence_ids: tuple[str, ...]
    confirmed_by: str
    reason: str
    acknowledged_claim_ids: tuple[str, ...]


def resolve_torque(operation: ProcessOperation, decision: TorqueDecision | None) -> TorqueClaim | None:
    if decision is None:
        return None
    claims = {claim.claim_id: claim for claim in operation.torque_claims}
    if (decision.operation_id != operation.operation_id or not decision.connection_id
            or any(ord(character) < 32 for character in decision.connection_id)
            or not decision.confirmed_by.strip() or not decision.reason.strip()
            or not decision.occurrence_ids or len(set(decision.occurrence_ids)) != len(decision.occurrence_ids)):
        raise ValueError('incomplete torque confirmation')
    if set(decision.acknowledged_claim_ids) != set(claims):
        raise ValueError('all competing torque claims must be acknowledged')
    if decision.claim_id not in claims:
        raise ValueError('selected torque claim is not source-backed')
    return claims[decision.claim_id]


def order_operations(operations: tuple[ProcessOperation, ...]) -> tuple[ProcessOperation, ...]:
    by_id = {operation.operation_id: operation for operation in operations}
    if len(by_id) != len(operations):
        raise ValueError('duplicate operation identity')
    for operation in operations:
        if set(operation.depends_on) - set(by_id):
            raise ValueError('unknown process dependency')
    return tuple(by_id[key] for key in TopologicalSorter(
        {operation.operation_id: operation.depends_on for operation in operations}).static_order())


def operation_from_dict(value: dict) -> ProcessOperation:
    return ProcessOperation(operation_id=value['operation_id'], title=value['title'],
        scope=value['scope'], materials=tuple(value['materials']),
        instructions=tuple(SourceEvidence(**item) for item in value['instructions']),
        torque_claims=tuple(TorqueClaim(**{**item, 'evidence': SourceEvidence(**item['evidence'])})
            for item in value.get('torque_claims', [])),
        depends_on=tuple(value.get('depends_on', [])), applicability=value.get('applicability', 'pending'))


def operation_payload(operations: tuple[ProcessOperation, ...]) -> dict:
    value = {'schema_version': 'process-operations/v1', 'operations': [asdict(item) for item in operations]}
    return {**value, 'fingerprint': fingerprint(value)}
