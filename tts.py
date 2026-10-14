"""Synthesize persona speech as raw PCM16 @ 16kHz mono (the assistant's STT input format)."""
import os
import httpx

CARTESIA_KEY = os.environ["CARTESIA_KEY"]
# A different voice than the assistant's own, so the caller is audibly a different speaker.
PERSONA_VOICE = os.environ.get("PERSONA_VOICE", "a0e99841-438c-4a64-b679-ae501e7d6091")


async def synthesize_pcm16(text: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={
                "X-API-Key": CARTESIA_KEY,
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json",
            },
            json={
                "model_id": "sonic-2",
                "transcript": text,
                "voice": {"mode": "id", "id": PERSONA_VOICE},
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": 16000,
                },
                "language": "en",
            },
        )
        resp.raise_for_status()
        return resp.content
