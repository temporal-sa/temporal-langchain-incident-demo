"""Workflow definitions. Deterministic code only: no LangChain, no I/O, no env reads."""

from incident_agent.workflows.incident import IncidentWorkflow
from incident_agent.workflows.triage import TriageWorkflow

ALL_WORKFLOWS = [IncidentWorkflow, TriageWorkflow]
