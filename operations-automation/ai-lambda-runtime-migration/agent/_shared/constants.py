"""
Shared constants for Lambda Runtime Migration agents.
Single source of truth for migration statuses, priority labels, and alert statuses.
"""


class MigrationStatus:
    """Migration workflow states — written to DynamoDB migration_status field."""
    DISCOVERED = "DISCOVERED"           # Phase 1: found by Trusted Advisor, enriched
    ANALYZING = "ANALYZING"             # Phase 2: code analysis in progress
    ASSESSED = "ASSESSED"               # Phase 2: analysis complete, migration report available
    TRANSFORMING = "TRANSFORMING"       # Phase 3: code transformation in progress
    READY_TO_MIGRATE = "READY_TO_MIGRATE"  # Phase 3: migrated code generated and validated
    TRANSFORM_FAILED = "TRANSFORM_FAILED"  # Phase 3: transformation failed
    SKIPPED = "SKIPPED"                 # Container image — cannot migrate via code
    RESOLVED = "RESOLVED"               # No longer in Trusted Advisor (migrated or deleted)

    # Statuses that allow Phase 3 (Transform) to proceed
    TRANSFORMABLE = {ASSESSED, READY_TO_MIGRATE, TRANSFORM_FAILED}


class PriorityLabel:
    """Priority labels derived from AI priority scores."""
    CRITICAL = "CRITICAL"   # 80-100
    HIGH = "HIGH"           # 60-79
    MEDIUM = "MEDIUM"       # 40-59
    LOW = "LOW"             # 20-39
    INACTIVE = "INACTIVE"   # 0-19

    @staticmethod
    def from_score(score: int) -> str:
        if score >= 80:
            return PriorityLabel.CRITICAL
        if score >= 60:
            return PriorityLabel.HIGH
        if score >= 40:
            return PriorityLabel.MEDIUM
        if score >= 20:
            return PriorityLabel.LOW
        return PriorityLabel.INACTIVE


class AlertStatus:
    """Trusted Advisor alert statuses."""
    RED = "Red"         # Runtime already past deprecation date
    YELLOW = "Yellow"   # Deprecation upcoming within 180 days
    GREEN = "Green"     # Resolved — no longer flagged by TA
