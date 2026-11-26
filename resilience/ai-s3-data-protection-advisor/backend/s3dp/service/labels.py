"""The seven scan steps as the dashboard shows them, and the map from durable step names to labels."""
STEPS = (
    "Determine the accounts to scan",
    "Find the regions in use",
    "Collect bucket settings",
    "Classify bucket roles",
    "Score",
    "Write the narrative",
    "Publish",
)
# Durable step name (or its prefix before "-") -> index in STEPS. "collect-<n>" is one unit of step 3.
_INDEX = {"accounts": 0, "regions": 1, "collect": 2, "assemble": 2, "classify": 3, "score": 4, "narrative": 5,
          "publish": 6}


def label_for(step_name):
    """Label of a durable step name, None for a name that is not one of the seven steps."""
    i = _INDEX.get(step_name.split("-", 1)[0])
    return None if i is None else STEPS[i]
