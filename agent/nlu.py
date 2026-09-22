"""Single NLU entry point used by the orchestrator.

Runs NER and intent classification in one shot and returns a compact
result object. Nothing here mutates TripState — that is the caller's job.
"""
from dataclasses import dataclass, field
from typing import Optional

from agent import intent as intent_mod
from agent import ner as ner_mod


@dataclass
class NLUResult:
    text: str
    intent: str
    confidence: float
    source: str              # "rule" | "classifier"
    slots: dict = field(default_factory=dict)

    @property
    def high_confidence(self) -> bool:
        # Rules are trusted. Classifier must clear a strict bar.
        if self.source == "rule":
            return True
        return self.confidence >= 0.70

    @property
    def has_slots(self) -> bool:
        return any(
            k in self.slots for k in
            ("origin", "destination_raw", "start_date", "end_date",
             "budget_total", "travellers", "travel_mode", "adventure_level")
        )


def understand(text: str, state=None) -> NLUResult:
    known_origin = getattr(state, "origin", None) if state else None
    slots = ner_mod.extract_entities(text, known_origin=known_origin)
    intent, conf, source = intent_mod.classify(text, state=state)
    return NLUResult(
        text=text, intent=intent, confidence=conf,
        source=source, slots=slots,
    )