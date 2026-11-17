from __future__ import annotations

from collections.abc import Mapping

from verityflow.config import Config
from verityflow.mapping.models import (
    Extraction,
    MappingMatch,
    MappingResult,
    MatchMethod,
    SemanticElement,
    SignatureOutcome,
)
from verityflow.mapping.semantics import equivalent_conditions
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding


def map_to_elements(
    requirements: tuple[SemanticElement, ...],
    extraction: Extraction,
    dictionary: DataDictionary,
    *,
    artifact: str,
    config: Config,
    hints: Mapping[str, tuple[str, ...]] | None = None,
    artifact_ref: ArtifactRef | None = None,
    trace_ref: ArtifactRef | None = None,
) -> MappingResult:
    """Match requirements to elements using semantics only; IDs never decide a match."""
    findings: list[Finding] = list(extraction.findings)
    elements = extraction.elements
    by_id = {element.element_id: element for element in elements}
    requirement_refs = {element.element_id for element in requirements}
    hint_map = dict(hints or {})

    def hint_finding(
        category: FindingCategory,
        message: str,
        element: str | None = None,
    ) -> None:
        findings.append(
            make_finding(
                category,
                Severity.COVERAGE_GAP,
                message,
                rule_ref=element,
                artifact_id=trace_ref.artifact_id if trace_ref is not None else None,
                artifact_version=trace_ref.version if trace_ref is not None else None,
            )
        )

    for requirement_ref, targets in sorted(hint_map.items()):
        if requirement_ref not in requirement_refs:
            hint_finding(
                FindingCategory.MISSING_EVIDENCE,
                f"trace map references unknown requirement {requirement_ref!r}",
            )
        for target in targets:
            if target not in by_id:
                hint_finding(
                    FindingCategory.MISSING_EVIDENCE,
                    f"trace map references unknown {artifact} element {target!r}",
                    requirement_ref,
                )

    matches: list[MappingMatch] = []
    unmatched: list[str] = []
    for requirement in requirements:
        candidates: list[tuple[SemanticElement, SignatureOutcome]] = []
        for element in elements:
            if _canonical_destination(element.destination, dictionary) != (
                _canonical_destination(requirement.destination, dictionary)
            ):
                continue
            outcome = equivalent_conditions(
                requirement.condition,
                element.condition,
                dictionary,
                max_cells=config.limits.mapping_max_signature_cells,
            )
            if outcome.equal is None:
                findings.append(
                    make_finding(
                        FindingCategory.LIMIT_EXCEEDED,
                        Severity.COVERAGE_GAP,
                        f"cannot establish correspondence between requirement "
                        f"{requirement.element_id!r} and {artifact} element "
                        f"{element.element_id!r}: {outcome.reason}",
                        rule_ref=requirement.element_id,
                        artifact_id=artifact_ref.artifact_id if artifact_ref is not None else None,
                        artifact_version=artifact_ref.version if artifact_ref is not None else None,
                    )
                )
                continue
            if outcome.equal:
                candidates.append((element, outcome))

        chosen: SemanticElement | None = None
        method = MatchMethod.INFERRED
        states = 0
        hinted = hint_map.get(requirement.element_id, ())
        fall_back = False
        if hinted:
            equivalent_hinted = [
                (element, outcome)
                for element, outcome in candidates
                if element.element_id in hinted
            ]
            for target in hinted:
                if target not in by_id:
                    continue
                if not any(element.element_id == target for element, _ in candidates):
                    hint_finding(
                        FindingCategory.INVALID_TRACE_HINT,
                        f"trace hint maps requirement {requirement.element_id!r} to "
                        f"{target!r}, but their conditions or destinations are not "
                        "semantically equivalent; the hint is ignored",
                        requirement.element_id,
                    )
            if len(equivalent_hinted) == 1:
                chosen, outcome = equivalent_hinted[0]
                method = MatchMethod.HINT
                states = outcome.states
            elif len(equivalent_hinted) > 1:
                chosen, method, states = _by_precedence(requirement, equivalent_hinted)
                if chosen is None:
                    findings.append(
                        _ambiguous(requirement, equivalent_hinted, artifact, artifact_ref)
                    )
            else:
                fall_back = True
        else:
            fall_back = True

        if chosen is None and fall_back:
            if len(candidates) == 1:
                chosen, outcome = candidates[0]
                states = outcome.states
            elif len(candidates) > 1:
                chosen, method, states = _by_precedence(requirement, candidates)
                if chosen is None:
                    findings.append(_ambiguous(requirement, candidates, artifact, artifact_ref))

        if chosen is None:
            unmatched.append(requirement.element_id)
            continue
        matches.append(
            MappingMatch(
                requirement_ref=requirement.element_id,
                element_id=chosen.element_id,
                element_kind=chosen.kind,
                method=method,
                states=states,
            )
        )

    matched_ids = {match.element_id for match in matches}
    unmatched_elements = tuple(
        element.element_id for element in elements if element.element_id not in matched_ids
    )
    for requirement_ref in unmatched:
        findings.append(
            make_finding(
                FindingCategory.UNTRACEABLE_REQUIREMENT,
                Severity.COVERAGE_GAP,
                f"requirement {requirement_ref!r} has no semantically equivalent "
                f"{artifact} element",
                rule_ref=requirement_ref,
                artifact_id=artifact_ref.artifact_id if artifact_ref is not None else None,
                artifact_version=artifact_ref.version if artifact_ref is not None else None,
            )
        )
    for element_id in unmatched_elements:
        findings.append(
            make_finding(
                FindingCategory.UNTRACEABLE_ELEMENT,
                Severity.COVERAGE_GAP,
                f"{artifact} element {element_id!r} has no semantically equivalent requirement",
                rule_ref=element_id,
                artifact_id=artifact_ref.artifact_id if artifact_ref is not None else None,
                artifact_version=artifact_ref.version if artifact_ref is not None else None,
            )
        )
    complete = (
        extraction.sound
        and not unmatched
        and not unmatched_elements
        and not any(
            finding.severity in {Severity.BLOCKER, Severity.COVERAGE_GAP} for finding in findings
        )
    )
    return MappingResult(
        artifact=artifact,
        matches=tuple(matches),
        unmatched_requirements=tuple(unmatched),
        unmatched_elements=unmatched_elements,
        findings=_dedupe(findings),
        complete=complete,
    )


def _by_precedence(
    requirement: SemanticElement,
    candidates: list[tuple[SemanticElement, SignatureOutcome]],
) -> tuple[SemanticElement | None, MatchMethod, int]:
    ranked = [
        (element, outcome)
        for element, outcome in candidates
        if element.precedence == requirement.precedence
    ]
    if len(ranked) == 1:
        element, outcome = ranked[0]
        return element, MatchMethod.PRECEDENCE, outcome.states
    return None, MatchMethod.PRECEDENCE, 0


def _ambiguous(
    requirement: SemanticElement,
    candidates: list[tuple[SemanticElement, SignatureOutcome]],
    artifact: str,
    artifact_ref: ArtifactRef | None,
) -> Finding:
    names = ", ".join(sorted(element.element_id for element, _ in candidates))
    return make_finding(
        FindingCategory.AMBIGUOUS_MAPPING,
        Severity.COVERAGE_GAP,
        f"requirement {requirement.element_id!r} corresponds equally to several "
        f"{artifact} elements ({names}); semantics and precedence do not disambiguate "
        "and no valid hint narrows the choice, so the correspondence is unreviewed",
        rule_ref=requirement.element_id,
        artifact_id=artifact_ref.artifact_id if artifact_ref is not None else None,
        artifact_version=artifact_ref.version if artifact_ref is not None else None,
    )


def _canonical_destination(destination: str, dictionary: DataDictionary) -> str:
    resolved = dictionary.destination(destination)
    return resolved if resolved is not None else destination


def _dedupe(findings: list[Finding]) -> tuple[Finding, ...]:
    seen: set[str] = set()
    unique: list[Finding] = []
    for finding in findings:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        unique.append(finding)
    return tuple(unique)


__all__ = ["map_to_elements"]
