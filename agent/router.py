"""In-Between Model: Semantic & Hybrid Router for Tool Calling.

Acts as an intelligent arbiter deciding whether to route a user's turn to:
  1. The NLP Tool Calling Engine (agent/tool_router.py):
     - Deterministic, ultra-fast (<50ms), 0 LLM tokens, handles structured tool commands,
       slot updates, and dialog-managed state progressions.
  2. The LLM Tool Calling Engine (agent/orchestrator.py):
     - Reasoning-heavy, handles open-ended nuances, comparative analysis, subjective recommendations,
       and conversational repair.

Uses lightweight local ONNX embeddings (via FastEmbed) with cosine similarity against
NLP and LLM route exemplars, combined with intent confidence, syntactic complexity,
and dialog state preconditions.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict
from typing import Literal, Optional, Tuple

import numpy as np

from agent.nlu import NLUResult
from agent.state import TripState

# --------------------------------------------------------------------------- #
# Route Exemplars
# --------------------------------------------------------------------------- #

_NLP_EXEMPLARS = [
    # Weather
    ("ask_weather", "what is the weather like in ooty"),
    ("ask_weather", "is it raining in kodaikanal"),
    ("ask_weather", "weather forecast for munnar from 20-9 to 25-9"),
    ("ask_weather", "check temperature and climate"),
    ("ask_weather", "will it rain during our trip"),
    ("ask_weather", "show weather outlook"),

    # Transport
    ("ask_transport", "how to travel from chennai to munnar"),
    ("ask_transport", "show flight train bus and driving options"),
    ("ask_transport", "best way to get there from bangalore"),
    ("ask_transport", "transport recommendations and route"),
    ("ask_transport", "how do we reach coonoor"),

    # Recommendations
    ("request_recommendations", "show top attractions and places to visit"),
    ("request_recommendations", "what are the best sightseeing spots in ooty"),
    ("request_recommendations", "recommend stays hotels and food spots"),
    ("request_recommendations", "things to do in kodaikanal"),
    ("request_recommendations", "show recommendations for this destination"),

    # Build Itinerary
    ("build_itinerary", "plan the days"),
    ("build_itinerary", "build my itinerary"),
    ("build_itinerary", "generate the trip schedule"),
    ("build_itinerary", "create day by day itinerary for 5 days"),
    ("build_itinerary", "schedule our days"),

    # Budget
    ("ask_budget", "how much will it cost"),
    ("ask_budget", "budget estimate for this trip"),
    ("ask_budget", "what is the total expense for 4 people"),
    ("ask_budget", "cost breakdown in inr"),

    # Pace
    ("change_pace", "make it more relaxed"),
    ("change_pace", "slow it down less per day"),
    ("change_pace", "packed schedule please"),
    ("change_pace", "cram more sights per day"),
    ("change_pace", "change pace to relaxed"),

    # Selection & Confirmation
    ("confirm_destination", "option 1"),
    ("confirm_destination", "number 2"),
    ("confirm_destination", "the first one"),
    ("confirm_destination", "let's go with ooty"),
    ("confirm_destination", "choose option 3"),

    # Pagination / Show More
    ("show_more_candidates", "more"),
    ("show_more_candidates", "show more"),
    ("show_more_candidates", "more options"),
    ("show_more_candidates", "what else is there"),
    ("show_more_candidates", "any other hill stations"),
    ("show_more_candidates", "show remaining places"),
    ("show_more_candidates", "see more destinations"),

    # Slots & Initiation
    ("provide_slot", "make a plan from tirunelveli to any hill station from 20-9 to 25-9 for 4 members under 40k"),
    ("provide_slot", "trip from chennai to ooty for 2 people"),
    ("provide_slot", "origin is bangalore"),
    ("provide_slot", "dates are 20th to 25th september"),
    ("provide_slot", "budget is 50000 rupees"),
    ("provide_slot", "we are 4 travellers"),

    # Discovery
    ("search_destination_candidates", "find hill stations near tirunelveli"),
    ("search_destination_candidates", "any beach nearby"),
    ("search_destination_candidates", "places to visit around coimbatore"),
    ("search_destination_candidates", "show nearby hill stations"),

    # Greetings
    ("greet", "hello"),
    ("greet", "hi there"),
    ("greet", "hey travel agent"),
]

_LLM_EXEMPLARS = [
    # Comparative Reasoning
    "Can you compare Ooty vs Kodaikanal for a quiet family trip with elderly grandparents?",
    "Which destination is more scenic and less crowded in October: Munnar or Coonoor?",
    "Why should I choose Wayanad over Ooty for a 4-day trip?",
    "Compare the travel vibe and food culture between Tamil Nadu and Kerala hill stations.",

    # Subjective / Open-ended
    "My children are fascinated by astronomy and botany, what unique educational spots would they love?",
    "I want an offbeat and quiet homestay surrounded by tea estates away from crowded tourist spots.",
    "Can you recommend a romantic itinerary for a honeymoon couple who love photography and nature walks?",
    "We want a blend of adventure sports, trekking, and peaceful lakeside evenings.",

    # Explanations & Advice
    "Can you explain the history and cultural heritage of the Nilgiri Mountain Railway?",
    "Is it safe to drive self-drive cars up the 36 hairpin bends during heavy monsoon rains?",
    "What kind of clothes, footwear, and essentials should we pack for foggy, rainy weather in the hills?",
    "Help me decide between booking a luxury resort or an authentic heritage homestay.",

    # Creative / Conversational
    "Write a short poem or travel quote inspired by the misty mountains of the Western Ghats.",
    "Tell me an interesting local myth or folklore about Kodaikanal lake.",
    "I'm feeling stressed and just want to disconnect from work, what kind of itinerary would you design?",
]

# Keywords that strongly signal qualitative/subjective LLM reasoning
_LLM_COMPLEXITY_KEYWORDS = {
    "compare", "vs", "versus", "which is better", "opinion", "advise", "advice",
    "recommend for my", "why", "explain", "history", "story", "poem", "safe to",
    "should i pack", "what to wear", "elderly", "grandparents", "toddler",
    "infant", "honeymoon", "offbeat", "crowded", "peaceful", "romantic",
    "vibe", "feel like", "disconnect", "relaxing vs", "difference between",
}

@dataclass
class RouteDecision:
    """Outcome of the in-between routing decision."""
    target: Literal["nlp", "llm"]
    confidence: float
    nlp_score: float
    llm_score: float
    reasons: list[str]
    latency_ms: float
    suggested_tool: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

class ToolCallRouter:
    """In-Between Model that arbitrates between NLP and LLM tool execution."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._embedder = None
        self._nlp_embeddings: Optional[np.ndarray] = None
        self._llm_embeddings: Optional[np.ndarray] = None
        self._nlp_tools: list[str] = [t for t, _ in _NLP_EXEMPLARS]
        self._nlp_texts: list[str] = [txt for _, txt in _NLP_EXEMPLARS]
        self._llm_texts: list[str] = _LLM_EXEMPLARS

        self._init_embeddings()

    def _init_embeddings(self):
        """Initialize local ONNX FastEmbed model and pre-compute exemplar embeddings."""
        try:
            from fastembed import TextEmbedding
            # FastEmbed downloads and caches ONNX model locally (~80MB, runs on CPU)
            self._embedder = TextEmbedding(model_name=self.model_name)

            nlp_vecs = list(self._embedder.embed(self._nlp_texts))
            self._nlp_embeddings = np.array(nlp_vecs, dtype=np.float32)
            # Normalize vectors for cosine similarity via dot product
            self._nlp_embeddings /= np.linalg.norm(self._nlp_embeddings, axis=1, keepdims=True)

            llm_vecs = list(self._embedder.embed(self._llm_texts))
            self._llm_embeddings = np.array(llm_vecs, dtype=np.float32)
            self._llm_embeddings /= np.linalg.norm(self._llm_embeddings, axis=1, keepdims=True)
        except Exception:
            # Fallback to TF-IDF if FastEmbed fails
            self._embedder = None
            self._init_tfidf_fallback()

    def _init_tfidf_fallback(self):
        """Fallback lightweight TF-IDF semantic space if FastEmbed is unavailable."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        self._tfidf = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
        all_texts = self._nlp_texts + self._llm_texts
        self._tfidf.fit(all_texts)
        self._nlp_embeddings = self._tfidf.transform(self._nlp_texts).toarray()
        self._llm_embeddings = self._tfidf.transform(self._llm_texts).toarray()

    def _embed_query(self, query: str) -> np.ndarray:
        """Embed a single query into normalized vector space."""
        if self._embedder is not None:
            vec = list(self._embedder.embed([query]))[0]
            vec = np.array(vec, dtype=np.float32)
            norm = np.linalg.norm(vec)
            return vec / (norm + 1e-9)
        else:
            vec = self._tfidf.transform([query]).toarray()[0]
            norm = np.linalg.norm(vec)
            return vec / (norm + 1e-9)

    def decide(
        self,
        user_text: str,
        nlu_result: NLUResult,
        state: TripState,
    ) -> RouteDecision:
        """Evaluate user_text + NLU + State to decide whether to route to NLP or LLM.

        Returns:
            RouteDecision with target ("nlp" | "llm"), confidence, scores, and rationale.
        """
        t0 = time.perf_counter()
        reasons: list[str] = []
        user_low = user_text.lower().strip()

        # ------------------------------------------------------------------ #
        # 1. Fast Path: Definite Deterministic Rules
        # ------------------------------------------------------------------ #
        # Simple numeric/option selections: "1", "option 2", "the first one"
        if nlu_result.intent == "confirm_destination" and nlu_result.source == "rule":
            reasons.append("rule:confirm_destination_selection")
            dt = (time.perf_counter() - t0) * 1000
            return RouteDecision(
                target="nlp",
                confidence=0.98,
                nlp_score=1.0,
                llm_score=0.0,
                reasons=reasons,
                latency_ms=dt,
                suggested_tool="confirm_destination",
            )

        # Simple greeting
        if nlu_result.intent == "greet" and len(user_text.split()) <= 4:
            reasons.append("rule:greeting")
            dt = (time.perf_counter() - t0) * 1000
            return RouteDecision(
                target="nlp",
                confidence=0.96,
                nlp_score=0.95,
                llm_score=0.05,
                reasons=reasons,
                latency_ms=dt,
                suggested_tool=None,
            )

        # ------------------------------------------------------------------ #
        # 2. Semantic Embedding Cosine Similarity
        # ------------------------------------------------------------------ #
        query_vec = self._embed_query(user_text)

        # Max cosine similarity to NLP exemplars
        nlp_sims = np.dot(self._nlp_embeddings, query_vec)
        best_nlp_idx = int(np.argmax(nlp_sims))
        best_nlp_score = float(nlp_sims[best_nlp_idx])
        suggested_tool = self._nlp_tools[best_nlp_idx]

        # Max cosine similarity to LLM exemplars
        llm_sims = np.dot(self._llm_embeddings, query_vec)
        best_llm_score = float(np.max(llm_sims))

        # ------------------------------------------------------------------ #
        # 3. Complexity & Qualitative Analysis
        # ------------------------------------------------------------------ #
        has_complexity_kw = any(
            re.search(rf"\b{re.escape(kw)}\b", user_low)
            for kw in _LLM_COMPLEXITY_KEYWORDS
        )
        is_long_query = len(user_text.split()) > 25

        if has_complexity_kw:
            reasons.append("complexity:keywords_detected")
        if is_long_query:
            reasons.append("complexity:long_open_ended_utterance")

        # ------------------------------------------------------------------ #
        # 4. Arbitration Logic
        # ------------------------------------------------------------------ #
        # Case A: Clear subjective/reasoning query -> LLM
        if (has_complexity_kw or is_long_query) and best_llm_score >= 0.50:
            target = "llm"
            conf = min(0.95, best_llm_score + 0.15)
            reasons.append(f"semantic:llm_dominance({best_llm_score:.2f} vs {best_nlp_score:.2f})")

        # Case B: LLM semantic score significantly higher than NLP score
        elif best_llm_score > best_nlp_score + 0.15 and not nlu_result.has_slots:
            target = "llm"
            conf = min(0.92, best_llm_score)
            reasons.append(f"semantic:higher_llm_similarity({best_llm_score:.2f} > {best_nlp_score:.2f})")

        # Case C: High NLP semantic similarity or high-confidence intent with slots
        elif best_nlp_score >= 0.65 or (nlu_result.has_slots and best_nlp_score >= 0.45):
            target = "nlp"
            conf = max(best_nlp_score, nlu_result.confidence)
            # FIX: when the rule layer fired, trust its intent over the
            # embedding neighbour. The dispatch in tool_router already uses
            # nlu_result.intent, so this only corrects the trace's
            # `suggested_tool` badge — but a wrong badge is a debugging
            # trap, so keep them consistent.
            if nlu_result.source == "rule":
                suggested_tool = nlu_result.intent
            reasons.append(f"semantic:high_nlp_match({suggested_tool}, score={best_nlp_score:.2f})")
            if nlu_result.has_slots:
                reasons.append("slots:entities_present")

        # Case D: Rule-based intent from NLU
        elif nlu_result.source == "rule" and nlu_result.high_confidence:
            target = "nlp"
            conf = nlu_result.confidence
            reasons.append(f"nlu:high_confidence_rule_intent({nlu_result.intent})")
            suggested_tool = nlu_result.intent

        # Case E: Ambiguous / Low Confidence -> Defer to LLM
        else:
            target = "llm"
            conf = 0.70
            reasons.append(f"fallback:ambiguous_or_low_confidence(nlp={best_nlp_score:.2f}, llm={best_llm_score:.2f})")

        dt = (time.perf_counter() - t0) * 1000
        return RouteDecision(
            target=target,
            confidence=round(conf, 3),
            nlp_score=round(best_nlp_score, 3),
            llm_score=round(best_llm_score, 3),
            reasons=reasons,
            latency_ms=round(dt, 2),
            suggested_tool=suggested_tool if target == "nlp" else None,
        )