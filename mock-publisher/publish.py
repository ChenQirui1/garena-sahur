#!/usr/bin/env python3
"""Mock upstream publisher for Spotlight.

Stands in for Ivan's Minecraft / pub-sub client and feeds the downstream
Python backend the four upstream topics it needs (npc.profile, world.snapshot,
game.event, conversation.turn) for a coherent market-theft scenario.

Stdlib only — no broker, no third-party deps.

Examples
--------
    # 10-NPC demo streamed as JSONL to the terminal
    python publish.py

    # 50 NPCs, faster, written to a replayable file
    python publish.py --npcs 50 --rate 20 --out run.jsonl

    # emit as fast as possible (no real-time pacing) into a test/backend
    python publish.py --no-sleep --out http://localhost:8000/ingest

    # quick preview of the message shapes
    python publish.py --duration 2 --rate 2
"""

from __future__ import annotations

import argparse
import sys
import time

import contracts
from scenario import MarketTheftScenario
from sinks import build_sink


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--npcs", type=int, default=10, help="crowd size (default: 10; benchmark points 10/25/50/100)")
    p.add_argument("--rate", type=float, default=5.0, help="world snapshots per second (default: 5)")
    p.add_argument("--duration", type=float, default=20.0, help="scenario length in seconds (default: 20)")
    p.add_argument("--session", default="demo-01", help="session_id stamped on snapshots")
    p.add_argument("--seed", type=int, default=7, help="RNG seed for a repeatable stream")
    p.add_argument("--out", default="-", help="'-' stdout (default), a file path (JSONL), or an http(s):// endpoint")
    p.add_argument("--no-sleep", action="store_true", help="emit as fast as possible (ignore --rate pacing)")
    p.add_argument("--epoch-ms", type=int, default=None, help="pin the base timestamp for a fully byte-identical stream (default: wall clock)")
    return p.parse_args(argv)


def run(args):
    total_ticks = max(1, int(args.duration * args.rate))
    dt = 1.0 / args.rate
    scenario = MarketTheftScenario(npcs=args.npcs, seed=args.seed, session_id=args.session)
    sink = build_sink(args.out)

    counts = {topic: 0 for topic in contracts.UPSTREAM_TOPICS}

    def emit(topic, message):
        sink.publish(topic, contracts.validate(message))
        counts[topic] += 1

    try:
        # Startup: publish every NPC profile once.
        for profile in scenario.profiles():
            emit(contracts.TOPIC_PROFILE, profile)

        base_ms = args.epoch_ms if args.epoch_ms is not None else int(time.time() * 1000)
        for tick in range(total_ticks):
            sequence = 1000 + tick
            t_ms = base_ms + int(tick * dt * 1000)

            emit(contracts.TOPIC_SNAPSHOT, scenario.snapshot(tick, sequence, t_ms))
            for topic, message in scenario.scripted(tick, total_ticks):
                emit(topic, message)

            if not args.no_sleep and tick < total_ticks - 1:
                time.sleep(dt)
    finally:
        sink.close()

    return counts


def main(argv=None):
    args = parse_args(argv)
    counts = run(args)
    total = sum(counts.values())
    # Summary to stderr so it never contaminates a JSONL stdout stream.
    summary = ", ".join(f"{t}={counts[t]}" for t in contracts.UPSTREAM_TOPICS)
    print(f"published {total} messages ({summary})", file=sys.stderr)


if __name__ == "__main__":
    main()
