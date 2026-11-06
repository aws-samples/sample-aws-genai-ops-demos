"""Estimate fine-tuning cost before and after safe fixes.

Local compute only (no AWS). Uses static pricing tables (SageMaker instance $/hr and
throughput, Bedrock-managed $/1k tokens). ``savings_percent`` is the share of cost removed
by dropping duplicate and empty records.
"""

from __future__ import annotations

from typing import Any

from config import DEFAULT_MODEL_ID

# Static SageMaker on-demand training pricing (USD/hour) and rough token throughput
# (tokens/second) per instance type. Demo-grade estimates, not a billing source.
SAGEMAKER_INSTANCE_PRICING = {
    "ml.p4d.24xlarge": {"usd_per_hour": 37.688, "tokens_per_sec": 20000.0},
    "ml.p5.48xlarge": {"usd_per_hour": 98.32, "tokens_per_sec": 45000.0},
    "ml.g5.12xlarge": {"usd_per_hour": 7.09, "tokens_per_sec": 4000.0},
    "ml.g5.48xlarge": {"usd_per_hour": 20.36, "tokens_per_sec": 12000.0},
    "default": {"usd_per_hour": 37.688, "tokens_per_sec": 20000.0},
}

# Static Bedrock-managed customization pricing (USD per 1k training tokens).
BEDROCK_MANAGED_PRICING = {
    "anthropic.claude-sonnet-4-5-20250929-v1:0": 0.0,  # not offered as managed customization
    "amazon.nova-pro-v1:0": 0.008,
    "meta.llama3-70b-instruct-v1:0": 0.0105,
    "meta.llama3-8b-instruct-v1:0": 0.004,
    "default": 0.008,
}


def _instance_pricing(instance_type: str) -> dict[str, float]:
    return SAGEMAKER_INSTANCE_PRICING.get(
        instance_type, SAGEMAKER_INSTANCE_PRICING["default"]
    )


def _managed_price_per_1k(target_model: str) -> float:
    return BEDROCK_MANAGED_PRICING.get(
        target_model, BEDROCK_MANAGED_PRICING["default"]
    )


def _sagemaker_cost(
    total_tokens: int,
    instance_type: str,
    num_nodes: int,
    epochs: int,
) -> float:
    pricing = _instance_pricing(instance_type)
    per_sec = pricing["tokens_per_sec"] * max(num_nodes, 1)
    processed_tokens = total_tokens * max(epochs, 1)
    hours = (processed_tokens / per_sec) / 3600.0 if per_sec else 0.0
    return hours * pricing["usd_per_hour"] * max(num_nodes, 1)


def _bedrock_cost(total_tokens: int, target_model: str, epochs: int) -> float:
    price = _managed_price_per_1k(target_model)
    return (total_tokens * max(epochs, 1) / 1000.0) * price


def estimate_training_cost(
    total_tokens: int,
    target_model: str = DEFAULT_MODEL_ID,
    instance_type: str = "ml.p4d.24xlarge",
    num_nodes: int = 4,
    epochs: int = 3,
    duplicate_rate: float = 0.0,
    empty_record_rate: float = 0.0,
    use_bedrock_managed: bool = False,
) -> dict[str, Any]:
    """Estimate cost before and after safe fixes.

    Returns cost_before_fixes, cost_after_fixes, savings_usd, savings_percent, plus the
    assumptions used.
    """
    total_tokens = max(int(total_tokens), 0)
    wasteful_rate = min(1.0, max(0.0, duplicate_rate) + max(0.0, empty_record_rate))
    effective_tokens = int(round(total_tokens * (1.0 - wasteful_rate)))

    if use_bedrock_managed:
        cost_before = _bedrock_cost(total_tokens, target_model, epochs)
        cost_after = _bedrock_cost(effective_tokens, target_model, epochs)
        method = "bedrock-managed"
    else:
        cost_before = _sagemaker_cost(total_tokens, instance_type, num_nodes, epochs)
        cost_after = _sagemaker_cost(effective_tokens, instance_type, num_nodes, epochs)
        method = "sagemaker"

    savings_usd = max(cost_before - cost_after, 0.0)
    savings_percent = (savings_usd / cost_before * 100.0) if cost_before > 0 else 0.0

    return {
        "method": method,
        "cost_before_fixes": round(cost_before, 2),
        "cost_after_fixes": round(cost_after, 2),
        "savings_usd": round(savings_usd, 2),
        "savings_percent": round(savings_percent, 2),
        "assumptions": {
            "total_tokens": total_tokens,
            "effective_tokens": effective_tokens,
            "target_model": target_model,
            "instance_type": instance_type,
            "num_nodes": num_nodes,
            "epochs": epochs,
            "duplicate_rate": duplicate_rate,
            "empty_record_rate": empty_record_rate,
            "use_bedrock_managed": use_bedrock_managed,
        },
    }
