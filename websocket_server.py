"""
websocket_server.py — OPTIMIZED v3 (seamless audio)

Strategy: Parallel TTS → concatenate → single inject
  1. LLM streams sentences into a queue (background task, truly parallel with trigger)
  2. TTS fires on each sentence AS IT ARRIVES (parallel with LLM)
  3. All audio bytes concatenated into ONE continuous MP3
  4. Single inject → zero gap between sentences

Why this works for Sam's 2-sentence responses:
  - LLM full response from Groq: ~400ms
  - TTS sentence 1 starts at ~200ms (while LLM still generating sentence 2)
  - TTS sentence 2 starts at ~400ms (overlaps with sentence 1 TTS)
  - Both TTS done by ~800-1000ms
  - Concatenate + inject: ~500ms
  - TOTAL: ~1.3-1.5s, completely seamless
"""

import asyncio
import json
import time
import base64
import io
from aiohttp import web
import aiohttp
from collections import deque

from Trigger import TriggerDetector
from Agent import PMAgent
from Speaker import CartesiaSpeaker, get_duration_ms


def ts():
    return time.strftime("%H:%M:%S")

def elapsed(since: float) -> str:
    return f"{(time.time() - since)*1000:.0f}ms"

WORDS_PER_SECOND = 3.3
_SENTINEL = object()


class WebSocketServer:
    def __init__(self, port: int = 8000, bot_id: str = None):
        self.port             = port
        self.trigger          = TriggerDetector()
        self.agent            = PMAgent()
        self.speaker          = CartesiaSpeaker(bot_id=bot_id)
        self._speaking        = False
        self._audio_playing   = False
        self._convo_history   = deque(maxlen=8)

        self._current_task:       asyncio.Task | None = None
        self._current_text:       str   = ""
        self._current_speaker:    str   = ""
        self._interrupt_event:    asyncio.Event = asyncio.Event()

        self._generation:   int   = 0

        self._buffer:       list  = []
        self._buffer_task:  asyncio.Task | None = None

        self.app = web.Application()
        self.app.router.add_get("/ws",     self.handle_websocket)
        self.app.router.add_get("/health", self.handle_health)

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "speaking": self._speaking})

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        print(f"[{ts()}] ✅ Recall.ai WebSocket connected")
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_event(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"[{ts()}] ⚠️  WS error: {ws.exception()}")
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                    break
        except Exception as e:
            print(f"[{ts()}] WS handler error: {e}")
        finally:
            print(f"[{ts()}] WebSocket disconnected")
        return ws

    async def _handle_event(self, raw: str):
        t = time.time()
        try:
            payload = json.loads(raw)
        except Exception:
            return

        event = payload.get("event", "")

        if event == "transcript.data":
            inner   = payload.get("data", {}).get("data", {})
            words   = inner.get("words", [])
            text    = " ".join(w.get("text", "") for w in words).strip()
            speaker = inner.get("participant", {}).get("name", "Unknown")
            if not text or speaker.lower() == "sam":
                return

            print(f"\n[{ts()}] [{speaker}] {text}  ⏱ {elapsed(t)}")

            if self._buffer_task and not self._buffer_task.done():
                self._buffer_task.cancel()

            if self._speaking and self._current_speaker == speaker:
                combined = f"{self._current_text} {text}".strip()
                print(f"[{ts()}] 🔄 Combined: \"{combined}\" — restarting")
                if self._current_task and not self._current_task.done():
                    self._current_task.cancel()
                if self._audio_playing:
                    asyncio.create_task(self.speaker.stop_audio())
                self._speaking = False
                self._audio_playing = False
                self._interrupt_event.set()
                await asyncio.sleep(0)
                self._start_process(combined, speaker, t)

            elif self._speaking and self._current_speaker != speaker:
                print(f"[{ts()}] ⚡ INTERRUPT — {speaker} cut in")
                asyncio.create_task(self.speaker.stop_audio())
                self._interrupt_event.set()
                self._start_process(text, speaker, t)

            else:
                self._start_process(text, speaker, t)

        elif event == "participant_events.speech_off":
            speaker = (
                payload.get("data", {}).get("data", {})
                       .get("participant", {}).get("name", "Unknown")
            )
            print(f"[{ts()}] 🔇 {speaker} stopped speaking")
            if self._buffer and not self._speaking:
                full_text = " ".join(txt for _, txt, _ in self._buffer)
                t0        = self._buffer[0][2]
                self._buffer.clear()
                self._start_process(full_text, speaker, t0)
            self._buffer.clear()

        elif event == "participant_events.speech_on":
            speaker = (
                payload.get("data", {}).get("data", {})
                       .get("participant", {}).get("name", "Unknown")
            )
            print(f"[{ts()}] 🎤 {speaker} started speaking")
            if self._speaking and self._current_speaker != speaker:
                print(f"[{ts()}] ⚡ INTERRUPT (speech_on) — {speaker} cut in")
                asyncio.create_task(self.speaker.stop_audio())
                self._interrupt_event.set()

        elif event == "participant_events.join":
            name = (
                payload.get("data", {}).get("data", {})
                       .get("participant", {}).get("name", "Unknown")
            )
            if name and name.lower() != "sam":
                print(f"[{ts()}] 👋 {name} joined")
                asyncio.create_task(self._greet_participant(name, t))

        elif event == "participant_events.leave":
            name = (
                payload.get("data", {}).get("data", {})
                       .get("participant", {}).get("name", "Unknown")
            )
            if name and name.lower() != "sam":
                print(f"[{ts()}] 👋 {name} left")

    def _start_process(self, text: str, speaker: str, t0: float):
        self._generation     += 1
        my_gen                = self._generation
        self._current_text    = text
        self._current_speaker = speaker
        self._interrupt_event.clear()
        task = asyncio.create_task(self._process(text, speaker, t0, my_gen))
        self._current_task = task

    async def _greet_participant(self, name: str, t0: float):
        await asyncio.sleep(2.0)
        if self._speaking:
            return
        greeting = f"Hey {name}, welcome to the call!"
        self._convo_history.append(f"Sam: {greeting}")
        await self._speak_response(greeting, t0)

    # ══════════════════════════════════════════════════════════════════════════
    # LLM producer — feeds sentences into queue (runs as background task)
    # ══════════════════════════════════════════════════════════════════════════

    async def _llm_producer(self, text: str, context: str, queue: asyncio.Queue):
        try:
            async for sentence in self.agent.stream_sentences(text, context):
                await queue.put(sentence)
            await queue.put(_SENTINEL)
        except asyncio.CancelledError:
            await queue.put(_SENTINEL)
        except Exception as e:
            print(f"[{ts()}] ⚠️  LLM producer error: {e}")
            await queue.put(_SENTINEL)

    # ══════════════════════════════════════════════════════════════════════════
    # CORE PIPELINE v3 — parallel TTS, concatenate, single inject, NO GAPS
    # ══════════════════════════════════════════════════════════════════════════

    async def _process(self, text: str, speaker: str, t0: float, generation: int = 0):
        if self._speaking:
            print(f"[{ts()}] ⚠️  Already speaking — dropping")
            return

        self._speaking = True
        self._interrupt_event.clear()
        my_generation = generation

        try:
            context         = "\n".join(self._convo_history)
            memory_snapshot = [m[0] for m in self.agent.memory[-20:]]

            t1 = time.time()

            # ── Fire trigger + LLM in TRUE parallel ───────────────────────────
            sentence_queue = asyncio.Queue()
            llm_task = asyncio.create_task(
                self._llm_producer(text, context, sentence_queue)
            )
            trigger_task = asyncio.create_task(
                self.trigger.should_respond(text, speaker, context, memory_snapshot)
            )

            print(f"[{ts()}] Trigger + LLM fired in parallel...")

            should = await trigger_task
            print(f"[{ts()}] Trigger: {'YES' if should else 'NO'} ({elapsed(t1)})")

            if not should:
                llm_task.cancel()
                return

            # ── Collect sentences + fire TTS in parallel as they arrive ───────
            sentences: list[str] = []
            tts_tasks: list[asyncio.Task] = []  # each produces raw audio bytes

            while True:
                if self._interrupt_event.is_set() or my_generation != self._generation:
                    print(f"[{ts()}] ⚡ Superseded — aborting")
                    llm_task.cancel()
                    for t_task in tts_tasks:
                        t_task.cancel()
                    return

                try:
                    item = await asyncio.wait_for(sentence_queue.get(), timeout=12.0)
                except asyncio.TimeoutError:
                    print(f"[{ts()}] ⚠️  LLM queue timeout")
                    break

                if item is _SENTINEL:
                    break

                sentence = item
                sentences.append(sentence)
                idx = len(sentences)
                print(f"[{ts()}] LLM sentence {idx} ({elapsed(t1)}): \"{sentence}\"")

                # Fire TTS immediately — runs in parallel with LLM generating next sentence
                tts_task = asyncio.create_task(
                    self.speaker._synthesise(sentence)
                )
                tts_tasks.append(tts_task)
                print(f"[{ts()}] ⏩ TTS {idx} fired")

            if not sentences:
                return

            # ── Await all TTS results ─────────────────────────────────────────
            # Most/all should already be done since they ran during LLM streaming
            t_tts = time.time()
            audio_chunks: list[bytes] = []
            for i, task in enumerate(tts_tasks):
                try:
                    audio_bytes = await asyncio.wait_for(task, timeout=10.0)
                    audio_chunks.append(audio_bytes)
                except Exception as e:
                    print(f"[{ts()}] ⚠️  TTS {i+1} failed: {e}")

            tts_ms = (time.time() - t_tts) * 1000
            print(f"[{ts()}] All TTS done (waited {tts_ms:.0f}ms for remaining)")

            if not audio_chunks:
                print(f"[{ts()}] ⚠️  No audio produced")
                return

            # Check interrupt before combining
            if self._interrupt_event.is_set() or my_generation != self._generation:
                print(f"[{ts()}] ⚡ Superseded after TTS — skipping")
                return

            # ── Concatenate all audio into ONE seamless MP3 ───────────────────
            t_concat = time.time()
            if len(audio_chunks) == 1:
                combined_bytes = audio_chunks[0]
            else:
                # MP3 frames are self-contained — raw concatenation works for speech
                # This avoids pydub overhead (~100-200ms)
                combined_bytes = b"".join(audio_chunks)
            
            combined_b64 = base64.b64encode(combined_bytes).decode("utf-8")
            full_response = " ".join(sentences)
            word_count = len(full_response.split())
            audio_duration_ms = max(500, word_count * 300)  # ~3.3 words/sec
            concat_ms = (time.time() - t_concat) * 1000

            # ── Single inject — no gaps! ──────────────────────────────────────
            if self._interrupt_event.is_set() or my_generation != self._generation:
                print(f"[{ts()}] ⚡ Superseded before inject — skipping")
                return

            t_inj = time.time()
            await self.speaker._inject_into_meeting(combined_b64)
            self._audio_playing = True
            inject_ms = (time.time() - t_inj) * 1000

            print(f"[{ts()}] 🔊 AUDIO | {len(sentences)} sentences | TTS {tts_ms:.0f}ms | Concat {concat_ms:.0f}ms | Inject {inject_ms:.0f}ms | TOTAL {elapsed(t0)}")

            # ── Interruptible playback lock ────────────────────────────────────
            try:
                await asyncio.wait_for(
                    self._interrupt_event.wait(),
                    timeout=audio_duration_ms / 1000,
                )
                print(f"[{ts()}] ⚡ Interrupted during playback")
                self._convo_history.append(f"Sam: {full_response} [interrupted]")
                self.trigger.mark_responded()
                return
            except asyncio.TimeoutError:
                pass

            self._audio_playing = False
            self._convo_history.append(f"Sam: {full_response}")
            self.trigger.mark_responded()
            print(f"[{ts()}] ✅ Done | TOTAL {elapsed(t0)}")

        except asyncio.CancelledError:
            print(f"[{ts()}] 🔄 Task cancelled")
        except Exception as e:
            import traceback
            print(f"[{ts()}] ❌ _process error: {e}")
            traceback.print_exc()
        finally:
            self._audio_playing = False
            self._speaking      = False

    # ── Greeting (simple path) ────────────────────────────────────────────────

    async def _speak_response(self, text: str, t0: float):
        if self._speaking:
            return
        self._speaking = True
        try:
            b64, duration_ms = await self.speaker.synthesise_and_encode(text)
            await self.speaker._inject_into_meeting(b64)
            self._interrupt_event.clear()
            try:
                await asyncio.wait_for(
                    self._interrupt_event.wait(),
                    timeout=duration_ms / 1000,
                )
            except asyncio.TimeoutError:
                pass
            print(f"[{ts()}] _speak_response: done ({elapsed(t0)})")
        except Exception as e:
            import traceback
            print(f"[{ts()}] ⚠️  _speak_response error: {e}")
            traceback.print_exc()
        finally:
            self._speaking = False

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()
        print(f"[{ts()}] WebSocket server ready on ws://0.0.0.0:{self.port}/ws")
        print(f"[{ts()}] Health check: http://localhost:{self.port}/health\n")