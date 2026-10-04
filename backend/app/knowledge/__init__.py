#This is a simple rule-based diagnosis engine for HVAC systems. It analyzes the signals from an event and applies a set of rules to determine the most likely cause of an issue, along with a confidence level and supporting evidence.
from app.llm import llm
from .search import search_knowledge
from .rules import diagnose_rules

llm.register_rules("diagnosis", diagnose_rules)  # fixes "no rules fallback registered"