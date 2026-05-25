"""GIS 记忆生成示例 — RAW 轨迹 → 地点事实 → 行为知识。

GIS 记忆不是文档里的换电偏好，而是从实际行驶数据中提炼的高密度知识：

  原始信息（raw）：
    - 点：GPS 定位点 + 停留时长（实际到访的每个地点）
    - 线：行驶路径（途经点序列 + 各段时速）

  提炼事实（extracted）：
    - 高频地点：家、公司、孩子学校、课外班……（访问次数、典型到离时间）
    - 路径段：通勤路线、接送路线（距离、耗时、电耗）

  行为知识（knowledge）：
    - 行程模式：工作日送娃→送人→上班 三段通勤
    - 周末模式：孩子课外班（家长在附近等待逛商场）
    - 活动半径：87% 行程在 15km 生活圈内

演示流程：
  1. 写入原始 GPS 停靠点（5个工作日 + 1个周末，raw 层）
  2. 分析提炼高频地点（extracted 层）
  3. 生成行为模式知识（knowledge 层）
  4. 验证：基于 GIS 记忆回答"用户最近常去哪里"

运行前提：
  STORAGE_BACKEND=oceanbase，GEO_ENABLED=true
  LLM_PROVIDER=langchain（L0 摘要生成 + 验证查询 LLM 重排）
  EMBEDDING_PROVIDER=langchain（向量化摘要，支持自然语言查询）
"""

import os
from dotenv import load_dotenv
load_dotenv()  # 将 .env 写入 os.environ，供 OpenAI SDK 读取 OPENAI_API_KEY 等

os.environ.setdefault("STORAGE_BACKEND", "oceanbase")
os.environ.setdefault("GEO_ENABLED", "true")
os.environ.setdefault("EMBEDDING_PROVIDER", "langchain")
os.environ.setdefault("SUMMARIZER_PROVIDER", "llm")
os.environ.setdefault("RETRIEVAL_RERANKER_MODE", "llm")
os.environ.setdefault("GEO_DISTANCE_DECAY_KM", "0.3")
os.environ.setdefault("RETRIEVAL_RECALL_ROUTES", '["phrase","terms","vector","geo"]')

import contextseek as cs
from contextseek.domain.geo import GeoPoint, GeoQuery

DRIVER_ID = "driver_sh_001"
SCOPE_RAW       = f"gis_memory/{DRIVER_ID}/raw"
SCOPE_EXTRACTED = f"gis_memory/{DRIVER_ID}/extracted"
SCOPE_KNOWLEDGE = f"gis_memory/{DRIVER_ID}/knowledge"

client = cs.ContextSeek.from_settings()

# ── 基础地理坐标（上海场景） ──────────────────────────────────────────────────
HOME          = GeoPoint(lat=31.2304, lon=121.4737)   # 家（普陀区）
OFFICE        = GeoPoint(lat=31.2284, lon=121.4757)   # 公司（静安区，5km 处）
KID_SCHOOL    = GeoPoint(lat=31.2350, lon=121.4680)   # 孩子学校（顺路）
SPOUSE_WORK   = GeoPoint(lat=31.2260, lon=121.4800)   # 配偶公司（送完孩子顺路送人）
WEEKEND_CLASS = GeoPoint(lat=31.2180, lon=121.4820)   # 孩子周末课外班（徐汇区）

# =============================================================================
# 1. 写入原始 GPS 停靠点（raw 层）
# =============================================================================
print("=== 写入原始 GPS 停靠点（raw 层）===")
print("  背景：车机每次点火/熄火记录一个停靠点（位置 + 停留时长）")

def write_stop(date: str, arrive: str, point: GeoPoint, label: str,
               dwell_min: int, tags: list[str], refs: list) -> cs.ContextItem:
    """写入单个停靠点（GPS track stop）"""
    return client.add(
        content={
            "record_type": "gps_stop",
            "driver_id": DRIVER_ID,
            "date": date,
            "arrive_time": f"{date}T{arrive}:00",
            "dwell_minutes": dwell_min,
            "location_label": label,
            "geo": {"lat": point.lat, "lon": point.lon, "geo_type": "track_point"},
        },
        scope=SCOPE_RAW,
        source="car_gps_module",
        source_type=cs.SourceType.external_api,
        stage=cs.Stage.raw,
        stability=cs.Stability.ephemeral,
        tags=["gps_stop"] + tags,
        check_conflicts=False,
    )

# 工作日行程：送娃 → 送人 → 上班（5 天）
weekday_stops = [
    ("07:00", HOME,        "家（出发）",     0,   ["home", "departure"]),
    ("07:45", KID_SCHOOL,  "孩子学校",       8,   ["school_dropoff"]),
    ("08:15", SPOUSE_WORK, "配偶公司",       5,   ["spouse_dropoff"]),
    ("08:55", OFFICE,      "公司（到达）",   480, ["office", "workplace"]),
    ("18:45", HOME,        "家（回程）",     0,   ["home", "arrival"]),
]

raw_items: list[cs.ContextItem] = []
for day_idx in range(5):
    date = f"2025-05-{19 + day_idx:02d}"
    for arrive, point, label, dwell, tags in weekday_stops:
        item = write_stop(date, arrive, point, label, dwell, ["weekday"] + tags, raw_items)
        raw_items.append(item)

# 周末行程：孩子课外班（周六）
weekend_stops = [
    ("09:00", HOME,          "家（出发）",         0,   ["home", "departure"]),
    ("09:35", WEEKEND_CLASS, "孩子课外班",         120, ["weekend_class", "kid_activity"]),
    ("11:55", HOME,          "家（回程）",         0,   ["home", "arrival"]),
]
for arrive, point, label, dwell, tags in weekend_stops:
    item = write_stop("2025-05-24", arrive, point, label, dwell, ["weekend"] + tags, raw_items)
    raw_items.append(item)

print(f"  写入停靠点：{len(raw_items)} 条"
      f"（工作日 {5 * len(weekday_stops)} + 周末 {len(weekend_stops)}）")

# =============================================================================
# 2. 提炼高频地点（extracted 层）
# =============================================================================
print("\n=== 提炼高频地点（extracted 层）===")
print("  分析：统计各地点访问频次、典型到离时间、平均停留时长")

frequent_locations = [
    dict(
        location_id="loc_home",
        label="家",
        location_type="home",
        visit_count_30d=22,
        weekday_visits=20,
        weekend_visits=2,
        typical_departure="07:00",
        typical_arrival="18:45",
        avg_dwell_hours=12.5,
        lat=HOME.lat, lon=HOME.lon,
    ),
    dict(
        location_id="loc_office",
        label="公司",
        location_type="workplace",
        visit_count_30d=20,
        weekday_visits=20,
        weekend_visits=0,
        typical_arrival="08:55",
        typical_departure="18:30",
        avg_dwell_hours=8.5,
        lat=OFFICE.lat, lon=OFFICE.lon,
    ),
    dict(
        location_id="loc_kid_school",
        label="孩子学校",
        location_type="school_dropoff",
        visit_count_30d=20,
        weekday_visits=20,
        weekend_visits=0,
        typical_arrival="07:45",
        typical_departure="07:53",
        avg_dwell_hours=0.13,  # 8 分钟快速接送
        lat=KID_SCHOOL.lat, lon=KID_SCHOOL.lon,
    ),
    dict(
        location_id="loc_spouse_work",
        label="配偶公司",
        location_type="spouse_dropoff",
        visit_count_30d=20,
        weekday_visits=20,
        weekend_visits=0,
        typical_arrival="08:15",
        typical_departure="08:20",
        avg_dwell_hours=0.08,  # 5 分钟快速送达
        lat=SPOUSE_WORK.lat, lon=SPOUSE_WORK.lon,
    ),
    dict(
        location_id="loc_weekend_class",
        label="孩子课外班",
        location_type="weekend_activity",
        visit_count_30d=4,
        weekday_visits=0,
        weekend_visits=4,
        typical_arrival="09:35",
        typical_departure="11:35",
        avg_dwell_hours=2.0,
        typical_wait_behavior="nearby_shopping",  # 家长等待期间在附近逛
        lat=WEEKEND_CLASS.lat, lon=WEEKEND_CLASS.lon,
    ),
]

loc_items: dict[str, cs.ContextItem] = {}
for loc in frequent_locations:
    lat, lon = loc.pop("lat"), loc.pop("lon")
    item = client.add(
        content={
            **loc,
            "geo": {"lat": lat, "lon": lon, "geo_type": "frequent_location"},
        },
        scope=SCOPE_EXTRACTED,
        source=f"location_analyser/{loc['location_id']}",
        source_type=cs.SourceType.agent_inference,
        stage=cs.Stage.extracted,
        stability=cs.Stability.stable,
        tags=["frequent_location", loc["location_type"]],
        links=[cs.Link(target_id=t.id, relation=cs.LinkType.derived_from)
               for t in raw_items[:min(3, len(raw_items))]],
        check_conflicts=False,
    )
    loc_items[loc["location_id"]] = item
    print(f"  [{loc['location_type']:20s}] {loc['label']:12s} "
          f"月访问={loc['visit_count_30d']:>3}次  "
          f"平均停留={loc['avg_dwell_hours']:.1f}h")
    if item.abstract:
        print(f"    ↳ L0: {item.abstract}")

# =============================================================================
# 3. 生成行为模式知识（knowledge 层）
# =============================================================================
print("\n=== 生成行为模式知识（knowledge 层）===")

# ── 3-1. 工作日通勤路径 ────────────────────────────────────────────────────
commute = client.add(
    content={
        "pattern_id": "weekday_commute",
        "pattern_type": "commute_sequence",
        "schedule": "weekday",
        "sequence": [
            "家（07:00 出发）",
            "孩子学校（07:45 送娃 ~8min）",
            "配偶公司（08:15 送人 ~5min）",
            "公司（08:55 到达）",
        ],
        "departure_window": "07:00–07:10",
        "arrival_window": "08:50–09:05",
        "total_km": 12.5,
        "avg_duration_min": 115,
        "avg_battery_consumption_pct": 11,
        "observation_days": 5,
        "confidence": 0.93,
        "geo": {
            "lat": OFFICE.lat,
            "lon": OFFICE.lon,
            "geo_type": "commute_pattern",
        },
    },
    scope=SCOPE_KNOWLEDGE,
    source="pattern_engine/commute",
    source_type=cs.SourceType.agent_inference,
    stage=cs.Stage.knowledge,
    stability=cs.Stability.stable,
    tags=["commute", "weekday", "multi_stop"],
    links=[
        cs.Link(target_id=loc_items["loc_home"].id,      relation=cs.LinkType.derived_from),
        cs.Link(target_id=loc_items["loc_kid_school"].id, relation=cs.LinkType.derived_from),
        cs.Link(target_id=loc_items["loc_spouse_work"].id, relation=cs.LinkType.derived_from),
        cs.Link(target_id=loc_items["loc_office"].id,    relation=cs.LinkType.derived_from),
    ],
    check_conflicts=False,
)
print("  工作日通勤：送娃(07:45) → 送人(08:15) → 公司(08:55)，电耗约 11%")
if commute.abstract:
    print(f"    ↳ L0: {commute.abstract}")

# ── 3-2. 周末课外班行程 ────────────────────────────────────────────────────
weekend_pattern = client.add(
    content={
        "pattern_id": "weekend_class_trip",
        "pattern_type": "weekend_activity",
        "schedule": "weekend_morning",
        "activity": "孩子课外班",
        "departure_window": "09:00–09:10",
        "class_duration_hours": 2.0,
        "wait_location": "课外班附近",
        "typical_wait_behavior": "nearby_shopping",
        "total_km": 8.5,
        "avg_battery_consumption_pct": 8,
        "observation_days": 4,
        "confidence": 0.88,
        "geo": {
            "lat": WEEKEND_CLASS.lat,
            "lon": WEEKEND_CLASS.lon,
            "geo_type": "activity_pattern",
        },
    },
    scope=SCOPE_KNOWLEDGE,
    source="pattern_engine/weekend",
    source_type=cs.SourceType.agent_inference,
    stage=cs.Stage.knowledge,
    stability=cs.Stability.stable,
    tags=["weekend", "school_run", "wait_nearby"],
    links=[
        cs.Link(target_id=loc_items["loc_home"].id,         relation=cs.LinkType.derived_from),
        cs.Link(target_id=loc_items["loc_weekend_class"].id, relation=cs.LinkType.derived_from),
    ],
    check_conflicts=False,
)
print("  周末行程：孩子课外班 2h，家长在附近等待（通常顺便逛逛）")
if weekend_pattern.abstract:
    print(f"    ↳ L0: {weekend_pattern.abstract}")

# ── 3-3. 日常活动半径 ────────────────────────────────────────────────────────
activity_zone = client.add(
    content={
        "pattern_id": "daily_activity_radius",
        "pattern_type": "activity_zone",
        "zone_label": "日常生活圈",
        "center_lat": HOME.lat,
        "center_lon": HOME.lon,
        "radius_km": 15.0,
        "coverage_pct": 87,  # 87% 的行程落在此半径内
        "familiar_locations": ["家", "公司", "孩子学校", "配偶公司", "孩子课外班"],
        "unfamiliar_threshold_km": 20.0,
        "geo": {
            "lat": HOME.lat,
            "lon": HOME.lon,
            "geo_type": "activity_zone",
        },
    },
    scope=SCOPE_KNOWLEDGE,
    source="pattern_engine/zone",
    source_type=cs.SourceType.agent_inference,
    stage=cs.Stage.knowledge,
    stability=cs.Stability.stable,
    tags=["activity_zone", "home_radius"],
    links=[
        cs.Link(target_id=loc_items[k].id, relation=cs.LinkType.derived_from)
        for k in ["loc_home", "loc_office", "loc_kid_school", "loc_weekend_class"]
    ],
    check_conflicts=False,
)
print("  活动半径：15km 生活圈覆盖 87% 行程（熟悉区域 5 个）")
if activity_zone.abstract:
    print(f"    ↳ L0: {activity_zone.abstract}")

# =============================================================================
# 4. 验证：基于 GIS 记忆查询
# =============================================================================
print("\n=== 验证：基于 GIS 记忆查询行为特征 ===")

# 4-1. 在公司附近查询已知地点记忆（向量召回理解"每天上班的地方"语义）
print("\n  Q: 公司附近（500m）有哪些已知地点记忆？")
hits = client.retrieve(
    query="我每天上班去的地方",
    scope=SCOPE_EXTRACTED,
    k=3,
    full=True,
    geo_query=GeoQuery(center=OFFICE, radius_km=0.5),
)
for h in hits:
    c = h.item.content if isinstance(h.item.content, dict) else {}
    print(f"    [{h.score:.3f}] {c.get('label','?'):10s} "
          f"类型={c.get('location_type','?'):15s} "
          f"月访问={c.get('visit_count_30d','?'):>3}次")

# 4-2. 查询周末行为知识（向量召回理解"周末的活动规律"语义）
print("\n  Q: 周末通常去哪、做什么？")
hits_wk = client.retrieve(
    query="周末我们通常去哪里做什么",
    scope=SCOPE_KNOWLEDGE,
    k=3,
    full=True,
    geo_query=GeoQuery(center=WEEKEND_CLASS, radius_km=2.0),
)
for h in hits_wk:
    c = h.item.content if isinstance(h.item.content, dict) else {}
    ptype = c.get("pattern_type", "?")
    if ptype == "weekend_activity":
        print(f"    [{h.score:.3f}] {c.get('activity','?')} | "
              f"等待行为={c.get('typical_wait_behavior','?')} | "
              f"置信度={c.get('confidence','?')}")
    elif ptype == "activity_zone":
        print(f"    [{h.score:.3f}] {c.get('zone_label','?')} | "
              f"覆盖半径={c.get('radius_km','?')}km | "
              f"行程覆盖率={c.get('coverage_pct','?')}%")

# 4-3. 查询工作日通勤知识（向量召回理解"上班方式"语义）
print("\n  Q: 工作日的通勤路径是什么？")
hits_cm = client.retrieve(
    query="我每天怎么去上班的",
    scope=SCOPE_KNOWLEDGE,
    k=2,
    full=True,
    geo_query=GeoQuery(center=OFFICE, radius_km=1.0),
)
for h in hits_cm:
    c = h.item.content if isinstance(h.item.content, dict) else {}
    if c.get("pattern_type") == "commute_sequence":
        seq = c.get("sequence", [])
        print(f"    [{h.score:.3f}] 通勤链：{' → '.join(seq)}")
        print(f"           电耗约 {c.get('avg_battery_consumption_pct','?')}%，"
              f"置信度={c.get('confidence','?')}")

print(f"\n✓ GIS 记忆生成完成")
print(f"  raw 层：{len(raw_items)} 条 GPS 停靠点")
print(f"  extracted 层：{len(frequent_locations)} 个高频地点")
print(f"  knowledge 层：3 个行为模式（通勤路径、周末活动、活动半径）")
print(f"\n  → 运行 smart_swap.py 查看如何将 GIS 记忆用于智能换电推荐")
