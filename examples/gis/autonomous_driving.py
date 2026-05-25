"""GIS 智能驾驶场景示例 — OceanBase 后端。

演示内容：
1. 写入高精地图要素：HD 道路、车道线、交叉路口
2. 写入 ODD 区域（Operational Design Domain）
3. 写入实时道路事件（施工、事故、限速）
4. ODD 边界判断：车辆是否在可运营区域内
5. 自动驾驶决策点附近的上下文召回
6. 证据链：路况事件如何影响决策层知识

运行前提：
- STORAGE_BACKEND=oceanbase（OB >= 4.2.2）
- GEO_ENABLED=true
"""

import os
from dotenv import load_dotenv
load_dotenv()

os.environ.setdefault("STORAGE_BACKEND", "oceanbase")
os.environ.setdefault("GEO_ENABLED", "true")
os.environ.setdefault("EMBEDDING_PROVIDER", "langchain")
os.environ.setdefault("SUMMARIZER_PROVIDER", "llm")
os.environ.setdefault("RETRIEVAL_RERANKER_MODE", "llm")
os.environ.setdefault("GEO_DEFAULT_RADIUS_KM", "0.5")
os.environ.setdefault("GEO_DISTANCE_DECAY_KM", "0.1")
os.environ.setdefault("RETRIEVAL_RECALL_ROUTES", '["phrase","terms","vector","geo"]')

import contextseek as cs
from contextseek.domain.geo import GeoPoint, GeoQuery

SCOPE = "autonomous/demo"
client = cs.ContextSeek.from_settings()

# =============================================================================
# 1. 写入 ODD 区域（限定自动驾驶可运营的地理范围）
# =============================================================================
print("=== 写入 ODD 区域 ===")

# 某园区内部的 ODD 区域（多边形）
client.add(
    content={
        "odd_id": "odd_campus_a",
        "name": "智驾园区A区ODD",
        "max_speed_kmh": 30,
        "weather_constraint": "no_fog",
        "geo": {
            "lat": 39.9800,
            "lon": 116.3000,
            "geo_type": "odd_zone",
            "geo_shape": (
                "POLYGON((39.9750 116.2950, 39.9750 116.3050, "
                "39.9850 116.3050, 39.9850 116.2950, 39.9750 116.2950))"
            ),
        },
    },
    scope=SCOPE,
    tags=["odd_zone", "campus"],
    stage=cs.Stage.knowledge,
    stability=cs.Stability.permanent,
    source="hd_map",
    source_type=cs.SourceType.external_api,
    confidence=0.99,
    check_conflicts=False,
)
print("  写入 ODD 区域：智驾园区A区")

# =============================================================================
# 2. 写入高精地图道路要素
# =============================================================================
print("\n=== 写入高精地图道路要素 ===")

hd_features = [
    {
        "feature_id": "road_001",
        "type": "hd_road",
        "name": "园区主干道（东西向）",
        "speed_limit": 30,
        "lanes": 4,
        "geo": {
            "lat": 39.9800,
            "lon": 116.3000,
            "geo_type": "hd_road",
            "geo_shape": "LINESTRING(39.9800 116.2950, 39.9800 116.3050)",
        },
    },
    {
        "feature_id": "int_001",
        "type": "intersection",
        "name": "园区A路口",
        "traffic_light": True,
        "geo": {"lat": 39.9800, "lon": 116.3000, "geo_type": "intersection"},
    },
    {
        "feature_id": "lane_001",
        "type": "lane",
        "name": "直行车道",
        "lane_type": "straight",
        "geo": {
            "lat": 39.9799,
            "lon": 116.2980,
            "geo_type": "lane",
            "geo_shape": "LINESTRING(39.9799 116.2950, 39.9799 116.3000)",
        },
    },
]

for f in hd_features:
    client.add(
        content=f,
        scope=SCOPE,
        tags=["hd_map", f["type"]],
        stage=cs.Stage.knowledge,
        stability=cs.Stability.stable,
        source=f["name"],
        source_type=cs.SourceType.external_api,
        confidence=0.98,
        check_conflicts=False,
    )
    print(f"  写入要素: {f['name']} ({f['type']})")

# =============================================================================
# 3. 写入实时道路事件
# =============================================================================
print("\n=== 写入实时道路事件 ===")

events = [
    {
        "event_id": "evt_001",
        "type": "construction",
        "description": "道路施工，右侧两车道封闭",
        "severity": "high",
        "geo": {"lat": 39.9802, "lon": 116.2975, "geo_type": "road_event"},
    },
    {
        "event_id": "evt_002",
        "type": "speed_limit_change",
        "description": "临时限速 20km/h",
        "severity": "medium",
        "geo": {"lat": 39.9798, "lon": 116.3010, "geo_type": "road_event"},
    },
    {
        "event_id": "evt_003",
        "type": "accident",
        "description": "轻微碰撞，已移至路边",
        "severity": "low",
        "geo": {"lat": 39.9805, "lon": 116.2990, "geo_type": "road_event"},
    },
]

event_items = []
for e in events:
    item = client.add(
        content=e,
        scope=SCOPE,
        tags=["road_event", e["type"], f"severity_{e['severity']}"],
        stage=cs.Stage.extracted,
        stability=cs.Stability.ephemeral,  # 道路事件短暂有效
        source=e["event_id"],
        source_type=cs.SourceType.external_api,
        confidence=0.85,
        check_conflicts=False,
    )
    event_items.append(item)
    print(f"  写入事件: {e['description']} ({e['type']})")

# =============================================================================
# 4. 写入车辆当前决策点
# =============================================================================
print("\n=== 写入自动驾驶决策点 ===")

vehicle_pos = GeoPoint(lat=39.9801, lon=116.2985)

client.add(
    content={
        "waypoint_id": "wp_current",
        "vehicle_id": "ego_001",
        "heading": 90.0,  # 正东方向
        "speed_kmh": 25.0,
        "confidence": 0.97,
        "geo": {"lat": vehicle_pos.lat, "lon": vehicle_pos.lon, "geo_type": "waypoint"},
    },
    scope=SCOPE,
    tags=["waypoint", "ego_vehicle"],
    stage=cs.Stage.raw,
    stability=cs.Stability.ephemeral,
    source="ego_vehicle",
    source_type=cs.SourceType.agent_inference,
    confidence=0.97,
    check_conflicts=False,
)
print(f"  写入车辆位置 @ ({vehicle_pos.lat}, {vehicle_pos.lon})")

# =============================================================================
# 5. ODD 边界检测
# =============================================================================
print("\n=== ODD 边界检测 ===")

within_odd = client.adapter.is_point_within_zone(vehicle_pos, zone_type="odd_zone", scope=SCOPE)
print(f"  车辆位置 ({vehicle_pos.lat}, {vehicle_pos.lon})")
print(f"  ODD 状态: {'✓ 在 ODD 范围内，可激活自动驾驶' if within_odd else '✗ 超出 ODD 范围，退出自动驾驶'}")

outside_pos = GeoPoint(lat=39.9700, lon=116.3200)
outside_odd = client.adapter.is_point_within_zone(outside_pos, zone_type="odd_zone", scope=SCOPE)
print(f"  测试点 ({outside_pos.lat}, {outside_pos.lon}): {'在 ODD 内' if outside_odd else '在 ODD 外'}")

# =============================================================================
# 6. 前方上下文召回：查询车辆前方 200m 内的道路信息
# =============================================================================
print("\n=== 前方上下文召回（200m 范围） ===")

geo_ahead = GeoQuery(center=vehicle_pos, radius_km=0.2)

hits = client.retrieve(
    query="道路施工 限速 交叉口 车道",
    scope=SCOPE,
    k=8,
    full=True,
    geo_query=geo_ahead,
)

print(f"  前方感知到 {len(hits)} 个上下文要素：")
for h in hits:
    content = h.item.content if isinstance(h.item.content, dict) else {}
    geo = content.get("geo", {})
    name = content.get("name") or content.get("description") or content.get("type", "?")
    geo_type = geo.get("geo_type", "?")
    tags = ", ".join(h.item.tags[:3])
    print(f"    [{h.score:.3f}] [{geo_type}] {name} | 标签: {tags}")

# =============================================================================
# 7. 高危事件专项召回
# =============================================================================
print("\n=== 高危事件召回 ===")

geo_local = GeoQuery(center=vehicle_pos, radius_km=0.5, geo_type_filter=["road_event"])

hits = client.retrieve(
    query="施工 事故 限速",
    scope=SCOPE,
    k=5,
    full=True,
    tags=["road_event"],
    geo_query=geo_local,
)

print(f"  周边道路事件（{len(hits)} 个）：")
for h in hits:
    content = h.item.content if isinstance(h.item.content, dict) else {}
    severity = content.get("severity", "?")
    desc = content.get("description", "?")
    icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")
    print(f"    [{h.score:.3f}] {icon} [{severity}] {desc}")

# =============================================================================
# 8. 证据链演示：道路事件 → 决策知识
# =============================================================================
print("\n=== 证据链：从路况事件生成驾驶决策建议 ===")

# 如果前方有高危施工，生成决策知识并链接到原始事件
high_severity_events = [
    h for h in hits if isinstance(h.item.content, dict) and h.item.content.get("severity") == "high"
]

if high_severity_events:
    source_event = high_severity_events[0].item
    decision = client.add(
        content={
            "decision_id": "dec_001",
            "action": "lane_change_required",
            "recommendation": "检测到前方施工区域，建议切换至左侧车道并减速至 20km/h",
            "confidence": 0.91,
            "geo": {"lat": vehicle_pos.lat, "lon": vehicle_pos.lon, "geo_type": "decision_pt"},
        },
        scope=SCOPE,
        tags=["decision", "lane_change"],
        stage=cs.Stage.knowledge,
        stability=cs.Stability.ephemeral,
        source=f"decision_{source_event.id}",
        source_type=cs.SourceType.agent_inference,
        confidence=0.91,
        links=[cs.Link(target_id=source_event.id, relation=cs.LinkType.derived_from)],
        check_conflicts=False,
    )
    print(f"  生成决策建议: {decision.content['recommendation']}")
    print(f"  证据来源: {source_event.content.get('description','?')}")
    print(f"  链接关系: {cs.LinkType.derived_from.value}")
else:
    print("  当前前方无高危事件，保持当前驾驶策略")

print("\n✓ 智能驾驶场景示例完成")
