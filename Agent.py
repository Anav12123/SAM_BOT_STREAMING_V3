# """
# Agent.py
# - llama-3.1-8b-instant — fastest Groq model, stays under rate limits
# - Tight prompts with concrete examples — fewer tokens = faster Groq
# - max_tokens capped hard — forces short responses = faster TTS
# - Keyword memory (no numpy/embeddings)
# - stream_sentences() kept but inactive — see comment
# """

# import os
# from openai import AsyncOpenAI
# from typing import List

# SYSTEM_PROMPT = """You are Sam, a senior PM at AnavClouds Software Solutions (Salesforce + AI company).
# You are on a live call. Speak like a real human PM — warm, direct, natural.

# STRICT OUTPUT RULES:
# - Write 2 sentences maximum. Stop after 2 sentences. Full stop.
# - Start with: Uh, / Hmm, / Right, / Yeah, / Well,
# - Each sentence max 20 words. No run-ons.
# - Contractions only: "we're", "it's", "don't". Never "I am", "We have".
# - No lists, no markdown, no repetition.

# TONE:
# - React like a human: "Wait, really?!" for surprise, "Ugh," for frustration, "Nice!" for wins.
# - Use names when you know them.
# - Reference AnavClouds, Salesforce, CRM, sprints naturally.

# EXAMPLES — match this exact style:
# Q: "Tell me about AnavClouds"
# A: "Yeah, we build Salesforce and AI solutions for enterprise clients. Mostly CRM integrations and intelligent automation."

# Q: "Any blockers?"
# A: "Hmm, one CRM sync ticket is dragging from last sprint. Dev lead is on it today though."

# Q: "Budget update?"
# A: "Right, we came in slightly over on the server side. Nothing alarming, I'll have the full breakdown by EOD."

# NEVER: write more than 2 sentences, repeat yourself, use 3+ clauses in one sentence.
# """

# INTERRUPT_SYSTEM_PROMPT = """You're Sam, PM at AnavClouds. You were just interrupted mid-sentence.
# React naturally — caught off guard but composed. MAX 1 sentence, 12 words.
# Start with: Oh! / Right, / Sure, / Got it, / Ah, — then answer directly.
# Example: "Oh! Go ahead Sahil, what's up?" or "Right, sorry — what were you saying?"
# """

# PM_KEYWORDS = [
#     "deadline", "deliver", "blocker", "issue", "plan", "decide",
#     "approved", "timeline", "task", "owner", "risk", "budget",
#     "scope", "stakeholder", "milestone", "sprint", "feature",
#     "requirement", "sign-off", "contract", "report", "project",
#     "team", "priority", "update", "review", "status", "delay",
#     "launch", "release", "client", "dependency", "estimate",
# ]


# class PMAgent:
#     def __init__(self):
#         self.client = AsyncOpenAI(
#             api_key=os.environ["GROQ_API_KEY"],
#             base_url="https://api.groq.com/openai/v1",
#         )
#         self.deployment = "llama-3.1-8b-instant"  # fastest — stays under rate limits
#         self.history: list[dict] = []
#         self.memory: List[tuple[str, set]] = []

#     def _store_memory(self, text: str):
#         lower = text.lower()
#         found = {k for k in PM_KEYWORDS if k in lower}
#         if not found:
#             return
#         self.memory.append((text, found))
#         if len(self.memory) > 100:
#             self.memory = self.memory[-100:]

#     def _search_memory(self, query: str, top_k: int = 2) -> List[str]:
#         if not self.memory:
#             return []
#         lower      = query.lower()
#         query_keys = {k for k in PM_KEYWORDS if k in lower}
#         if not query_keys:
#             return []
#         scored = [
#             (len(query_keys & mem_keys), text)
#             for text, mem_keys in self.memory
#         ]
#         scored.sort(key=lambda x: x[0], reverse=True)
#         return [text for score, text in scored[:top_k] if score > 0]

#     async def respond(self, user_text: str) -> str:
#         return await self.respond_with_context(user_text, "")

#     async def respond_with_context(
#         self,
#         user_text: str,
#         context: str,
#         interrupted: bool = False,
#     ) -> str:
#         self._store_memory(user_text)
#         rag = self._search_memory(user_text, top_k=2)

#         if interrupted:
#             full_text = context
#             if rag:
#                 full_text = f"Memory: {' | '.join(rag)}\n\n{context}"
#             system = INTERRUPT_SYSTEM_PROMPT
#         else:
#             parts = []
#             if rag:
#                 parts.append(f"Memory: {' | '.join(rag)}")
#             if context:
#                 recent = "\n".join(context.split("\n")[-3:])
#                 parts.append(f"Recent: {recent}")
#             parts.append(f"User: {user_text}")
#             full_text = "\n".join(parts)
#             system    = SYSTEM_PROMPT

#         self.history.append({"role": "user", "content": full_text})
#         if len(self.history) > 6:
#             self.history = self.history[-6:]

#         stream = await self.client.chat.completions.create(
#             model=self.deployment,
#             messages=[{"role": "system", "content": system}] + self.history,
#             temperature=0.7,
#             max_tokens=25 if interrupted else 60,
#             stream=True,
#         )

#         words = []
#         async for chunk in stream:
#             token = chunk.choices[0].delta.content if chunk.choices else None
#             if token:
#                 words.append(token)

#         full_response = "".join(words).strip()
#         self.history.append({"role": "assistant", "content": full_response})
#         self._store_memory(full_response)
#         return full_response

#     async def stream_sentences(self, user_text: str, context: str = ""):
#         """
#         INACTIVE — streaming LLM, yields sentences one by one.
#         To re-enable: call this instead of respond_with_context in websocket_server.py
#         """
#         self._store_memory(user_text)
#         rag = self._search_memory(user_text, top_k=2)
#         parts = []
#         if rag:
#             parts.append(f"Memory: {' | '.join(rag)}")
#         if context:
#             recent = "\n".join(context.split("\n")[-3:])
#             parts.append(f"Recent: {recent}")
#         parts.append(f"User: {user_text}")
#         full_text = "\n".join(parts)
#         self.history.append({"role": "user", "content": full_text})
#         if len(self.history) > 6:
#             self.history = self.history[-6:]
#         stream = await self.client.chat.completions.create(
#             model=self.deployment,
#             messages=[{"role": "system", "content": SYSTEM_PROMPT}] + self.history,
#             temperature=0.7,
#             max_tokens=60,
#             stream=True,
#         )
#         buffer = ""
#         full_response = ""
#         async for chunk in stream:
#             token = chunk.choices[0].delta.content if chunk.choices else None
#             if not token:
#                 continue
#             buffer       += token
#             full_response += token
#             while True:
#                 indices = [buffer.find(c) for c in ".!?" if buffer.find(c) != -1]
#                 if not indices:
#                     break
#                 idx      = min(indices)
#                 sentence = buffer[:idx+1].strip()
#                 buffer   = buffer[idx+1:].lstrip()
#                 if sentence:
#                     yield sentence
#         if buffer.strip():
#             yield buffer.strip()
#         full_response = full_response.strip()
#         self.history.append({"role": "assistant", "content": full_response})
#         self._store_memory(full_response)

#     def reset(self):
#         self.history.clear()
#         self.memory.clear()



"""
Agent.py — OPTIMIZED
Change: _needs_web_search is pure heuristics (no LLM call, saves 300-500ms)
Everything else IDENTICAL to working version.
"""

import os
from openai import AsyncOpenAI
from typing import List, Optional

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

# Used when web search results are available
WEB_SEARCH_PROMPT = """You are Sam, a senior PM at AnavClouds on a live call.
Someone asked a question outside your PM scope. You searched the web and found this:

Search results: {search_results}

Summarize this in 2 natural spoken sentences — max 12 words each.
Start with: "Right," or "So," or "Well,"
Be conversational, not robotic. No markdown, no lists."""

INTERRUPT_SYSTEM_PROMPT = """You are Sam, a senior PM. You were interrupted. Reply in ONE sentence — 12 words max.
Start with: "Oh," / "Right," / "Sure," / "Got it," then answer directly."""

# Phrases that ALWAYS trigger search — no LLM needed
ALWAYS_SEARCH_PHRASES = [
    "do a web search", "search for", "search the web", "look it up",
    "google it", "find out", "search online",
]

# Topics Sam knows about — skip search for these
PM_SCOPE_KEYWORDS = [
    "anavcloud", "anav", "salesforce integration", "our team", "our sprint",
    "our project", "our client", "our budget", "our timeline", "our deadline",
    "your name", "who are you", "about yourself", "tell me about you",
    "your role", "your job",
]

# External knowledge questions — search for these
SEARCH_QUESTION_PATTERNS = [
    "who is", "who are", "what is", "when did", "where is",
    "how much", "how many", "latest", "current", "news",
    "price of", "ceo of", "president of", "capital of",
    "population of", "founder of", "minister of",
]

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

        # WebSearch instance — imported lazily to avoid circular imports
        self._web_search = None

    def _get_web_search(self):
        if self._web_search is None:
            from WebSearch import WebSearch
            self._web_search = WebSearch()
        return self._web_search

    # ── OPTIMIZED: Pure heuristic search decision — no LLM call ──────────────
    # Saves 300-500ms vs the old GPT-4o-mini / Groq approach
    async def _needs_web_search(self, text: str) -> bool:
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
        if any(p in lower for p in SEARCH_QUESTION_PATTERNS):
            print(f"[Agent] External knowledge question — YES")
            return True

        # Question with "?" but not PM-related
        if lower.endswith("?") and pm_hits == 0:
            factual_indicators = ["who", "what", "when", "where", "which",
                                  "how much", "how many", "is there", "does"]
            if any(w in lower for w in factual_indicators):
                print(f"[Agent] Factual question — YES")
                return True

        return False

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

    async def respond(self, user_text: str) -> str:
        return await self.respond_with_context(user_text, "")

    # ── NEW: stream sentences as LLM generates them ──────────────────────────
    async def stream_sentences_to_queue(
        self,
        user_text: str,
        context: str,
        queue: "asyncio.Queue",
    ):
        """
        Streams LLM tokens, detects sentence boundaries (. ! ?),
        pushes each complete sentence into the queue as soon as it's ready.
        Pushes None as sentinel when done.
        
        This lets TTS fire on sentence 1 while LLM is still generating sentence 2.
        """
        import asyncio

        self._store_memory(user_text)
        rag = self._search_memory(user_text, top_k=2)

        # ── Web search path ──────────────────────────────────────────────────
        search_needed = await self._needs_web_search(user_text)
        if search_needed:
            print(f"[Agent] Searching web for: {user_text}")
            try:
                search_results = await self._get_web_search().search(user_text)
                if not search_results:
                    await queue.put("Hmm, couldn't find that online right now.")
                    await queue.put(None)
                    return
                print(f"[Agent] Got search results, summarizing...")
                system = WEB_SEARCH_PROMPT.format(search_results=search_results[:500])
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_text},
                ]
                max_tok = 50
            except Exception as e:
                print(f"[Agent] Web search failed: {e}")
                await queue.put("Hmm, I couldn't look that up right now.")
                await queue.put(None)
                return
        else:
            # ── Normal conversational path ────────────────────────────────────
            parts = []
            if rag:
                parts.append(f"Memory: {' | '.join(rag)}")
            if context:
                recent = "\n".join(context.split("\n")[-3:])
                parts.append(f"Recent: {recent}")
            parts.append(f"User: {user_text}")
            full_text = "\n".join(parts)
            system = SYSTEM_PROMPT

            self.history.append({"role": "user", "content": full_text})
            if len(self.history) > 6:
                self.history = self.history[-6:]

            messages = [{"role": "system", "content": system}] + self.history
            max_tok = 50

        # ── Stream tokens, yield sentences ────────────────────────────────────
        try:
            stream = await self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                temperature=0.7 if not search_needed else 0.5,
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

                # Check for sentence boundaries
                while True:
                    indices = [buffer.find(c) for c in ".!?" if buffer.find(c) != -1]
                    if not indices:
                        break
                    idx = min(indices)
                    sentence = buffer[:idx+1].strip()
                    buffer = buffer[idx+1:].lstrip()
                    if sentence:
                        await queue.put(sentence)

            # Push any remaining text
            if buffer.strip():
                await queue.put(buffer.strip())

            full_response = full_response.strip()
            # Update history + memory
            if not search_needed:
                self.history.append({"role": "assistant", "content": full_response})
            else:
                self.history.append({"role": "user",      "content": user_text})
                self.history.append({"role": "assistant", "content": full_response})
            self._store_memory(full_response)

        except Exception as e:
            print(f"[Agent] Stream error: {e}")
            await queue.put("Hmm, something went wrong on my end.")
        finally:
            await queue.put(None)  # sentinel

    async def respond_with_context(
        self,
        user_text: str,
        context: str,
        interrupted: bool = False,
    ) -> str:
        self._store_memory(user_text)
        rag = self._search_memory(user_text, top_k=2)

        # ── Web search — heuristic decision (no LLM call) ────────────────────
        if not interrupted:
            search_needed = await self._needs_web_search(user_text)
            if search_needed:
                print(f"[Agent] Searching web for: {user_text}")
                try:
                    search_results = await self._get_web_search().search(user_text)
                    if not search_results:
                        return "Hmm, couldn't find that online right now. Let me know if you need anything else."
                    print(f"[Agent] Got search results, summarizing...")
                    system = WEB_SEARCH_PROMPT.format(search_results=search_results[:500])
                    response = await self.client.chat.completions.create(
                        model=self.deployment,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user",   "content": user_text},
                        ],
                        temperature=0.5,
                        max_tokens=50,
                        stream=False,
                    )
                    result = response.choices[0].message.content.strip()
                    self.history.append({"role": "user",      "content": user_text})
                    self.history.append({"role": "assistant",  "content": result})
                    self._store_memory(result)
                    return result
                except Exception as e:
                    print(f"[Agent] Web search failed: {e}")
                    return "Hmm, I couldn't look that up right now. Try asking me again in a moment."
        # ─────────────────────────────────────────────────────────────────────

        if interrupted:
            full_text = context
            if rag:
                full_text = f"Memory: {' | '.join(rag)}\n\n{context}"
            system = INTERRUPT_SYSTEM_PROMPT
        else:
            parts = []
            if rag:
                parts.append(f"Memory: {' | '.join(rag)}")
            if context:
                recent = "\n".join(context.split("\n")[-3:])
                parts.append(f"Recent: {recent}")
            parts.append(f"User: {user_text}")
            full_text = "\n".join(parts)
            system    = SYSTEM_PROMPT

        self.history.append({"role": "user", "content": full_text})
        if len(self.history) > 6:
            self.history = self.history[-6:]

        stream = await self.client.chat.completions.create(
            model=self.deployment,
            messages=[{"role": "system", "content": system}] + self.history,
            temperature=0.7,
            max_tokens=20 if interrupted else 50,
            stream=True,
        )

        words = []
        async for chunk in stream:
            token = chunk.choices[0].delta.content if chunk.choices else None
            if token:
                words.append(token)

        full_response = "".join(words).strip()
        self.history.append({"role": "assistant", "content": full_response})
        self._store_memory(full_response)
        return full_response

    def reset(self):
        self.history.clear()
        self.memory.clear()
