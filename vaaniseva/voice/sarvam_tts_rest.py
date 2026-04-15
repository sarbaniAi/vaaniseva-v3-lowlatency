"""Sarvam TTS via REST API — bypasses the buggy WebSocket plugin.

The livekit-plugins-sarvam WebSocket TTS declares audio/wav but sends MP3,
causing decode failures. This custom TTS uses Sarvam's REST API which
returns proper base64 WAV audio.

Usage:
    tts = SarvamRestTTS(api_key="...", speaker="anushka")
"""

import base64
import logging

import aiohttp
from livekit.agents import tts, utils

logger = logging.getLogger(__name__)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


class SarvamRestTTS(tts.TTS):
    """Sarvam Bulbul TTS using REST API (non-streaming, returns WAV)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "bulbul:v2",
        target_language_code: str = "hi-IN",
        speaker: str = "anushka",
        pace: float = 1.0,
        pitch: float = 0.0,
        loudness: float = 1.5,
        sample_rate: int = 22050,
        http_session: aiohttp.ClientSession | None = None,
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
        self._pitch = pitch
        self._loudness = loudness
        self._sample_rate = sample_rate
        self._http_session = http_session

    def synthesize(self, text: str, *, conn_options=None) -> "SarvamChunkedStream":
        return SarvamChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class SarvamChunkedStream(tts.ChunkedStream):
    """Single-request TTS that returns WAV audio."""

    def __init__(self, *, tts: SarvamRestTTS, input_text: str, conn_options):
        if conn_options is None:
            from livekit.agents import APIConnectOptions
            conn_options = APIConnectOptions()
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: SarvamRestTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        session = self._tts._http_session or aiohttp.ClientSession()
        own_session = self._tts._http_session is None

        try:
            payload = {
                "inputs": [self._input_text[:500]],
                "target_language_code": self._tts._target_language_code,
                "speaker": self._tts._speaker,
                "model": self._tts._model,
                "pitch": self._tts._pitch,
                "pace": self._tts._pace,
                "loudness": self._tts._loudness,
                "speech_sample_rate": self._tts._sample_rate,
                "enable_preprocessing": True,
            }
            headers = {
                "api-subscription-key": self._tts._api_key,
                "Content-Type": "application/json",
            }

            import time
            t0 = time.time()

            async with session.post(
                SARVAM_TTS_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                t1 = time.time()
                if resp.status != 200:
                    error = await resp.text()
                    logger.error(f"Sarvam TTS error {resp.status}: {error[:200]}")
                    return

                data = await resp.json()
                audios = data.get("audios", [])
                if not audios:
                    logger.warning("Sarvam TTS returned no audio")
                    return

                request_id = data.get("request_id", "sarvam-rest")

                output_emitter.initialize(
                    request_id=request_id,
                    sample_rate=self._tts._sample_rate,
                    num_channels=1,
                    mime_type="audio/wav",
                )

                total_bytes = 0
                for audio_b64 in audios:
                    wav_bytes = base64.b64decode(audio_b64)
                    output_emitter.push(wav_bytes)
                    total_bytes += len(wav_bytes)

                t2 = time.time()
                text_preview = self._input_text[:60].replace('\n', ' ')
                logger.info(
                    f"Sarvam TTS: {t1-t0:.2f}s API + {t2-t1:.2f}s decode "
                    f"= {t2-t0:.2f}s total | {total_bytes/1024:.0f}KB | "
                    f"text: '{text_preview}...'"
                )

        except Exception as e:
            logger.error(f"Sarvam REST TTS error: {e}", exc_info=True)
        finally:
            if own_session:
                await session.close()
