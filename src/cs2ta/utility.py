"""Observed grenade flights and event endpoints, with explicitly schematic ranges."""

import math

import polars as pl

from cs2ta.frames import missing

PROJECTILES = {
    "CSmokeGrenadeProjectile": "smoke", "CHEGrenadeProjectile": "he",
    "CFlashbangProjectile": "flash", "CMolotovProjectile": "fire",
    "CDecoyProjectile": "decoy",
}
DETONATIONS = {
    "smokegrenade_detonate": "smoke", "hegrenade_detonate": "he",
    "flashbang_detonate": "flash", "inferno_startburn": "fire",
    "decoy_started": "decoy",
}
EXPIRATIONS = {"smoke": "smokegrenade_expired", "fire": "inferno_expire"}
# World units. These are diagram guides, never a damage/visibility simulation.
RANGES = {"smoke": 144, "he": 350, "flash": 500, "fire": 180, "decoy": 0}


def number(value):
    if missing(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def identity(value):
    if missing(value) or str(value) in {"", "0"}:
        return None
    if isinstance(value, (int, float)):
        return str(int(value))
    return str(value)


def utility_payload(tracks, events, start, end, anchor, radar=None):
    """Never infer a landing from the final sample or join unrelated throws."""
    def point(row):
        xyz = [number(row.get(c)) for c in ("x", "y", "z")]
        if xyz[0] is None or xyz[1] is None:
            return None
        xy = radar.project(*xyz[:2]) if radar else (xyz[0], -xyz[1])
        return [round(float(xy[0]), 3), round(float(xy[1]), 3), xyz[2]]

    flights = []
    if tracks is not None and not tracks.is_empty():
        subset = tracks.filter(pl.col("tick").is_between(start, end))
        for frame in subset.partition_by(["grenade_entity_id", "grenade_type", "steamid"], maintain_order=True):
            first = frame.row(0, named=True)
            entity, kind, owner = (first[name] for name in ("grenade_entity_id", "grenade_type", "steamid"))
            if kind not in PROJECTILES:
                continue
            frame = frame.sort("tick").unique("tick", keep="last", maintain_order=True)
            frame = frame.with_columns((pl.col("tick").diff() > 2).fill_null(False).cum_sum().alias("_segment"))
            for segment in frame.partition_by("_segment", maintain_order=True):
                samples = [[int(row["tick"]) - anchor, *p] for row in segment.iter_rows(named=True)
                           if (p := point(row)) is not None]
                if not samples:
                    continue
                flights.append({"kind": PROJECTILES[kind], "entity": identity(entity),
                                "player": identity(owner), "name": str(segment.row(0, named=True).get("name", "未知玩家")),
                                "samples": samples, "matched": False})
    result = []
    event_rows = [] if events is None or events.is_empty() else events.filter(
        pl.col("tick").is_between(start, end)).sort("tick").to_dicts()
    for event in event_rows:
        kind = DETONATIONS.get(event.get("event_type"))
        if kind is None:
            continue
        tick = int(event["tick"]) - anchor
        p = point(event)
        owner, entity = identity(event.get("user_steamid")), identity(event.get("entityid"))
        candidates = []
        for flight in flights:
            if flight["matched"] or flight["kind"] != kind or flight["samples"][0][0] > tick:
                continue
            if owner and flight["player"] and owner != flight["player"]:
                continue
            last = flight["samples"][-1]
            if kind != "fire" and entity and entity == flight["entity"] and tick <= last[0] + 8:
                candidates.append((0, flight))
            elif kind == "fire" and owner and owner == flight["player"] and p:
                distance = math.dist(p, last[1:]) if None not in (p + last[1:]) else math.inf
                if 0 <= tick - last[0] <= 128 and distance <= 256:
                    candidates.append((distance + 1, flight))
        flight = min(candidates, key=lambda item: item[0])[1] if candidates else None
        if flight:
            flight["matched"] = True
        samples = [s for s in flight["samples"] if s[0] <= tick] if flight else []
        expiry = None
        for expired in event_rows:
            if (expired.get("event_type") == EXPIRATIONS.get(kind)
                    and entity is not None and identity(expired.get("entityid")) == entity
                    and int(expired["tick"]) > int(event["tick"])):
                expiry = int(expired["tick"]) - anchor
                break
        label = event.get("user_name")
        name = str(label) if not missing(label) and str(label).strip() else (flight["name"] if flight else "投掷者未知")
        result.append({"kind": kind, "player": owner or (flight["player"] if flight else None),
                       "name": name,
                       "samples": samples, "start": samples[0][0] if samples else tick,
                       "effectTick": tick, "endTick": expiry, "point": p, "confirmed": True,
                       "radius": RANGES[kind] / (radar.scale if radar else 1)})
    for flight in flights:
        if not flight["matched"]:
            samples = flight["samples"]
            result.append({"kind": flight["kind"], "player": flight["player"], "name": flight["name"],
                           "samples": samples, "start": samples[0][0], "effectTick": None,
                           "endTick": None, "point": samples[-1][1:], "confirmed": False, "radius": 0})
    return sorted(result, key=lambda item: item["start"])
