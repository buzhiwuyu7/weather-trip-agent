"""Independent skill: query local SQLite context, with OSM discovery for other cities."""
import json
import sqlite3
from urllib.parse import urlencode

from weather import catalog, fetch_json, known_city, now_iso


def local_places(city_name):
    city = known_city(city_name)
    if not city:
        return []
    # A bound parameter is used; user/model text never becomes SQL source.
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE attractions (city TEXT, id TEXT, environment TEXT, payload TEXT)")
        for record in catalog()["cities"]:
            connection.executemany("INSERT INTO attractions VALUES (?, ?, ?, ?)",
                                   [(record["name"], p["id"], p["environment"], json.dumps(p, ensure_ascii=False)) for p in record["places"]])
        rows = connection.execute("SELECT payload FROM attractions WHERE city = ? ORDER BY id", (city["name"],)).fetchall()
    return [json.loads(row[0]) | {"source_type": "curated_catalog", "catalog_version": catalog()["catalog_version"]} for row in rows]


def osm_places(city, fetch=fetch_json):
    lat, lon = float(city["latitude"]), float(city["longitude"])
    query = ('[out:json][timeout:18];('
             'nwr(around:15000,%s,%s)[tourism~"^(museum|gallery|viewpoint|attraction)$"][name];'
             'nwr(around:15000,%s,%s)[leisure=park][name];);out tags center 35;' % (lat, lon, lat, lon))
    data = fetch("https://overpass-api.de/api/interpreter", data=urlencode({"data": query}).encode(),
                 headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=25)
    places, seen = [], set()
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name:zh") or tags.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        kind = tags.get("tourism", tags.get("leisure", "attraction"))
        environment = "indoor" if kind in {"museum", "gallery"} else "outdoor" if kind in {"park", "viewpoint"} else "mixed"
        element_type = element.get("type")
        if element_type not in {"node", "way", "relation"}:
            continue
        places.append({"id": "osm-%s-%s" % (element_type, element["id"]), "name": name[:160],
                       "category": {"museum": "博物馆", "gallery": "艺术展馆", "park": "公园", "viewpoint": "观景点"}.get(kind, "景点"),
                       "environment": environment, "description": "OpenStreetMap 标注的城市附近景点。",
                       "caution": "室内外按地图类别推断，需核实；位于所选城市中心15公里范围，不保证属于同一行政区。",
                       "source_type": "openstreetmap_live", "source_url": "https://www.openstreetmap.org/%s/%s" % (element_type, element["id"])})
    return places


def rank_places(places, assessment, preference="均衡", count=4):
    eligible, excluded = [], []
    for original in places:
        place = dict(original)
        env = place["environment"]
        if assessment["level"] == "postpone":
            excluded.append(place | {"exclusion_reason": "天气风险较高，本次不安排出门游玩"})
            continue
        if assessment["level"] == "indoor" and env != "indoor":
            excluded.append(place | {"exclusion_reason": "涉及露天活动，当前天气优先选择室内"})
            continue
        score = {"indoor": 80, "mixed": 65, "outdoor": 75}[env]
        if assessment["level"] == "indoor":
            score = 95
        if preference == "室内文化" and env == "indoor":
            score += 10
        if preference == "户外风景" and env == "outdoor":
            score += 15
        if preference == "亲子轻松" and any(w in place["category"] for w in ("科学", "亲子", "博物馆")):
            score += 10
        place["match_score"] = min(score, 100)
        place["fit_reason"] = "主要活动在室内，可减少天气暴露；途中和排队仍需留意。" if env == "indoor" else "当前日级指标允许考虑短时户外；出发前确认临近天气。"
        eligible.append(place)
    eligible.sort(key=lambda p: (-p["match_score"], p["id"]))
    return eligible[:count], excluded


def search_attractions(city, weather, preference="均衡", *, fetch=fetch_json):
    places = local_places(city["name"])
    warnings = []
    source_mode = "本地景点数据库"
    if not places and weather["assessment"]["level"] != "postpone":
        source_mode = "OpenStreetMap 在线查询"
        try:
            places = osm_places(city, fetch)
        except Exception:
            warnings.append("在线景点服务暂不可用，无法核实该城市的具体景点；不以模型记忆补造推荐。")
    eligible, excluded = rank_places(places, weather["assessment"], preference)
    if not eligible and weather["assessment"]["level"] != "postpone":
        warnings.append("当前数据源中没有满足天气条件的景点，可稍后重试或选择已覆盖城市。")
    if source_mode == "OpenStreetMap 在线查询":
        warnings.append("地图数据来源：© OpenStreetMap contributors；类别与位置可能不完整，请核实。")
    return {"city": city["name"], "source_mode": source_mode, "retrieved_at": now_iso(),
            "preference": preference, "eligible": eligible, "excluded": excluded,
            "warnings": warnings, "availability_checked": False}
