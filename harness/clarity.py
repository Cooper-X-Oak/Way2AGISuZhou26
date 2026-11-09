"""Clarity transitions return new versions; callers must persist returned snapshots.

Pass the authoritative latest brief to require_approved when checking a cached
snapshot. No process-global registry or implicit persistence is introduced here.
"""

from contracts.harness import BriefApproval, ChannelEntryBrief, Contract, BriefStatus


class ClarityAssessment(Contract):
    state: BriefStatus
    missing_fields: tuple[str, ...]
    can_start: bool


def assess(brief: ChannelEntryBrief) -> ClarityAssessment:
    missing = []
    for key in ("decision_question", "product_profile_version", "delivery_profile"):
        if getattr(brief, key) is None:
            missing.append(key)
    for key in ("entry_modes", "stop_conditions"):
        if not getattr(brief, key):
            missing.append(key)
    for key in ("exclusions", "compliance_constraints", "assumptions_and_unknowns"):
        if getattr(brief, key) is None:
            missing.append(key)
    scope = brief.target_scope
    for key in ("city", "radius_km"):
        if getattr(scope, key) is None:
            missing.append(f"target_scope.{key}")
    for key in ("territories", "channel_types"):
        if not getattr(scope, key):
            missing.append(f"target_scope.{key}")
    for key in ("salespeople", "period_days", "daily_first_visits"):
        if getattr(brief.resources, key) is None:
            missing.append(f"resources.{key}")
    status = brief.status
    if status != "superseded" and missing:
        status = "needs_user_input"
    return ClarityAssessment(state=status, missing_fields=tuple(missing),
                             can_start=status == "approved" and not missing)


def request_input(brief: ChannelEntryBrief) -> ChannelEntryBrief:
    if brief.status not in ("draft", "needs_user_input"):
        raise ValueError("only an unapproved brief can request input")
    return ChannelEntryBrief.model_validate({**brief.model_dump(), "status": "needs_user_input"})


def approve(brief: ChannelEntryBrief, *, approved_by: str) -> ChannelEntryBrief:
    if brief.status != "needs_user_input":
        raise ValueError("approval requires needs_user_input state")
    gaps = assess(brief).missing_fields
    if gaps:
        raise ValueError(f"brief has missing fields: {', '.join(gaps)}")
    return ChannelEntryBrief.model_validate({
        **brief.model_dump(), "status": "approved",
        "approval": BriefApproval(approved_by=approved_by, version=brief.version),
    })


def require_approved(brief: ChannelEntryBrief, *, latest: ChannelEntryBrief | None = None) -> None:
    if latest is not None and latest != brief:
        raise ValueError("brief is not the authoritative latest approved snapshot")
    if not assess(brief).can_start:
        raise ValueError(f"brief is not approved and complete: {brief.status}")


def supersede(brief: ChannelEntryBrief, replacement: ChannelEntryBrief) -> tuple[ChannelEntryBrief, ChannelEntryBrief]:
    """Return retired old snapshot and replacement, for atomic storage by the caller."""
    require_approved(brief)
    if (replacement.project_id, replacement.brief_id) != (brief.project_id, brief.brief_id):
        raise ValueError("replacement must belong to the same project and brief")
    if replacement.version <= brief.version or replacement.supersedes != brief.version:
        raise ValueError("replacement must be a newer version explicitly superseding the old")
    if replacement.status == "superseded":
        raise ValueError("replacement is already superseded")
    retired = ChannelEntryBrief.model_validate({**brief.model_dump(), "status": "superseded"})
    return retired, replacement
