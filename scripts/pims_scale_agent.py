"""Truck-scale agent — runs at the plant, posts weights to PIMS.

Scale indicators publish a line of text over a serial port or a TCP socket
("GROSS 45320 LB  TARE 15100 LB"). This reads that stream and posts each
stable reading to ``POST /api/scale/readings``, where the loadout screen picks
it up. Nothing here decides anything: PIMS treats a reading as a suggestion
until an operator posts a transaction with it.

    # simulate, for trying the flow without a scale
    python scripts/pims_scale_agent.py --url http://127.0.0.1:8080 \\
        --token "$PIMS_TOKEN" --plant-id 1 --source simulate

    # a serial indicator (needs pyserial)
    python scripts/pims_scale_agent.py --source serial --port /dev/ttyUSB0 --baud 9600

    # an indicator that broadcasts over TCP
    python scripts/pims_scale_agent.py --source tcp --host 10.14.2.50 --tcp-port 4001

Run it under systemd or as a Windows service; it retries a failed post and
never drops a reading silently.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from typing import Iterator

DEFAULT_URL = "http://127.0.0.1:8080"


def read_serial(port: str, baud: int) -> Iterator[str]:  # pragma: no cover - hardware
    try:
        import serial
    except ImportError:
        raise SystemExit("pyserial is required for --source serial: pip install pyserial")
    with serial.Serial(port, baud, timeout=2) as stream:
        while True:
            line = stream.readline().decode("ascii", errors="ignore").strip()
            if line:
                yield line


def read_tcp(host: str, port: int) -> Iterator[str]:  # pragma: no cover - hardware
    import socket

    with socket.create_connection((host, port), timeout=10) as sock:
        buffer = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("ascii", errors="ignore").strip()
                if text:
                    yield text


def read_simulated(interval: float) -> Iterator[str]:
    """A weigh-out every `interval` seconds, for trying the flow end to end."""

    rng = random.Random()
    while True:
        tare = rng.randrange(14_000, 16_500, 20)
        gross = tare + rng.randrange(20_000, 48_000, 20)
        yield f"GROSS {gross} LB  TARE {tare} LB"
        time.sleep(interval)


def post(url: str, token: str, payload: dict, retries: int = 4) -> dict | None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/scale/readings",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    delay = 2.0
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
            print(f"! PIMS rejected the reading ({exc.code}): {detail}", file=sys.stderr)
            return None                      # a rejected reading will not improve on retry
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"! post failed (attempt {attempt}/{retries}): {exc}", file=sys.stderr)
            if attempt == retries:
                return None
            time.sleep(delay)
            delay *= 2
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PIMS truck-scale agent")
    parser.add_argument("--url", default=DEFAULT_URL, help="PIMS base URL")
    parser.add_argument("--token", default="", help="API token for a user with txn.post")
    parser.add_argument("--plant-id", type=int, required=True)
    parser.add_argument("--scale-id", default="scale-1")
    parser.add_argument("--trailer", default="", help="trailer on the scale, when known")
    parser.add_argument("--source", choices=["serial", "tcp", "simulate"], default="simulate")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--tcp-port", type=int, default=4001)
    parser.add_argument("--interval", type=float, default=20.0, help="simulate: seconds between weigh-outs")
    parser.add_argument("--once", action="store_true", help="post one reading and exit")
    args = parser.parse_args(argv)

    if args.source == "serial":
        lines = read_serial(args.port, args.baud)
    elif args.source == "tcp":
        lines = read_tcp(args.host, args.tcp_port)
    else:
        lines = read_simulated(args.interval)

    last_line = None
    for line in lines:
        # Indicators repeat the same reading while the truck sits on the scale.
        if line == last_line:
            continue
        last_line = line
        payload = {
            "plant_id": args.plant_id,
            "scale_id": args.scale_id,
            "trailer_number": args.trailer,
            "line": line,
            "source": f"agent:{args.source}",
        }
        result = post(args.url, args.token, payload)
        if result:
            print(
                f"posted reading {result['reading_id']}: "
                f"net {result.get('net_lbs') or result.get('gross_lbs')} lbs"
            )
        if args.once:
            return 0 if result else 1
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
