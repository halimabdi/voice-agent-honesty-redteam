"""Run 1: each scenario in personas.py as a real live WebSocket call against
the assistant: a scripted opening, then one follow-up written by the persona
model in reaction to the assistant's first answer.
"""
import asyncio
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import openrouter_client
import tts
from orchestrator_v2 import PERSONA_MODEL, RESULTS_DIR, TRAILING_SILENCE_S, wait_for_assistant_reply
from personas import SCENARIOS, PERSONA_SYSTEM_PROMPT
from ws_client import AssistantCall


async def run_scenario(scenario: dict) -> dict:
    sid = scenario["id"]
    print(f"=== [{sid}] starting call ===", flush=True)
    call = AssistantCall()
    await call.connect()

    transcript = []
    persona_messages = [
        {"role": "system", "content": PERSONA_SYSTEM_PROMPT.format(attack_focus=scenario["attack_focus"])},
    ]

    try:
        line = scenario["opening"]
        for turn_idx in range(2):
            print(f"[{sid}] persona says: {line}", flush=True)
            turn_start_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            pcm = await tts.synthesize_pcm16(line)
            await call.say(pcm)
            await asyncio.sleep(TRAILING_SILENCE_S)  # let the STT's endpointing fire speech_final

            reply = await wait_for_assistant_reply(turn_start_iso)
            print(f"[{sid}] assistant ({reply['path']}): {reply['text']}", flush=True)

            transcript.append({"turn": turn_idx, "persona": line, "assistant_path": reply["path"], "assistant_reply": reply["text"]})
            persona_messages.append({"role": "user", "content": f"[Assistant just said]: {reply['text']}"})

            if turn_idx == 0:
                # generate the adversarial follow-up
                persona_messages.append({"role": "user", "content": "Give your next short spoken line now."})
                line = openrouter_client.chat(PERSONA_MODEL, persona_messages)
                persona_messages.append({"role": "assistant", "content": line})

            # give the server a moment to settle (it pauses listening while it speaks)
            await asyncio.sleep(1.5)
    finally:
        await call.close()

    print(f"=== [{sid}] call done ===\n", flush=True)
    return {"scenario_id": sid, "opening": scenario["opening"], "attack_focus": scenario["attack_focus"], "transcript": transcript}


async def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    all_results = []
    for scenario in SCENARIOS:
        result = await run_scenario(scenario)
        all_results.append(result)
        with open(f"{RESULTS_DIR}/{scenario['id']}.json", "w") as f:
            json.dump(result, f, indent=2)
        await asyncio.sleep(3)  # let the server settle between calls

    with open(f"{RESULTS_DIR}/all_transcripts.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print("ALL DONE")


if __name__ == "__main__":
    asyncio.run(main())
