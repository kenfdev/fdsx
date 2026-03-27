"""Aggregation logic for parallel branch results in the compiler package."""

from typing import Any

from fdsx.models.flow import AggregateRule


def _aggregate(source_data: list[dict[str, Any]], rule: AggregateRule) -> str:
    """Aggregate parallel results using majority/all/any strategy.

    Args:
        source_data: List of branch result dictionaries (already resolved from state)
        rule: AggregateRule with field, strategy, match/no_match values

    Returns:
        The aggregated result (rule.match or rule.no_match)

    Security note: Uses total branch count (len(source_data)) as the denominator,
    so failed branches that did not produce the field count as no_match votes.
    This prevents bypassing a dissenting reviewer by knocking out their branch.
    """
    if not source_data:
        return rule.no_match

    # Use total branch count as denominator — failed/missing branches count as no_match
    total = len(source_data)

    match_count = 0
    for item in source_data:
        if isinstance(item, dict) and rule.field in item:
            if str(item[rule.field]) == str(rule.match):
                match_count += 1
        # Items without the field (failed branches) are treated as no_match

    if rule.strategy == "majority":
        return rule.match if match_count > total / 2 else rule.no_match
    elif rule.strategy == "all":
        return rule.match if match_count == total else rule.no_match
    elif rule.strategy == "any":
        return rule.match if match_count > 0 else rule.no_match
    else:
        raise ValueError(f"Unknown aggregation strategy: {rule.strategy}")
