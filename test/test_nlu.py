from agent.state import TripState, DestinationCandidate
from agent import nlu

state = TripState()
state.destination_candidates = [
    DestinationCandidate(name="Ooty",       lat=11.41, lng=76.70),
    DestinationCandidate(name="Kodaikanal", lat=10.24, lng=77.49),
    DestinationCandidate(name="Munnar",     lat=10.09, lng=77.06),
]

for s in ["2", "Ooty", "the second one", "option 3", "hi", "drop the museum"]:
    r = nlu.understand(s, state)
    print(f"{s!r:20} -> {r.intent:22} {r.confidence:.2f}  [{r.source}]")