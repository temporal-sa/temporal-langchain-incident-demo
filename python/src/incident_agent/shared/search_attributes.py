"""Custom search attributes used for Advanced Visibility.

Register them once per namespace (the Makefile's dev server target does this for
you, or run `uv run incident-doctor --register-search-attributes`).
"""

from temporalio.common import SearchAttributeKey

INCIDENT_SERVICE = SearchAttributeKey.for_keyword("IncidentService")
INCIDENT_SEVERITY = SearchAttributeKey.for_keyword("IncidentSeverity")
DEMO_SCENARIO = SearchAttributeKey.for_keyword("DemoScenario")
AGENT_PHASE = SearchAttributeKey.for_keyword("AgentPhase")
APPROVAL_STATUS = SearchAttributeKey.for_keyword("ApprovalStatus")
REMEDIATION_ACTION = SearchAttributeKey.for_keyword("RemediationAction")
LLM_TOKENS = SearchAttributeKey.for_int("LlmTokens")

# name -> Temporal indexed value type (as used by `temporal server start-dev --search-attribute`)
DEFINITIONS: dict[str, str] = {
    "IncidentService": "Keyword",
    "IncidentSeverity": "Keyword",
    "DemoScenario": "Keyword",
    "AgentPhase": "Keyword",
    "ApprovalStatus": "Keyword",
    "RemediationAction": "Keyword",
    "LlmTokens": "Int",
}
