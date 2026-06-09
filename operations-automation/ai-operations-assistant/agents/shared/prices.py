"""
Shared price table for the G.O.A.T. Network Agent capture cost estimates.

This module is the single source of truth for the unit prices and the
``estimated_bytes`` heuristic used by:

- The Orchestration Agent's ``Capture_Confirmation_Prompt`` (which
  shows the user a cost estimate before invoking ``start_capture``),
- The README ``Monthly Cost Estimate`` cost table for the Network
  Agent (per Requirement 14.2).

By centralising both the unit prices and the formula in one module
(plus its companion ``prices.json`` for non-Python runtimes), the chat
confirmation cost and the documented cost cannot drift. When AWS
pricing changes, updating the constants in this file (and regenerating
``prices.json``) updates both surfaces simultaneously.

Sources for the published rates:

- VPC Traffic Mirroring per-ENI-hour rate and the ``$0.015/GB`` data
  charge: https://aws.amazon.com/vpc/pricing/ (Traffic Mirroring tab).
  The per-ENI-hour rate is regional; the table below seeds the
  commonly used commercial regions and falls back to the
  ``us-east-1`` rate when an unknown region is requested.
- S3 Standard storage rate (first 50 TB/month, ``us-east-1``):
  https://aws.amazon.com/s3/pricing/.

References:

- design.md, ``Capture_Confirmation_Prompt structure`` section.
- requirements.md, Requirements 14.2 and 17.2.
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Traffic Mirror pricing
# ---------------------------------------------------------------------------

#: Default per-ENI-hour rate in USD for VPC Traffic Mirroring, used when
#: the requested region is not present in
#: :data:`TRAFFIC_MIRROR_ENI_HOUR_PRICE_BY_REGION`.
TRAFFIC_MIRROR_ENI_HOUR_PRICE_USD: float = 0.015

#: Per-region per-ENI-hour rate for VPC Traffic Mirroring in USD.
#:
#: Seeded with the published rates for the commercial regions most
#: commonly used in the GenAI Ops demo library. AWS publishes the same
#: rate (``$0.015``/ENI-hour) across the listed commercial regions at
#: the time of writing; the table is structured as a region → price
#: mapping anyway so future regional differences can be reflected
#: without changing call sites.
TRAFFIC_MIRROR_ENI_HOUR_PRICE_BY_REGION: dict = {
    "us-east-1": 0.015,
    "us-east-2": 0.015,
    "us-west-1": 0.015,
    "us-west-2": 0.015,
    "eu-west-1": 0.015,
    "eu-west-2": 0.015,
    "eu-west-3": 0.015,
    "eu-central-1": 0.015,
    "eu-north-1": 0.015,
    "ap-northeast-1": 0.015,
    "ap-northeast-2": 0.015,
    "ap-southeast-1": 0.015,
    "ap-southeast-2": 0.015,
    "ap-south-1": 0.015,
    "ca-central-1": 0.015,
    "sa-east-1": 0.015,
}

#: Per-GB data charge in USD for traffic processed through a VPC
#: Traffic Mirror session.
TRAFFIC_MIRROR_DATA_PRICE_PER_GB_USD: float = 0.015


# ---------------------------------------------------------------------------
# S3 storage pricing (used by the README cost-estimate table)
# ---------------------------------------------------------------------------

#: S3 Standard storage rate per GB-month in USD (``us-east-1``, first
#: 50 TB tier). Used by the README ``Monthly Cost Estimate`` to value
#: the storage line item (Req 14.2). The Traffic Mirror data charge is
#: independent of S3 storage, so the cost-estimate prompt does not use
#: this constant directly; it appears in this module so the README
#: cost-estimate table can pull every unit price from a single source.
S3_STANDARD_STORAGE_PRICE_PER_GB_MONTH_USD: float = 0.023


# ---------------------------------------------------------------------------
# Traffic-volume heuristic
# ---------------------------------------------------------------------------

#: Average mirrored throughput per ENI assumed by the cost estimate.
#: Chosen to match the README cost-estimate table (Req 14.2).
MBPS_PER_ENI_HEURISTIC: float = 1.0

#: Number of bytes per second corresponding to 1 megabit per second
#: (``1_000_000 bits / 8 bits per byte``). Used to convert the
#: ``MBPS_PER_ENI_HEURISTIC`` into a bytes-per-second figure.
BYTES_PER_SECOND_PER_MBPS: int = 125_000


def estimate_bytes(eni_count: int, duration_minutes: int) -> int:
    """Return the default ``estimated_bytes`` for a capture.

    Implements the heuristic documented in the design:

    .. code-block:: text

        estimated_bytes = eni_count * duration_minutes * 60 * 125000

    which is equivalent to assuming 1 Mbps of mirrored throughput per
    ENI (``MBPS_PER_ENI_HEURISTIC``).

    Args:
        eni_count: The number of ENIs being mirrored.
        duration_minutes: The capture duration in minutes.

    Returns:
        The estimated total mirrored byte count.
    """
    return (
        int(eni_count)
        * int(duration_minutes)
        * 60
        * BYTES_PER_SECOND_PER_MBPS
    )


def get_traffic_mirror_eni_hour_price(region: Optional[str] = None) -> float:
    """Return the regional per-ENI-hour rate for VPC Traffic Mirroring.

    Args:
        region: AWS region name (for example ``"eu-west-1"``). When
            ``None`` or unknown, the default rate
            :data:`TRAFFIC_MIRROR_ENI_HOUR_PRICE_USD` is returned.

    Returns:
        The per-ENI-hour rate in USD.
    """
    if region is None:
        return TRAFFIC_MIRROR_ENI_HOUR_PRICE_USD
    return TRAFFIC_MIRROR_ENI_HOUR_PRICE_BY_REGION.get(
        region, TRAFFIC_MIRROR_ENI_HOUR_PRICE_USD
    )


def compute_capture_cost_usd(
    eni_count: int,
    duration_minutes: int,
    region: Optional[str] = None,
    estimated_bytes: Optional[int] = None,
) -> float:
    """Compute the estimated cost in USD of a capture.

    Implements the formula documented in the design's
    ``Capture_Confirmation_Prompt`` section:

    .. code-block:: text

        cost_usd = (eni_count * duration_hours * price_per_eni_hour)
                 + (estimated_bytes / 1e9 * price_per_gb)

    where ``duration_hours = duration_minutes / 60`` and
    ``estimated_bytes`` defaults to the heuristic returned by
    :func:`estimate_bytes` when not supplied.

    Args:
        eni_count: The number of ENIs in ``eni_ids``. Must be a
            non-negative integer.
        duration_minutes: The capture duration in minutes. Must be a
            non-negative integer.
        region: Optional AWS region name. Determines which regional
            per-ENI-hour rate is read from
            :data:`TRAFFIC_MIRROR_ENI_HOUR_PRICE_BY_REGION`. When
            ``None`` or unknown, the default rate is used.
        estimated_bytes: Optional override for the assumed total
            mirrored byte count. When ``None``, the heuristic
            ``eni_count * duration_minutes * 60 * 125000`` is applied.

    Returns:
        The estimated cost in USD as a ``float``.

    Raises:
        ValueError: If ``eni_count`` or ``duration_minutes`` is
            negative, or ``estimated_bytes`` is negative.
    """
    if eni_count < 0:
        raise ValueError("eni_count must be non-negative")
    if duration_minutes < 0:
        raise ValueError("duration_minutes must be non-negative")
    if estimated_bytes is not None and estimated_bytes < 0:
        raise ValueError("estimated_bytes must be non-negative")

    duration_hours = duration_minutes / 60.0
    price_per_eni_hour = get_traffic_mirror_eni_hour_price(region)
    price_per_gb = TRAFFIC_MIRROR_DATA_PRICE_PER_GB_USD

    if estimated_bytes is None:
        estimated_bytes = estimate_bytes(eni_count, duration_minutes)

    eni_hours_cost = eni_count * duration_hours * price_per_eni_hour
    data_cost = (estimated_bytes / 1e9) * price_per_gb

    return eni_hours_cost + data_cost
