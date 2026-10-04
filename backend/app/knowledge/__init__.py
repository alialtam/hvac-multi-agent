from app.llm import llm
from .search import search_knowledge
from .rules import diagnose_rules

llm.register_rules("diagnosis", diagnose_rules)  # fixes "no rules fallback registered"