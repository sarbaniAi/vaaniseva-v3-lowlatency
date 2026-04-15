"""Sarvam Bulbul v3 Streaming TTS for livekit-agents.

Uses the sarvamai SDK's convert_stream() for progressive audio delivery.
Each LLM sentence is streamed to TTS independently — user hears the first
sentence while subsequent sentences are still being synthesized.

Best practices from Bulbul v3 Guide:
- bulbul:v3 model with Priya voice (Tier 1, CER 0.13%)
- PCM/LINEAR16 at 8kHz for telephony
- Devanagari script for Hindi (not Romanized)
- Pace 1.0, Temperature 0.6

Reference: Bulbul v3 Best Practices Guide (Sarvam AI, March 2026)
"""

import asyncio
import base64
import logging
import time

from sarvamai import AsyncSarvamAI
from livekit.agents import tts, utils, tokenize

logger = logging.getLogger(__name__)


class SarvamStreamingTTS(tts.TTS):
    """Sarvam Bulbul v3 streaming TTS using sarvamai SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "bulbul:v3",
        target_language_code: str = "hi-IN",
        speaker: str = "priya",
        pace: float = 1.0,
        temperature: float = 0.6,
        output_format: str = "linear16",
        sample_rate: int = 8000,
        **kwargs,  # ignore extra kwargs like http_session
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._api_key = api_key
        self._model = model
        self._target_language_code = target_language_code
        self._speaker = speaker
        self._pace = pace
        self._temperature = temperature
        self._output_format = output_format
        self._sample_rate = sample_rate
        self._client = AsyncSarvamAI(api_subscription_key=api_key)

    def synthesize(self, text: str, *, conn_options=None) -> "SarvamSDKStream":
        return SarvamSDKStream(tts=self, input_text=text, conn_options=conn_options)


class SarvamSDKStream(tts.ChunkedStream):
    """Streams audio via sarvamai SDK convert_stream()."""

    def __init__(self, *, tts: SarvamStreamingTTS, input_text: str, conn_options):
        if conn_options is None:
            from livekit.agents import APIConnectOptions
            conn_options = APIConnectOptions(timeout=15.0)
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: SarvamStreamingTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        t0 = time.time()
        first_chunk_time = None
        total_bytes = 0
        chunks = 0

        try:
            output_emitter.initialize(
                request_id=utils.shortuuid(),
                sample_rate=self._tts._sample_rate,
                num_channels=1,
                mime_type="audio/pcm",
            )

            stream = self._tts._client.text_to_speech.convert_stream(
                text=self._input_text[:2500],
                target_language_code=self._tts._target_language_code,
                speaker=self._tts._speaker,
                model=self._tts._model,
                pace=self._tts._pace,
                temperature=self._tts._temperature,
                output_audio_codec=self._tts._output_format,
                speech_sample_rate=self._tts._sample_rate,
            )

            async for chunk in stream:
                if chunk and len(chunk) > 0:
                    output_emitter.push(chunk)
                    total_bytes += len(chunk)
                    chunks += 1
                    if first_chunk_time is None:
                        first_chunk_time = time.time()

        except Exception as e:
            logger.error(f"Sarvam TTS streaming error: {e}", exc_info=True)

        t_end = time.time()
        ttfb = (first_chunk_time - t0) if first_chunk_time else 0
        text_preview = self._input_text[:50].replace("\n", " ")
        logger.info(
            f"TTS: TTFB={ttfb:.2f}s total={t_end-t0:.2f}s "
            f"chunks={chunks} {total_bytes/1024:.0f}KB | "
            f"'{text_preview}...'"
        )
