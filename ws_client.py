"""WebSocket client that stands in for the assistant's phone client.

Wire protocol:
- Connect to ASSISTANT_WS_URL?token=<ASSISTANT_AUTH_TOKEN>
- Client -> server: BINARY frames of raw PCM16LE mono @ 16000Hz (mic audio)
- Server -> client: BINARY frames of raw PCM16LE mono @ 24000Hz (TTS audio),
  and a text JSON {"type":"tts_done"} once a TTS utterance finishes.
- The server's streaming STT (Deepgram) has its own idle timeout (~12s): if
  the client stops sending ANY audio (not even silence) for ~12s, it closes
  the socket with a 1011 error. So we keep a continuous silence-frame
  keepalive running whenever we're not actively "speaking", like a phone
  with an open mic sitting in a quiet room.
"""
import asyncio
import os
import ssl
import time
import websockets

ASSISTANT_WS_URL = os.environ["ASSISTANT_WS_URL"]
ASSISTANT_AUTH_TOKEN = os.environ["ASSISTANT_AUTH_TOKEN"]

SAMPLE_RATE = 16000
FRAME_MS = 40
FRAME_BYTES = int(SAMPLE_RATE * (FRAME_MS / 1000) * 2)  # 16-bit mono
SILENCE_FRAME = b"\x00" * FRAME_BYTES


class AssistantCall:
    def __init__(self):
        self.ws = None
        self._speaking = asyncio.Event()  # set while real audio is being sent
        self._send_lock = asyncio.Lock()
        self._keepalive_task = None
        self._recv_task = None
        self._closed = False
        self.last_tts_done = 0.0

    async def connect(self):
        ssl_ctx = ssl.create_default_context()
        url = f"{ASSISTANT_WS_URL}?token={ASSISTANT_AUTH_TOKEN}"
        self.ws = await websockets.connect(url, ssl=ssl_ctx, max_size=16 * 1024 * 1024)
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _keepalive_loop(self):
        try:
            while not self._closed:
                if not self._speaking.is_set():
                    async with self._send_lock:
                        await self.ws.send(SILENCE_FRAME)
                await asyncio.sleep(FRAME_MS / 1000)
        except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
            pass

    async def _recv_loop(self):
        try:
            async for msg in self.ws:
                if isinstance(msg, str):
                    if '"tts_done"' in msg:
                        self.last_tts_done = time.time()
        except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
            pass

    async def say(self, pcm_bytes: bytes):
        """Send a real utterance as paced 16kHz PCM16 frames, then let the
        keepalive silence resume (which is what lets the STT's endpointing
        fire speech_final ~2s after we stop)."""
        self._speaking.set()
        try:
            for i in range(0, len(pcm_bytes), FRAME_BYTES):
                chunk = pcm_bytes[i:i + FRAME_BYTES]
                if len(chunk) < FRAME_BYTES:
                    chunk = chunk + b"\x00" * (FRAME_BYTES - len(chunk))
                async with self._send_lock:
                    await self.ws.send(chunk)
                await asyncio.sleep(FRAME_MS / 1000)
        finally:
            self._speaking.clear()

    async def close(self):
        self._closed = True
        for t in (self._keepalive_task, self._recv_task):
            if t:
                t.cancel()
        if self.ws:
            await self.ws.close()
