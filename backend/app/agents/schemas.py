from typing import Literal, Optional
from pydantic import BaseModel, Field

Cause = Literal["filter_blockage", "compressor_failure", "refrigerant_leak",
                "sensor_stuck", "after_hours_waste", "unknown"]
Priority = Literal["LOW", "MEDIUM", "HIGH"]
State = Literal["investigating", "awaiting_approval", "approved", "rejected", "resolved"]


class Diagnosis(BaseModel):
    cause: Cause
    cause_text: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(min_length=1)
    sources: list[str] = []


class EnergyImpact(BaseModel):
    extra_kwh_per_day: float
    cost_per_day: float
    currency: str = "INR"
    explanation: str


class Recommendation(BaseModel):
    priority: Priority
    action: str
    checklist: list[str]
    estimated_downtime_min: int
    assign_to: str = "HVAC technician team"


class Critique(BaseModel):
    """Energy agent reviewing the Diagnosis agent's answer."""
    agrees: bool
    issues: list[str] = []
    suggested_cause: Optional[Cause] = None


class Route(BaseModel):
    """Supervisor's LLM routing decision."""
    next: Literal["diagnosis", "energy", "maintenance", "wait_approval", "end"]
    reason: str


class IncidentState(BaseModel):
    incident_id: str
    event: dict
    state: State = "investigating"
    diagnosis: Optional[Diagnosis] = None
    energy: Optional[EnergyImpact] = None
    critique: Optional[Critique] = None
    recommendation: Optional[Recommendation] = None
    revisions: int = 0              # revision loop, max 2
    replans: int = 0
    operator_feedback: Optional[str] = None
    ticket_id: Optional[str] = None
    trace: list[dict] = []