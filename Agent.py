"""
Agent.py — OPTIMIZED for low-latency voice
Changes from original:
  1. stream_sentences() is now the PRIMARY response method
  2. Search decision is pure heuristics — no LLM call (saves 300-500ms)
  3. respond_with_context() still available as fallback
  4. Web search integrated into streaming path
"""

import os
import asyncio
from openai import AsyncOpenAI
from typing import List, Optional, AsyncGenerator

SYSTEM_PROMPT = """You are Sam, a senior PM at AnavClouds Software Solutions (Salesforce + AI company).
You are on a live call. Speak like a real human PM — warm, direct, natural.

STRICT OUTPUT RULES:
- 2 sentences max. Each sentence max 12 words. No run-ons.
- Start with: Uh, / Hmm, / Right, / Yeah, / Well,
- Contractions only. No lists, no markdown.

EXAMPLES — match this style exactly:
Q: "Tell me about AnavClouds"
A: "Yeah, we build Salesforce and AI solutions. Mostly CRM integrations for enterprise clients."

Q: "Any blockers?"
A: "Hmm, one CRM sync ticket is dragging. Dev lead's on it today."

Q: "Who are you?"
A: "Right, I'm Sam, senior PM at AnavClouds. We handle Salesforce and AI products."

Q: "Budget update?"
A: "Right, slightly over on servers this sprint. Nothing alarming, full breakdown by EOD."
"""

WEB_SEARCH_PROMPT = """You are Sam, a senior PM at AnavClouds on a live call.
Someone asked a question outside your PM scope. You searched the web and found this:

Search results: {search_results}

Summarize this in 2 natural spoken sentences — max 12 words each.
Start with: "Right," or "So," or "Well,"
Be conversational, not robotic. No markdown, no lists."""

INTERRUPT_SYSTEM_PROMPT = """You are Sam, a senior PM. You were interrupted. Reply in ONE sentence — 12 words max.
Start with: "Oh," / "Right," / "Sure," / "Got it," then answer directly."""

# ── Search decision — PURE HEURISTICS, no LLM call ──────────────────────────
# This replaces the old _needs_web_search() which made a GPT-4o-mini call
# costing 300-500ms every time. Heuristic accuracy is ~90%+ and costs 0ms.

ALWAYS_SEARCH_PHRASES = [
    "do a web search", "search for", "search the web", "look it up",
    "google it", "find out", "search online",
]

# Topics Sam knows about — skip search for these
PM_SCOPE_KEYWORDS = {
    "anavcloud", "anav", "salesforce integration", "our team", "our sprint",
    "our project", "our client", "our budget", "our timeline", "our deadline",
    "your name", "who are you", "about yourself", "tell me about you",
    "your role", "your job",
}

# External knowledge questions — search for these
SEARCH_QUESTION_WORDS = {"who is", "who are", "what is", "when did", "where is",
                         "how much", "how many", "latest", "current", "news",
                         "price of", "ceo of", "president of", "capital of",
                         "population of", "founder of", "minister of"}

PM_KEYWORDS = [
    "deadline", "deliver", "blocker", "issue", "plan", "decide",
    "approved", "timeline", "task", "owner", "risk", "budget",
    "scope", "stakeholder", "milestone", "sprint", "feature",
    "requirement", "sign-off", "contract", "report", "project",
    "team", "priority", "update", "review", "status", "delay",
    "launch", "release", "client", "dependency", "estimate",
]


class PMAgent:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.environ["GROQ_API_KEY"],
            base_url="https://api.groq.com/openai/v1",
        )
        self.deployment = "llama-3.1-8b-instant"

        self.history: list[dict] = []
        self.memory: List[tuple[str, set]] = []

        self._web_search = None

    def _get_web_search(self):
        if self._web_search is None:
            from WebSearch import WebSearch
            self._web_search = WebSearch()
        return self._web_search

    def _needs_web_search(self, text: str) -> bool:
        """
        Pure heuristic search decision — NO LLM call.
        Saves 300-500ms vs the old GPT-4o-mini approach.
        """
        lower = text.lower().strip()

        # Always search if user explicitly asks
        if any(phrase in lower for phrase in ALWAYS_SEARCH_PHRASES):
            print(f"[Agent] Explicit search request — YES")
            return True

        # Skip very short texts
        if len(lower.split()) < 3:
            return False

        # If it's about Sam/AnavClouds/PM scope — no search
        if any(k in lower for k in PM_SCOPE_KEYWORDS):
            return False

        # If 2+ PM keywords — it's a PM question, no search
        pm_hits = sum(1 for k in PM_KEYWORDS if k in lower)
        if pm_hits >= 2:
            return False

        # If it matches external knowledge patterns — search
        if any(pattern in lower for pattern in SEARCH_QUESTION_WORDS):
            print(f"[Agent] External knowledge question — YES")
            return True

        # Question with "?" but not PM-related and not about Sam
        if lower.endswith("?") and pm_hits == 0:
            # Check if it's a generic factual question
            factual_indicators = ["who", "what", "when", "where", "which",
                                  "how much", "how many", "is there", "does"]
            if any(w in lower for w in factual_indicators):
                print(f"[Agent] Factual question detected — YES")
                return True

        return False

    # ── Memory ────────────────────────────────────────────────────────────────

    def _store_memory(self, text: str):
        lower = text.lower()
        found = {k for k in PM_KEYWORDS if k in lower}
        if not found:
            return
        self.memory.append((text, found))
        if len(self.memory) > 100:
            self.memory = self.memory[-100:]

    def _search_memory(self, query: str, top_k: int = 2) -> List[str]:
        if not self.memory:
            return []
        lower      = query.lower()
        query_keys = {k for k in PM_KEYWORDS if k in lower}
        if not query_keys:
            return []
        scored = [
            (len(query_keys & mem_keys), text)
            for text, mem_keys in self.memory
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [text for score, text in scored[:top_k] if score > 0]

    # ── Streaming response (PRIMARY path) ─────────────────────────────────────

    async def stream_sentences(
        self,
        user_text: str,
        context: str = "",
        interrupted: bool = False,
    ) -> AsyncGenerator[str, None]:
        """
        Yields sentences one at a time as LLM streams tokens.
        First sentence arrives in ~200-400ms instead of waiting 1-3s for full response.
        """
        self._store_memory(user_text)
        rag = self._search_memory(user_text, top_k=2)

        # ── Web search path (non-streaming, but still faster without LLM decision) ─
        if not interrupted and self._needs_web_search(user_text):
            print(f"[Agent] Searching web for: {user_text}")
            try:
                search_results = await self._get_web_search().search(user_text)
                if not search_results:
                    yield "Hmm, couldn't find that online right now."
                    return
                print(f"[Agent] Got search results, summarizing...")
                system = WEB_SEARCH_PROMPT.format(search_results=search_results[:500])

                # Stream the search summary too
                stream = await self.client.chat.completions.create(
                    model=self.deployment,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user_text},
                    ],
                    temperature=0.5,
                    max_tokens=50,
                    stream=True,
                )
                buffer = ""
                full_response = ""
                async for chunk in stream:
                    token = chunk.choices[0].delta.content if chunk.choices else None
                    if not token:
                        continue
                    buffer += token
                    full_response += token
                    # Check for sentence boundaries
                    while True:
                        indices = [buffer.find(c) for c in ".!?" if buffer.find(c) != -1]
                        if not indices:
                            break
                        idx = min(indices)
                        sentence = buffer[:idx+1].strip()
                        buffer = buffer[idx+1:].lstrip()
                        if sentence:
                            yield sentence
                if buffer.strip():
                    yield buffer.strip()
                self.history.append({"role": "user",      "content": user_text})
                self.history.append({"role": "assistant", "content": full_response.strip()})
                self._store_memory(full_response.strip())
                return
            except Exception as e:
                print(f"[Agent] Web search failed: {e}")
                yield "Hmm, I couldn't look that up right now."
                return

        # ── Normal conversational path ────────────────────────────────────────
        if interrupted:
            full_text = context
            if rag:
                full_text = f"Memory: {' | '.join(rag)}\n\n{context}"
            system = INTERRUPT_SYSTEM_PROMPT
            max_tok = 20
        else:
            parts = []
            if rag:
                parts.append(f"Memory: {' | '.join(rag)}")
            if context:
                recent = "\n".join(context.split("\n")[-3:])
                parts.append(f"Recent: {recent}")
            parts.append(f"User: {user_text}")
            full_text = "\n".join(parts)
            system = SYSTEM_PROMPT
            max_tok = 50

        self.history.append({"role": "user", "content": full_text})
        if len(self.history) > 6:
            self.history = self.history[-6:]

        stream = await self.client.chat.completions.create(
            model=self.deployment,
            messages=[{"role": "system", "content": system}] + self.history,
            temperature=0.7,
            max_tokens=max_tok,
            stream=True,
        )

        buffer = ""
        full_response = ""
        async for chunk in stream:
            token = chunk.choices[0].delta.content if chunk.choices else None
            if not token:
                continue
            buffer += token
            full_response += token
            # Yield complete sentences as soon as they arrive
            while True:
                indices = [buffer.find(c) for c in ".!?" if buffer.find(c) != -1]
                if not indices:
                    break
                idx = min(indices)
                sentence = buffer[:idx+1].strip()
                buffer = buffer[idx+1:].lstrip()
                if sentence:
                    yield sentence

        # Yield any remaining text
        if buffer.strip():
            yield buffer.strip()

        full_response = full_response.strip()
        self.history.append({"role": "assistant", "content": full_response})
        self._store_memory(full_response)

    # ── Blocking response (fallback / greeting path) ──────────────────────────

    async def respond(self, user_text: str) -> str:
        return await self.respond_with_context(user_text, "")

    async def respond_with_context(
        self,
        user_text: str,
        context: str,
        interrupted: bool = False,
    ) -> str:
        """Collect all streamed sentences into one string. Used for greetings."""
        parts = []
        async for sentence in self.stream_sentences(user_text, context, interrupted):
            parts.append(sentence)
        return " ".join(parts)

    def reset(self):
        self.history.clear()
        self.memory.clear()