"""Smoke-test simulation: a data pulse, a link failure, a timeout and a failover.

Node A sends packet 1 to node B over the primary link P. P is cut while the packet is in
flight, so the packet is lost. A's retransmission timer fires, A marks P down and resends
the same payload as packet 2 over the secondary link S. B receives it and acknowledges
over S.

This is a simplified simulation (evidence class `simulated`), not a model of any
particular protocol. Link latencies carry small seeded jitter, so the seed matters.
"""

from __future__ import annotations

from fractions import Fraction

from ostrium_kinetic.sim import Outcome, SimContext

TICK_RATE = Fraction(1, 1000)  # 1 tick = 1 ms


def run(ctx: SimContext) -> None:
    p = ctx.params
    world, rec = ctx.world, ctx.rec
    ms = world.ticks  # exact seconds -> ticks

    latency_p = ms(p["latency_p_s"]) + ctx.rng.int_between(
        "jitter.P", -p["jitter_ms"], p["jitter_ms"]
    )
    latency_s = ms(p["latency_s_s"]) + ctx.rng.int_between(
        "jitter.S", -p["jitter_ms"], p["jitter_ms"]
    )
    cut_at = ms(p["cut_p_at_s"])
    timeout = ms(p["timeout_s"])
    reroute_delay = ms(p["reroute_delay_s"])
    ack_latency = ms(p["ack_latency_s"])  # acks are small; modelled as a shorter transit
    end_at = ms(p["end_s"])

    rec.entity_type("node", role="str")
    rec.entity_type("link", status="str", latency_ms="int")
    rec.entity_type("packet", status="str", payload="str")

    rec.create("node:A", role="sender")
    rec.create("node:B", role="receiver")
    rec.create("link:P", status="up", latency_ms=latency_p)
    rec.create("link:S", status="standby", latency_ms=latency_s)

    def send(packet: str, link: str, src: str, dst: str, payload: str, latency: int) -> None:
        rec.create(packet, status="in_flight", payload=payload)
        rec.begin(f"tx:{packet}", packet, "transit", attrs={"from": src, "to": dst, "via": link})
        world.after(latency, arrive, packet, link, dst)

    def arrive(packet: str, link: str, dst: str) -> None:
        if not rec.is_open(f"tx:{packet}"):
            return  # already lost on a failed link
        rec.end(f"tx:{packet}", Outcome.COMPLETED)
        rec.set(packet, status="delivered")
        if packet == "packet:2":
            send("packet:ack2", "link:S", "node:B", "node:A", "ack", ack_latency)
        elif packet == "packet:ack2":
            rec.signal("ack.received", "node:A", packet="packet:2")

    def cut_primary() -> None:
        rec.set("link:P", status="failed")
        rec.signal("fault.injected", "link:P", fault="cut")
        if rec.is_open("tx:packet:1"):
            rec.end("tx:packet:1", Outcome.LOST)
            rec.set("packet:1", status="lost")

    def on_timeout() -> None:
        rec.signal("timeout.fired", "node:A", packet="packet:1")
        world.after(reroute_delay, failover)

    def failover() -> None:
        rec.set("link:P", status="down")
        rec.set("link:S", status="up")
        rec.signal("route.changed", "node:A", via="link:S")
        send("packet:2", "link:S", "node:A", "node:B", "payload-1", latency_s)

    world.at(0, send, "packet:1", "link:P", "node:A", "node:B", "payload-1", latency_p)
    world.at(cut_at, cut_primary)
    world.at(timeout, on_timeout)
    world.run(until=end_at)
