"""Enrichment plugin loaded at run time by lambda_function.load_plugin."""


def enrich(payload):
    enriched = dict(payload)
    enriched["source"] = "partner-webhook"
    return enriched
