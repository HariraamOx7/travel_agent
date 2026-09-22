"""Intent classification for routing user messages.

Two-layer design:
  1. Rule layer (agent.intent_rules) — high-precision regexes that fire on
     unambiguous phrasings ("2", "hi", "plan the days"). A rule match is
     trusted and can safely short-circuit the LLM.
  2. ML layer — TF-IDF (word + char n-grams) + Logistic Regression, trained
     on a small labelled dataset. Handles fuzzier phrasings the rules miss.

`classify()` returns (intent, confidence, source) where source is "rule" or
"classifier". Callers should only short-circuit on source == "rule", or on
source == "classifier" with confidence above a strict threshold (see
NLUResult.high_confidence in agent.nlu).

The ML model auto-trains on first use and caches to disk. Delete
cache/intent_clf.joblib to force a retrain after editing TRAINING_DATA.
"""
import os
import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, FeatureUnion

from agent import intent_rules

MODEL_PATH = "cache/intent_clf.joblib"
MIN_CONFIDENCE = 0.55

INTENTS = [
    "provide_slot",
    "request_recommendations",
    "build_itinerary",
    "change_pace",
    "remove_stop",
    "ask_budget",
    "confirm_destination",
    "ask_transport",
    "ask_weather",
    "greet",
    "other",
]

# ~70 labelled examples. Add more as you observe real traffic.
TRAINING_DATA = [
    # ------------------------------------------------------------------ #
    # provide_slot — user is supplying trip information
    # ------------------------------------------------------------------ #
    ("I'm from Chennai", "provide_slot"),
    ("we are 4 people", "provide_slot"),
    ("my budget is 40000", "provide_slot"),
    ("15/9/26 to 20/9/26", "provide_slot"),
    ("we'll go by car", "provide_slot"),
    ("I want to go to Ooty", "provide_slot"),
    ("travelling with 2 adults", "provide_slot"),
    ("from Bangalore to Munnar", "provide_slot"),
    ("budget around 40k INR", "provide_slot"),
    ("5 days starting next Monday", "provide_slot"),
    ("it's just me", "provide_slot"),
    ("trip for two", "provide_slot"),
    ("I'm travelling solo", "provide_slot"),
    ("we are a group of 6", "provide_slot"),
    ("starting from Mumbai on 1st October", "provide_slot"),
    ("our budget is 80 thousand rupees", "provide_slot"),
    ("we'll drive down", "provide_slot"),
    ("planning to fly there", "provide_slot"),
    ("just my wife and me", "provide_slot"),
    ("destination is Coorg", "provide_slot"),
    ("we leave on the 10th and return on the 15th", "provide_slot"),
    ("we're three people", "provide_slot"),

    # ------------------------------------------------------------------ #
    # request_recommendations — user wants POI / stay / food suggestions
    # ------------------------------------------------------------------ #
    ("suggest some places", "request_recommendations"),
    ("what can I see there", "request_recommendations"),
    ("give me recommendations", "request_recommendations"),
    ("show me attractions", "request_recommendations"),
    ("what's good to eat there", "request_recommendations"),
    ("recommend hotels", "request_recommendations"),
    ("suggest me hill stations", "request_recommendations"),
    ("what are the top sights", "request_recommendations"),
    ("where should we stay", "request_recommendations"),
    ("any hidden gems nearby", "request_recommendations"),
    ("what's worth visiting", "request_recommendations"),
    ("food options please", "request_recommendations"),

    # ------------------------------------------------------------------ #
    # build_itinerary — user wants the day-by-day plan
    # ------------------------------------------------------------------ #
    ("plan the days", "build_itinerary"),
    ("build my trip", "build_itinerary"),
    ("make the itinerary", "build_itinerary"),
    ("ok plan it", "build_itinerary"),
    ("go ahead and schedule it", "build_itinerary"),
    ("yes build the day by day plan", "build_itinerary"),
    ("create the schedule", "build_itinerary"),
    ("map out the days", "build_itinerary"),
    ("let's plan the trip", "build_itinerary"),
    ("schedule it please", "build_itinerary"),

    # ------------------------------------------------------------------ #
    # change_pace — user wants a faster or slower schedule
    # ------------------------------------------------------------------ #
    ("make it relaxed", "change_pace"),
    ("more packed please", "change_pace"),
    ("slow it down", "change_pace"),
    ("can we do more per day", "change_pace"),
    ("less packed", "change_pace"),
    ("take it easy", "change_pace"),
    ("we want a slower pace", "change_pace"),
    ("cram more in", "change_pace"),
    ("fewer things per day", "change_pace"),
    ("too busy, tone it down", "change_pace"),

    # ------------------------------------------------------------------ #
    # remove_stop — user wants to drop or swap a scheduled stop
    # ------------------------------------------------------------------ #
    ("drop Sunset Deck", "remove_stop"),
    ("remove the museum", "remove_stop"),
    ("swap out the tea garden", "remove_stop"),
    ("skip the waterfall", "remove_stop"),
    ("take out stop 2", "remove_stop"),
    ("delete the viewpoint", "remove_stop"),
    ("exclude the zoo", "remove_stop"),
    ("replace stop 3", "remove_stop"),
    ("don't include the temple", "remove_stop"),

    # ------------------------------------------------------------------ #
    # ask_budget — user wants the cost estimate
    # ------------------------------------------------------------------ #
    ("what will it cost", "ask_budget"),
    ("how much is the trip", "ask_budget"),
    ("estimate my budget", "ask_budget"),
    ("total cost please", "ask_budget"),
    ("is it within my budget", "ask_budget"),
    ("how expensive", "ask_budget"),
    ("what's the damage", "ask_budget"),
    ("cost breakdown", "ask_budget"),
    ("give me a budget estimate", "ask_budget"),

    # ------------------------------------------------------------------ #
    # confirm_destination — user picked a candidate
    # ------------------------------------------------------------------ #
    ("1", "confirm_destination"),
    ("2", "confirm_destination"),
    ("3", "confirm_destination"),
    ("4", "confirm_destination"),
    ("option 2", "confirm_destination"),
    ("number 3", "confirm_destination"),
    ("the first one", "confirm_destination"),
    ("the second one", "confirm_destination"),
    ("Ooty", "confirm_destination"),
    ("Kodaikanal", "confirm_destination"),
    ("let's go with Munnar", "confirm_destination"),
    ("pick option 3", "confirm_destination"),
    ("Kodaikanal it is", "confirm_destination"),
    ("we'll go with Ooty", "confirm_destination"),
    ("choose Munnar", "confirm_destination"),

    # ------------------------------------------------------------------ #
    # ask_transport — user wants transport options
    # ------------------------------------------------------------------ #
    ("how do I get there", "ask_transport"),
    ("what are the transport options", "ask_transport"),
    ("should I fly or drive", "ask_transport"),
    ("train or bus", "ask_transport"),
    ("how should I reach there", "ask_transport"),
    ("what's the best way to travel", "ask_transport"),
    ("flight or train", "ask_transport"),
    ("how do we travel from Chennai", "ask_transport"),

    # ------------------------------------------------------------------ #
    # greet
    # ------------------------------------------------------------------ #
    ("hi", "greet"),
    ("hello", "greet"),
    ("hey there", "greet"),
    ("good morning", "greet"),
    ("hi there", "greet"),
    ("good evening", "greet"),
    ("namaste", "greet"),
    ("hey", "greet"),

    # ------------------------------------------------------------------ #
    # ask_weather — user wants weather or climate info
    # ------------------------------------------------------------------ #
    ("what's the weather", "ask_weather"),
    ("what is the weather like", "ask_weather"),
    ("how is the weather in Ooty", "ask_weather"),
    ("is it going to rain", "ask_weather"),
    ("weather forecast for the trip", "ask_weather"),
    ("will it be cold", "ask_weather"),
    ("what's the climate like", "ask_weather"),
    ("temperature in Munnar", "ask_weather"),
    ("will it rain next week", "ask_weather"),
    ("show me the weather forecast", "ask_weather"),
    ("how is the climate", "ask_weather"),
    ("weather outlook", "ask_weather"),

    # ------------------------------------------------------------------ #
    # other — acknowledgements and misc
    # ------------------------------------------------------------------ #
    ("thanks", "other"),
    ("ok", "other"),
    ("cool", "other"),
    ("hmm", "other"),
    ("sounds good", "other"),
    ("got it", "other"),
    ("nice", "other"),
    ("no", "other"),
    ("maybe later", "other"),
]


class IntentClassifier:
    """TF-IDF (word + char) + Logistic Regression, trained on TRAINING_DATA."""

    def __init__(self):
        self.pipeline: Pipeline | None = None
        self._load_or_train()

    def _load_or_train(self):
        if os.path.exists(MODEL_PATH):
            try:
                self.pipeline = joblib.load(MODEL_PATH)
                return
            except Exception:
                # Corrupt or incompatible pickle — fall through to retrain.
                pass
        self._train()

    def _train(self):
        X = [t for t, _ in TRAINING_DATA]
        y = [lbl for _, lbl in TRAINING_DATA]

        # Word n-grams capture "plan the days"; char n-grams give short
        # tokens like "2" or "hi" some signal even when the word
        # vocabulary has nothing useful.
        word_vec = TfidfVectorizer(ngram_range=(1, 2), lowercase=True)
        char_vec = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), lowercase=True,
        )
        feats = FeatureUnion([("word", word_vec), ("char", char_vec)])

        self.pipeline = Pipeline([
            ("feats", feats),
            ("clf", LogisticRegression(
                max_iter=2000,
                C=2.0,
                class_weight="balanced",
            )),
        ])
        self.pipeline.fit(X, y)

        os.makedirs(os.path.dirname(MODEL_PATH) or ".", exist_ok=True)
        joblib.dump(self.pipeline, MODEL_PATH)

    def predict(self, text: str) -> tuple[str, float]:
        if not text.strip() or self.pipeline is None:
            return "other", 0.0
        proba = self.pipeline.predict_proba([text])[0]
        idx = int(proba.argmax())
        return self.pipeline.classes_[idx], float(proba[idx])


_CLF: IntentClassifier | None = None


def _get_classifier() -> IntentClassifier:
    global _CLF
    if _CLF is None:
        _CLF = IntentClassifier()
    return _CLF


def classify(text: str, state=None) -> tuple[str, float, str]:
    """Return (intent, confidence, source).

    source is "rule" when a high-precision regex fired, else "classifier".
    Callers should only short-circuit on source == "rule", or on
    source == "classifier" with confidence above a strict threshold.
    """
    # --- Layer 1: rules (high-precision, trusted) ----------------------
    r = intent_rules.rule_intent(text, state)
    if r is not None:
        intent, conf = r
        return intent, conf, "rule"

    # --- Layer 2: ML classifier (fuzzy fallback) -----------------------
    clf = _get_classifier()
    intent, conf = clf.predict(text)
    return intent, conf, "classifier"


def retrain() -> None:
    """Force a full retrain and overwrite the cached model."""
    global _CLF
    _CLF = IntentClassifier()
    _CLF._train()


if __name__ == "__main__":
    # Quick manual check: python -m agent.intent
    samples = [
        "I'm from Chennai and want to go to Ooty",
        "we are 4 people, budget 40k, 15/9/26 to 20/9/26",
        "suggest me hill stations",
        "plan the days",
        "make it relaxed",
        "drop Sunset Deck",
        "what will it cost",
        "hi",
        "2",
        "how do I get there",
    ]
    for s in samples:
        intent, conf, source = classify(s)
        print(f"{s!r:58} -> {intent:24} {conf:.2f}  [{source}]")