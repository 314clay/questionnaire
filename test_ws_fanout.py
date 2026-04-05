"""Test WebSocket audio fan-out: binary frames sent by one client are relayed to peers."""

import asyncio
import json
import sys
import time
import urllib.request
import urllib.error

import websockets

BASE = "http://localhost:3050"
WS_BASE = "ws://localhost:3050"


def api(method, path, data=None):
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_for_server(timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            api("GET", "/api/state")
            return True
        except Exception:
            time.sleep(0.3)
    return False


async def test_fanout_binary_frames():
    """Producer sends binary audio frames; consumer peer should receive them."""
    status, data = api("POST", "/api/create", {
        "type": "live-stream",
        "payload": {"question": "Fan-out test"},
        "id": "test-fanout",
    })
    assert status == 201, f"Failed to create questionnaire: {status} {data}"
    qid = data["id"]

    received_frames = []

    try:
        # Connect two WS clients: producer (simulates browser) and consumer (simulates whisper-transcriber)
        async with websockets.connect(f"{WS_BASE}/ws/{qid}") as producer, \
                   websockets.connect(f"{WS_BASE}/ws/{qid}") as consumer:

            # Both send session_start
            await producer.send(json.dumps({
                "type": "session_start",
                "port": qid,
                "mime_type": "audio/webm",
            }))
            await consumer.send(json.dumps({
                "type": "session_start",
                "port": qid,
                "mime_type": "audio/webm",
            }))

            # Read ack messages from both
            ack1 = json.loads(await asyncio.wait_for(producer.recv(), timeout=3))
            assert ack1["type"] == "ack", f"Expected ack, got {ack1}"
            ack2 = json.loads(await asyncio.wait_for(consumer.recv(), timeout=3))
            assert ack2["type"] == "ack", f"Expected ack, got {ack2}"

            # Producer sends 3 binary frames (simulated audio chunks)
            test_chunks = [b"\x00" * 100, b"\xff" * 200, b"\xab\xcd" * 50]
            for chunk in test_chunks:
                await producer.send(chunk)

            # Small delay for fan-out processing
            await asyncio.sleep(0.3)

            # Consumer should receive exactly those 3 binary frames
            for i, expected in enumerate(test_chunks):
                try:
                    frame = await asyncio.wait_for(consumer.recv(), timeout=3)
                    assert isinstance(frame, bytes), f"Frame {i}: expected bytes, got {type(frame)}"
                    assert frame == expected, f"Frame {i}: content mismatch ({len(frame)} vs {len(expected)} bytes)"
                    received_frames.append(frame)
                except asyncio.TimeoutError:
                    raise AssertionError(f"Frame {i}: timed out waiting for fan-out delivery")

            # Producer should NOT receive its own frames back
            try:
                extra = await asyncio.wait_for(producer.recv(), timeout=0.5)
                # If we get something, it should be text (pong, etc.), not our binary back
                if isinstance(extra, bytes):
                    raise AssertionError("Producer received its own binary frame back (echo)")
            except asyncio.TimeoutError:
                pass  # Good — no echo

        print(f"  fan-out: {len(received_frames)} binary frames relayed correctly")

    finally:
        api("DELETE", f"/api/questionnaire/{qid}")


async def test_fanout_multiple_consumers():
    """Multiple consumers all receive the same binary frames."""
    status, data = api("POST", "/api/create", {
        "type": "live-stream",
        "payload": {"question": "Multi-consumer test"},
        "id": "test-fanout-multi",
    })
    assert status == 201
    qid = data["id"]

    try:
        async with websockets.connect(f"{WS_BASE}/ws/{qid}") as producer, \
                   websockets.connect(f"{WS_BASE}/ws/{qid}") as consumer1, \
                   websockets.connect(f"{WS_BASE}/ws/{qid}") as consumer2:

            # All send session_start and consume acks
            for ws in [producer, consumer1, consumer2]:
                await ws.send(json.dumps({"type": "session_start", "port": qid}))
                ack = await asyncio.wait_for(ws.recv(), timeout=3)

            # Producer sends a binary frame
            payload = b"\xde\xad\xbe\xef" * 25
            await producer.send(payload)
            await asyncio.sleep(0.3)

            # Both consumers should receive it
            for name, consumer in [("consumer1", consumer1), ("consumer2", consumer2)]:
                frame = await asyncio.wait_for(consumer.recv(), timeout=3)
                assert isinstance(frame, bytes), f"{name}: expected bytes"
                assert frame == payload, f"{name}: content mismatch"

        print("  fan-out: multiple consumers all received frames")

    finally:
        api("DELETE", f"/api/questionnaire/{qid}")


async def test_fanout_no_relay_of_text():
    """Text messages (JSON) should NOT be relayed to peers."""
    status, data = api("POST", "/api/create", {
        "type": "live-stream",
        "payload": {"question": "Text relay test"},
        "id": "test-fanout-text",
    })
    assert status == 201
    qid = data["id"]

    try:
        async with websockets.connect(f"{WS_BASE}/ws/{qid}") as producer, \
                   websockets.connect(f"{WS_BASE}/ws/{qid}") as consumer:

            # session_start + acks
            for ws in [producer, consumer]:
                await ws.send(json.dumps({"type": "session_start", "port": qid}))
                await asyncio.wait_for(ws.recv(), timeout=3)

            # Producer sends a ping (text message)
            await producer.send(json.dumps({"type": "ping"}))
            await asyncio.sleep(0.3)

            # Consumer should NOT receive the ping — only binary frames are fanned out
            try:
                msg = await asyncio.wait_for(consumer.recv(), timeout=1)
                # If we get a pong, that's the server responding to producer, not relay
                if isinstance(msg, str):
                    parsed = json.loads(msg)
                    assert parsed.get("type") != "ping", "Text messages should not be relayed"
            except asyncio.TimeoutError:
                pass  # Correct — no text relay

        print("  fan-out: text messages not relayed to peers")

    finally:
        api("DELETE", f"/api/questionnaire/{qid}")


if __name__ == "__main__":
    if not wait_for_server():
        print("Server not running at localhost:3050")
        sys.exit(1)

    print("Testing WebSocket audio fan-out...")
    tests = [
        test_fanout_binary_frames,
        test_fanout_multiple_consumers,
        test_fanout_no_relay_of_text,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            asyncio.run(test())
            passed += 1
        except Exception as e:
            print(f"  FAIL {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
