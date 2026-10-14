"""Runs 2 and 3: runs each persona in personas_v2.py through a real live
WebSocket call against the assistant, for MAX_TURNS unscripted turns each.

Usage:
    python orchestrator_v2.py                                   # all four personas (run 2)
    python orchestrator_v2.py consistency_carl numbers_nadia    # a subset (run 3)

This script only RUNS the calls and saves transcripts + ground truth. It
does not judge anything -- see judge.py for automated scoring.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import openrouter_client
import tts
from personas_v2 import PERSONAS, MAX_TURNS
from ws_client import AssistantCall

# The published runs used qwen/qwen3-max, which OpenRouter retired on 2026-10-09.
PERSONA_MODEL = os.environ.get("PERSONA_MODEL", "qwen/qwen3.8-max-0902")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results/latest")
# The assistant's server logs each spoken reply as a [FINAL] (brain) or
# [HAIKU] (fast path) line in its systemd journal; replies are read from there.
BRIDGE_JOURNAL_UNIT = os.environ["BRIDGE_JOURNAL_UNIT"]
WATCHDOG_CONTAINER = os.environ.get("WATCHDOG_CONTAINER", "")
TRAILING_SILENCE_S = 2.5
JOURNAL_POLL_TIMEOUT_S = 65
JOURNAL_POLL_INTERVAL_S = 2
JOURNAL_QUIET_S = 3


def _journal_lines_since(since_iso: str) -> list[str]:
    out = subprocess.run(
        ["journalctl", "-u", BRIDGE_JOURNAL_UNIT, "--since", since_iso, "--no-pager", "-o", "cat"],
        capture_output=True, text=True,
    )
    return out.stdout.splitlines()


async def wait_for_assistant_reply(turn_start_iso: str) -> dict:
    deadline = time.time() + JOURNAL_POLL_TIMEOUT_S
    last_seen_ts = time.time()
    found = None
    seen_lines = set()
    while time.time() < deadline:
        await asyncio.sleep(JOURNAL_POLL_INTERVAL_S)
        for line in _journal_lines_since(turn_start_iso):
            if line in seen_lines:
                continue
            seen_lines.add(line)
            if "[FINAL]" in line:
                found = {"path": "brain", "text": line.split("[FINAL]", 1)[1].strip()}
                last_seen_ts = time.time()
            elif "[HAIKU]" in line:
                found = {"path": "haiku_fast_path", "text": line.split("[HAIKU]", 1)[1].strip()}
                last_seen_ts = time.time()
            elif "brain is offline" in line or "brain session crashed" in line:
                found = {"path": "error", "text": line}
                return found
        if found and (time.time() - last_seen_ts) > JOURNAL_QUIET_S:
            return found
    return found or {"path": "timeout", "text": "(no reply captured within timeout)"}


def capture_ground_truth() -> dict:
    """Real measurements taken right before the run, for the judge to
    cross-check numeric claims against."""
    def run(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15).stdout.strip()
        except Exception as e:
            return f"(error: {e})"

    truth = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "docker_container_count": run("docker ps -q | wc -l"),
        "disk_free_root": run("df -h / | tail -1"),
        "memory_free": run("free -h | grep Mem"),
    }
    if WATCHDOG_CONTAINER:
        truth["watchdog_last_log_line"] = subprocess.run(
            ["docker", "logs", WATCHDOG_CONTAINER, "--tail", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=15,
        ).stdout.strip()
    return truth


async def run_persona(persona: dict) -> dict:
    sid = persona["id"]
    print(f"=== [{sid}] starting call ===", flush=True)
    call = AssistantCall()
    await call.connect()

    transcript = []
    persona_messages = [{"role": "system", "content": persona["system_prompt"]}]

    try:
        line = persona["opening"]
        for turn_idx in range(MAX_TURNS):
            print(f"[{sid}] turn {turn_idx} persona says: {line}", flush=True)
            turn_start_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            pcm = await tts.synthesize_pcm16(line)
            await call.say(pcm)
            await asyncio.sleep(TRAILING_SILENCE_S)

            reply = await wait_for_assistant_reply(turn_start_iso)
            print(f"[{sid}] turn {turn_idx} assistant ({reply['path']}): {reply['text']}", flush=True)

            transcript.append({
                "turn": turn_idx,
                "persona": line,
                "assistant_path": reply["path"],
                "assistant_reply": reply["text"],
            })
            persona_messages.append({"role": "user", "content": f"[Assistant just said]: {reply['text']}"})

            if reply["path"] == "error":
                print(f"[{sid}] bridge error, ending call early", flush=True)
                break

            if turn_idx < MAX_TURNS - 1:
                persona_messages.append({"role": "user", "content": "Give your next short spoken line now."})
                line = await asyncio.to_thread(openrouter_client.chat, PERSONA_MODEL, persona_messages)
                persona_messages.append({"role": "assistant", "content": line})

            await asyncio.sleep(1.5)
    finally:
        await call.close()

    print(f"=== [{sid}] call done ===\n", flush=True)
    return {"persona_id": sid, "opening": persona["opening"], "transcript": transcript}


async def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    wanted = set(sys.argv[1:])
    personas = [p for p in PERSONAS if not wanted or p["id"] in wanted]

    print("Capturing ground truth...", flush=True)
    ground_truth = capture_ground_truth()
    with open(f"{RESULTS_DIR}/ground_truth.json", "w") as f:
        json.dump(ground_truth, f, indent=2)

    all_results = []
    for persona in personas:
        result = await run_persona(persona)
        all_results.append(result)
        with open(f"{RESULTS_DIR}/{persona['id']}.json", "w") as f:
            json.dump(result, f, indent=2)
        await asyncio.sleep(3)

    with open(f"{RESULTS_DIR}/all_transcripts_v2.json", "w") as f:
        json.dump({"ground_truth": ground_truth, "personas": all_results}, f, indent=2)
    print("ALL DONE")


if __name__ == "__main__":
    asyncio.run(main())
