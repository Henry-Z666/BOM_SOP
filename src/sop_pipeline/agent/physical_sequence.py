from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Protocol


PHYSICAL_SEQUENCE_POLICY = "interface-physical-precedence/v1"


class PhysicalStep(Protocol):
    step_id: str
    main_process_id: str
    title: str
    stage_scope_occurrence: str
    receiver_occurrences: tuple[str, ...]
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class PhysicalPrecedence:
    before_step_id: str
    after_step_id: str
    rule: str
    shared_receivers: tuple[str, ...] = ()


_SEAL = re.compile(
    r"密封|垫圈|垫片|o\s*[-形型]?\s*ring|gasket|seal",
    re.IGNORECASE,
)
_CLOSURE = re.compile(
    r"顶板|顶盖|端盖|盖板|盖子|封板|cover|lid|end\s*cap",
    re.IGNORECASE,
)
_RETAINER = re.compile(r"卡箍|抱箍|clamp|retainer", re.IGNORECASE)
_JOINT = re.compile(r"接头|connector|fitting", re.IGNORECASE)


def physical_role(title: str) -> str:
    value = str(title).strip()
    if _SEAL.search(value):
        return "seal"
    if _CLOSURE.search(value):
        return "closure"
    if _RETAINER.search(value) and not _JOINT.search(value):
        return "retainer"
    return "component"


def infer_physical_precedence(
    steps: Iterable[PhysicalStep],
) -> tuple[PhysicalPrecedence, ...]:
    """Infer only high-confidence, interface-local physical order.

    Creo proves which receiving occurrence an item uses.  Names provide a
    bounded semantic role, but never an occurrence, direction, or coordinate.
    A rule is emitted only for a shared native receiver or for seals and a
    closure inside the same main process and staging scope.
    """

    ordered = tuple(steps)
    roles = {step.step_id: physical_role(step.title) for step in ordered}
    by_receiver: dict[str, list[PhysicalStep]] = {}
    for step in ordered:
        for receiver in step.receiver_occurrences:
            by_receiver.setdefault(receiver, []).append(step)
    result: dict[tuple[str, str], PhysicalPrecedence] = {}
    direct_closure_pairs: list[tuple[PhysicalStep, PhysicalStep]] = []

    def add(before: PhysicalStep, after: PhysicalStep, rule: str, receivers=()) -> None:
        if before.step_id == after.step_id:
            return
        # Native receiver dependencies are stronger than semantic precedence.
        # Do not introduce a reverse edge when the seal itself needs the target
        # occurrence to exist first (for example a seal on the far side of a
        # valve that has already been installed).
        if after.step_id in before.depends_on:
            return
        key = (before.step_id, after.step_id)
        result.setdefault(
            key,
            PhysicalPrecedence(
                before.step_id,
                after.step_id,
                rule,
                tuple(sorted(set(receivers))),
            ),
        )

    for seal in (step for step in ordered if roles[step.step_id] == "seal"):
        for receiver in seal.receiver_occurrences:
            non_seals = [
                step
                for step in by_receiver.get(receiver, ())
                if step.step_id != seal.step_id and roles[step.step_id] != "seal"
            ]
            if len(non_seals) == 1:
                add(seal, non_seals[0], "seal_before_unique_interface_part", (receiver,))
                if roles[non_seals[0].step_id] == "closure":
                    direct_closure_pairs.append((seal, non_seals[0]))
            else:
                for target in non_seals:
                    if roles[target.step_id] in {"closure", "retainer"}:
                        add(seal, target, "seal_before_interface_closure", (receiver,))
                        if roles[target.step_id] == "closure":
                            direct_closure_pairs.append((seal, target))

    # A BOM seal family can be split into several receiver-layer render groups.
    # Once one group is proven to serve a closure's native interface, keep its
    # same-process siblings before that closure as well.  Do not pull unrelated
    # seals (for example a nearby sensor O-ring) in front of the closure.
    for direct_seal, closure in direct_closure_pairs:
        family = _role_family(direct_seal.title)
        for peer in ordered:
            if (
                roles[peer.step_id] == "seal"
                and peer.main_process_id == direct_seal.main_process_id
                and peer.stage_scope_occurrence
                == direct_seal.stage_scope_occurrence
                and _role_family(peer.title) == family
            ):
                add(peer, closure, "interface_seal_family_before_closure")

    for retainer in (step for step in ordered if roles[step.step_id] == "retainer"):
        for closure in (step for step in ordered if roles[step.step_id] == "closure"):
            shared = set(retainer.receiver_occurrences) & set(
                closure.receiver_occurrences
            )
            if shared:
                add(closure, retainer, "closure_before_interface_retainer", shared)

    return tuple(
        result[key]
        for key in sorted(
            result,
            key=lambda item: (item[1], item[0]),
        )
    )


def _role_family(title: str) -> str:
    value = re.sub(r"[（(]\s*\d+\s*/\s*\d+\s*[)）]", "", str(title))
    return re.sub(r"\s+", "", value).casefold()
