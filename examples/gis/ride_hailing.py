"""GIS 打车场景示例 — OceanBase 后端。

演示内容：
1. 写入司机位置（driver）、乘客订单（order）、热力区域（zone）
2. 按乘客位置召回附近司机（半径搜索）
3. 按多边形区域召回区域内活跃订单
4. 结合 Stage 演进：位置更新 extracted → 聚合 knowledge

运行前提：
- STORAGE_BACKEND=oceanbase（OB >= 4.2.2）
- GEO_ENABLED=true
"""

import os
import time
from dotenv import load_dotenv
load_dotenv()

# ── 快速配置，也可以通过 .env 文件注入 ────────────────────────────────────────
os.environ.setdefault("STORAGE_BACKEND", "oceanbase")
os.environ.setdefault("GEO_ENABLED", "true")
os.environ.setdefault("EMBEDDING_PROVIDER", "langchain")
os.environ.setdefault("SUMMARIZER_PROVIDER", "llm")
os.environ.setdefault("RETRIEVAL_RERANKER_MODE", "llm")
os.environ.setdefault("GEO_DEFAULT_RADIUS_KM", "5.0")
os.environ.setdefault("GEO_DISTANCE_DECAY_KM", "1.0")
os.environ.setdefault("RETRIEVAL_RECALL_ROUTES", '["phrase","terms","vector","geo"]')

import contextseek as cs
from contextseek.domain.geo import GeoPoint, GeoMetadata, GeoQuery

# ── 初始化客户端 ───────────────────────────────────────────────────────────────
client = cs.ContextSeek.from_settings()

SCOPE = "ride_hailing/demo"

# =============================================================================
# 1. 写入司机位置
# =============================================================================
print("=== 写入司机位置 ===")

drivers = [
    {
        "driver_id": "d_001",
        "name": "张师傅",
        "lat": 39.9087,
        "lon": 116.3975,
        "status": "available",
    },
    {
        "driver_id": "d_002",
        "name": "李师傅",
        "lat": 39.9102,
        "lon": 116.4012,
        "status": "available",
    },
    {
        "driver_id": "d_003",
        "name": "王师傅",
        "lat": 39.9045,
        "lon": 116.3890,
        "status": "on_trip",
    },
    {
        "driver_id": "d_004",
        "name": "赵师傅",
        "lat": 39.9200,
        "lon": 116.4100,
        "status": "available",
    },
]

for d in drivers:
    client.add(
        content={
            "driver_id": d["driver_id"],
            "name": d["name"],
            "status": d["status"],
            "geo": {"lat": d["lat"], "lon": d["lon"], "geo_type": "driver"},
        },
        scope=SCOPE,
        tags=["driver", d["status"]],
        stage=cs.Stage.extracted,
        stability=cs.Stability.ephemeral,  # 位置每分钟刷新，短生命周期
        source=d["name"],
        source_type=cs.SourceType.external_api,
        confidence=0.95,
        check_conflicts=False,
    )
    print(f"  写入司机 {d['name']} @ ({d['lat']}, {d['lon']})")

# =============================================================================
# 2. 写入乘客订单
# =============================================================================
print("\n=== 写入乘客订单 ===")

orders = [
    {
        "order_id": "o_001",
        "passenger": "小明",
        "pickup": {"lat": 39.9095, "lon": 116.3980},
        "dest": "北京西站",
    },
    {
        "order_id": "o_002",
        "passenger": "小红",
        "pickup": {"lat": 39.9080, "lon": 116.4020},
        "dest": "朝阳公园",
    },
]

for o in orders:
    client.add(
        content={
            "order_id": o["order_id"],
            "passenger": o["passenger"],
            "destination": o["dest"],
            "status": "pending",
            "geo": {
                "lat": o["pickup"]["lat"],
                "lon": o["pickup"]["lon"],
                "geo_type": "order",
            },
        },
        scope=SCOPE,
        tags=["order", "pending"],
        stage=cs.Stage.extracted,
        stability=cs.Stability.transient,
        source=o["order_id"],
        source_type=cs.SourceType.external_api,
        confidence=1.0,
        check_conflicts=False,
    )
    print(f"  写入订单 {o['order_id']} 乘客={o['passenger']} 起点=({o['pickup']['lat']}, {o['pickup']['lon']})")

# =============================================================================
# 3. 写入热力区域（多边形）
# =============================================================================
print("\n=== 写入热力区域 ===")

# 北京金融街附近的一个矩形热力区
client.add(
    content={
        "zone_id": "z_finance",
        "name": "金融街高峰需求区",
        "demand_level": "high",
        "geo": {
            "lat": 39.9150,
            "lon": 116.3900,
            "geo_type": "zone",
            "geo_shape": (
                "POLYGON((39.9100 116.3850, 39.9100 116.3950, "
                "39.9200 116.3950, 39.9200 116.3850, 39.9100 116.3850))"
            ),
        },
    },
    scope=SCOPE,
    tags=["zone", "high_demand"],
    stage=cs.Stage.knowledge,
    stability=cs.Stability.stable,
    source="ops_team",
    source_type=cs.SourceType.human_input,
    confidence=0.9,
    check_conflicts=False,
)
print("  写入热力区域：金融街高峰需求区")

# =============================================================================
# 4. 半径搜索：找乘客附近的可用司机
# =============================================================================
print("\n=== 乘客叫车：查找附近可用司机 ===")

# 乘客在 (39.9090, 116.3985)，需要 3km 内的空闲司机
passenger_location = GeoPoint(lat=39.9090, lon=116.3985)
geo_q = GeoQuery(
    center=passenger_location,
    radius_km=3.0,
    geo_type_filter=["driver"],
)

hits = client.retrieve(
    query="空闲司机",
    scope=SCOPE,
    k=5,
    full=True,
    tags=["driver"],
    geo_query=geo_q,
)

print(f"  找到 {len(hits)} 位附近司机：")
for h in hits:
    content = h.item.content if isinstance(h.item.content, dict) else {}
    geo = content.get("geo", {})
    print(
        f"    [{h.score:.3f}] {content.get('name','?')} "
        f"状态={content.get('status','?')} "
        f"位置=({geo.get('lat','?')}, {geo.get('lon','?')})"
    )

# =============================================================================
# 5. 多边形搜索：查找区域内的所有订单
# =============================================================================
print("\n=== 区域调度：查找金融街区域内的订单 ===")

polygon_wkt = (
    "POLYGON((39.9050 116.3850, 39.9050 116.4050, "
    "39.9200 116.4050, 39.9200 116.3850, 39.9050 116.3850))"
)
geo_area = GeoQuery(polygon_wkt=polygon_wkt, geo_type_filter=["order"])

hits = client.retrieve(
    query="待派单订单",
    scope=SCOPE,
    k=10,
    full=True,
    tags=["order"],
    geo_query=geo_area,
)

print(f"  区域内订单数：{len(hits)}")
for h in hits:
    content = h.item.content if isinstance(h.item.content, dict) else {}
    print(
        f"    [{h.score:.3f}] 订单 {content.get('order_id','?')} "
        f"乘客={content.get('passenger','?')} 目的地={content.get('destination','?')}"
    )

# =============================================================================
# 6. 沿路线搜索：找途经路段附近的司机（路线召回）
# =============================================================================
print("\n=== 路线召回：找途经东西长安街的司机 ===")

# 东西长安街简化为一条 LineString
route_wkt = "LINESTRING(39.9050 116.3800, 39.9070 116.3900, 39.9080 116.4000, 39.9070 116.4100)"
geo_route = GeoQuery(route_wkt=route_wkt, buffer_km=0.5, geo_type_filter=["driver"])

hits = client.retrieve(
    query="沿途司机",
    scope=SCOPE,
    k=5,
    full=True,
    tags=["driver"],
    geo_query=geo_route,
)

print(f"  路线附近司机数：{len(hits)}")
for h in hits:
    content = h.item.content if isinstance(h.item.content, dict) else {}
    print(f"    [{h.score:.3f}] {content.get('name','?')} 状态={content.get('status','?')}")

print("\n✓ 打车场景示例完成")
