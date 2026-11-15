"""Live agent skill autonomy test runner.

Tests whether the live running agent autonomously discovers and invokes skills
via list_agent_skills and activate_agent_skill without explicit user prompts.
"""

from __future__ import annotations

import httpx
import json
import time
import functools
print = functools.partial(print, flush=True)

BASE_URL = "http://127.0.0.1:8000"
DIAGNOSTICS_URL = "http://127.0.0.1:3010"
CHAT_API_KEY = "931a4b22b1403af97688211012170b332fc78ec21ccde08d8af3eee783007570"

TEST_CASES = [
    {
        "name": "1C Query Optimization (Autonomous Discovery)",
        "message": (
            "Как в 1С правильно составить и оптимизировать запрос к виртуальной таблице "
            "остатков регистра накопления с соединением по номенклатуре, "
            "чтобы избежать проблем с производительностью на больших объемах данных?"
        ),
        "expected_skill": "1c-query-opt",
        "expected_keywords": ["параметр", "виртуальн", "временн", "соединени"],
    },
]


def run_test():
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": CHAT_API_KEY,
    }

    print("==================================================")
    print("  Live Agent Autonomous Skill Usage Test")
    print("==================================================")
    print(f"Agent URL: {BASE_URL}")

    # Check health
    with httpx.Client(timeout=10.0) as client:
        try:
            h = client.get(f"{BASE_URL}/health")
            print(f"Agent Health: {h.status_code} -> {h.json()}")
        except Exception as e:
            print(f"Failed to connect to agent: {e}")
            return False

    for idx, test in enumerate(TEST_CASES, start=1):
        print(f"\n[Test Case {idx}]: {test['name']}")
        print(f"User Message: \"{test['message']}\"")
        print("Note: User message DOES NOT mention skills or activation tools.")

        payload = {
            "message": test["message"],
            "user_context": {
                "role": "programmer",
                "departments": ["it", "development"],
            },
        }

        start_time = time.time()
        with httpx.Client(timeout=180.0) as client:
            try:
                print("Sending request to live agent (waiting for completion)...")
                resp = client.post(f"{BASE_URL}/api/v1/chat", headers=headers, json=payload)
                elapsed = time.time() - start_time
                print(f"Response status: {resp.status_code} in {elapsed:.2f}s")
                
                if resp.status_code != 200:
                    print(f"Error: {resp.text}")
                    continue

                data = resp.json()
                reply = data.get("reply", "")
                thread_id = data.get("thread_id", "")
                print(f"Thread ID: {thread_id}")
                print(f"\n--- Agent Response Preview (first 600 chars) ---\n{reply[:600]}...\n------------------------------------------------")

                # Verify keywords from skill
                found_kw = [kw for kw in test["expected_keywords"] if kw.lower() in reply.lower()]
                print(f"Domain keywords matched: {found_kw} / {test['expected_keywords']}")

            except Exception as e:
                print(f"Request failed: {e}")

    # Check latest trace from Diagnostics
    try:
        with httpx.Client(timeout=10.0) as client:
            tr_resp = client.get(f"{DIAGNOSTICS_URL}/api/traces?limit=3")
            if tr_resp.status_code == 200:
                traces = tr_resp.json().get("items", [])
                print(f"\nDiagnostics recent traces found: {len(traces)}")
                for t in traces[:2]:
                    print(f"  Trace ID: {t.get('id')} | Agent: {t.get('agent')} | Question: {t.get('question')[:60]}... | Model calls: {t.get('model_calls')}")
    except Exception as e:
        print(f"Diagnostics query note: {e}")


if __name__ == "__main__":
    run_test()
