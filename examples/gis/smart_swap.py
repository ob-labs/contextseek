"""GIS 记忆驱动的智能换电推荐示例。

核心问题：为什么明明公司旁边就有一个绝佳的换电站（第四代、3分钟换电、免停1小时），
车主来来回回跑了好多次却从来没被推过？

答案：系统不知道你常来这里，不知道这里有什么，也不知道把两件事联系起来。

本示例演示如何利用 GIS 记忆（gis_memory.py 生成的 extracted/knowledge 层）
将位置感知、行程模式和换电站信息结合，产生分层次的智能推荐：

  场景 1 - 熟悉地点（公司）   → 主动推送旁边绝佳换电站，无需导航
  场景 2 - 周末课外班          → 推荐含免停的商场换电站，顺便逛逛
  场景 3 - 陌生区域            → 开导航，最近最快，常规安全推荐
  场景 4 - 高速长途            → 沿途站点播报 + 长续航短租提示

前提：建议先运行 gis_memory.py 写入位置记忆，否则场景1/2/4降级为常规推荐。
      STORAGE_BACKEND=oceanbase，GEO_ENABLED=true
      LLM_PROVIDER=langchain（换电站 L0 摘要生成 + LLM 重排推荐排序）
      EMBEDDING_PROVIDER=langchain（向量化摘要，支持自然语言意图查询）
"""

import os
from dotenv import load_dotenv
load_dotenv()

os.environ.setdefault("STORAGE_BACKEND", "oceanbase")
os.environ.setdefault("GEO_ENABLED", "true")
os.environ.setdefault("EMBEDDING_PROVIDER", "langchain")
os.environ.setdefault("SUMMARIZER_PROVIDER", "llm")
os.environ.setdefault("RETRIEVAL_RERANKER_MODE", "llm")
os.environ.setdefault("GEO_DISTANCE_DECAY_KM", "0.5")
os.environ.setdefault("RETRIEVAL_RECALL_ROUTES", '["phrase","terms","vector","geo"]')

import contextseek as cs
from contextseek.domain.geo import GeoPoint, GeoQuery

DRIVER_ID       = "driver_sh_001"
SCOPE_EXTRACTED = f"gis_memory/{DRIVER_ID}/extracted"
SCOPE_KNOWLEDGE = f"gis_memory/{DRIVER_ID}/knowledge"
SCOPE_SWAP      = "swap_network/shanghai"

client = cs.ContextSeek.from_settings()

# 和 gis_memory.py 保持一致的坐标
HOME          = GeoPoint(lat=31.2304, lon=121.4737)
OFFICE        = GeoPoint(lat=31.2284, lon=121.4757)
WEEKEND_CLASS = GeoPoint(lat=31.2180, lon=121.4820)

# 关键换电站坐标
SWAP_BESIDE_OFFICE = GeoPoint(lat=31.2283, lon=121.4778)   # 公司旁 200m，核心场景
SWAP_IN_MALL       = GeoPoint(lat=31.2182, lon=121.4842)   # 课外班旁商场地下
SWAP_UNFAMILIAR    = GeoPoint(lat=30.9810, lon=121.2510)   # 陌生区域最近站（松江）
SWAP_HW_FENGQING   = GeoPoint(lat=30.9500, lon=121.0200)   # 高速枫泾服务区
SWAP_HW_JINSHAN    = GeoPoint(lat=30.8800, lon=120.9200)   # 高速金山服务区

# =============================================================================
# 0. 写入换电站 POI（生产中来自换电网络实时数据库）
# =============================================================================
print("=== 写入换电站 POI ===")

swap_stations = [
    {
        "station_id": "SH_4G_JING_001",
        "name": "蔚来换电站（静安华敏翰尊旁）",
        "generation": 4,
        "swap_minutes": 3,
        "free_parking_hours": 1,
        "available_batteries": 9,
        "total_slots": 13,
        "queuing_vehicles": 0,
        "est_wait_min": 0,
        "note": "公司步行 2 分钟，免费停车 1 小时，换完刚好出发",
        "geo": {
            "lat": SWAP_BESIDE_OFFICE.lat,
            "lon": SWAP_BESIDE_OFFICE.lon,
            "geo_type": "swap_station",
        },
    },
    {
        "station_id": "SH_3G_XH_WANDA",
        "name": "蔚来换电站（徐汇万达广场 B2）",
        "generation": 3,
        "swap_minutes": 5,
        "free_parking_hours": 2,
        "available_batteries": 11,
        "total_slots": 13,
        "queuing_vehicles": 0,
        "est_wait_min": 0,
        "mall_name": "徐汇万达广场",
        "mall_brands": "优衣库、星巴克、超市",
        "note": "换电 5 分钟，商场停车免 2 小时，顺便逛逛不亏",
        "geo": {
            "lat": SWAP_IN_MALL.lat,
            "lon": SWAP_IN_MALL.lon,
            "geo_type": "swap_station",
        },
    },
    {
        "station_id": "SH_3G_SJ_001",
        "name": "蔚来换电站（松江新城）",
        "generation": 3,
        "swap_minutes": 5,
        "free_parking_hours": 0,
        "available_batteries": 6,
        "total_slots": 13,
        "queuing_vehicles": 1,
        "est_wait_min": 8,
        "geo": {
            "lat": SWAP_UNFAMILIAR.lat,
            "lon": SWAP_UNFAMILIAR.lon,
            "geo_type": "swap_station",
        },
    },
    {
        "station_id": "SH_HW_FENGQING",
        "name": "蔚来换电站（沪昆高速 枫泾服务区）",
        "generation": 3,
        "swap_minutes": 5,
        "free_parking_hours": 0,
        "available_batteries": 3,
        "total_slots": 6,
        "queuing_vehicles": 4,
        "est_wait_min": 25,
        "highway": "沪昆高速",
        "km_from_sh": 65,
        "geo": {
            "lat": SWAP_HW_FENGQING.lat,
            "lon": SWAP_HW_FENGQING.lon,
            "geo_type": "swap_station",
        },
    },
    {
        "station_id": "SH_HW_JINSHAN",
        "name": "蔚来换电站（沪昆高速 金山服务区）",
        "generation": 3,
        "swap_minutes": 5,
        "free_parking_hours": 0,
        "available_batteries": 9,
        "total_slots": 13,
        "queuing_vehicles": 1,
        "est_wait_min": 5,
        "highway": "沪昆高速",
        "km_from_sh": 95,
        "geo": {
            "lat": SWAP_HW_JINSHAN.lat,
            "lon": SWAP_HW_JINSHAN.lon,
            "geo_type": "swap_station",
        },
    },
]

for s in swap_stations:
    try:
        station_item = client.add(
            content=s,
            scope=SCOPE_SWAP,
            source=s["station_id"],
            source_type=cs.SourceType.external_api,
            stage=cs.Stage.knowledge,
            stability=cs.Stability.stable,
            tags=["swap_station", f"gen{s['generation']}",
                  "highway" if "highway" in s else "urban"],
            check_conflicts=True,
        )
    except ValueError:
        station_item = None  # exact duplicate already in DB — skip
    gen_label = f"第{s['generation']}代({s['swap_minutes']}min)"
    avail = f"{s['available_batteries']}/{s['total_slots']}"
    wait  = f"等待{s['est_wait_min']}min" if s["est_wait_min"] else "即到即换"
    print(f"  {s['name'][:20]:20s} {gen_label:10s} 电池{avail:5s} {wait}")
    if station_item and station_item.abstract:
        print(f"    ↳ L0: {station_item.abstract}")


# ── 推荐辅助函数 ──────────────────────────────────────────────────────────────

def query_location_memory(pos: GeoPoint, radius_km: float = 0.5) -> list:
    """查找当前位置附近是否有已知的高频地点记忆"""
    return client.retrieve(
        query="我经常来的地方",
        scope=SCOPE_EXTRACTED,
        k=3,
        full=True,
        geo_query=GeoQuery(center=pos, radius_km=radius_km,
                           geo_type_filter=["frequent_location"]),
    )

def query_nearby_stations(pos: GeoPoint, radius_km: float, k: int = 5) -> list:
    """查找附近换电站"""
    return client.retrieve(
        query="附近哪里可以换电",
        scope=SCOPE_SWAP,
        k=k,
        full=True,
        geo_query=GeoQuery(center=pos, radius_km=radius_km,
                           geo_type_filter=["swap_station"]),
    )

def is_in_activity_zone(pos: GeoPoint) -> bool:
    """判断是否在熟悉的活动半径内（直接计算与已知核心地点的距离）"""
    from contextseek.domain.geo import dist_to_geo_sim
    # 基于已知高频地点列表做距离判断，避免 RRF 文本相似度干扰
    known_anchors = [HOME, OFFICE, WEEKEND_CLASS]
    for anchor in known_anchors:
        # 简单欧式距离估算（1 degree ≈ 111km）
        dlat = (pos.lat - anchor.lat) * 111.0
        dlon = (pos.lon - anchor.lon) * 111.0 * 0.87  # cos(31°) ≈ 0.87
        dist_km = (dlat ** 2 + dlon ** 2) ** 0.5
        if dist_km <= 15.0:
            return True
    return False


# =============================================================================
# 场景 1：公司旁边就有绝佳换电站，为什么从来没推过？
# =============================================================================
print("\n" + "═" * 62)
print("  场景 1：工作日下班前 — 公司旁换电站主动推送")
print("  （本月来了 20 次，旁边 200m 就有第四代站，从来没被推过）")
print("═" * 62)

# 当前状态：公司门口，电量 38%，准备下班
cur_pos = GeoPoint(lat=OFFICE.lat + 0.0001, lon=OFFICE.lon + 0.0005)
battery  = 38

print(f"\n  当前位置 ({cur_pos.lat:.4f}, {cur_pos.lon:.4f})  |  电量 {battery}%")
print(f"  时段: 工作日 18:20，准备下班")

# Step 1: 查询位置记忆（我来这里多少次了？）
loc_memory = query_location_memory(cur_pos, radius_km=0.5)

if loc_memory:
    lc = loc_memory[0].item.content if isinstance(loc_memory[0].item.content, dict) else {}
    loc_label   = lc.get("label", "未知地点")
    loc_type    = lc.get("location_type", "")
    visit_count = lc.get("visit_count_30d", 0)
    print(f"\n  位置记忆命中：「{loc_label}」({loc_type})，本月已到访 {visit_count} 次")

    # Step 2: 找 500m 内的换电站
    nearby = query_nearby_stations(cur_pos, radius_km=0.5, k=3)
    print(f"  500m 内换电站：{len(nearby)} 个")

    if nearby and loc_type in ("workplace", "home", "frequent_location"):
        best = nearby[0]
        sc   = best.item.content if isinstance(best.item.content, dict) else {}

        # ── 主动推送 ────────────────────────────────────────────────────────
        print(f"\n  ┌─── 主动推送 ───────────────────────────────────────────────")
        print(f"  │  您在「{loc_label}」（本月 {visit_count} 次），旁边 200m 就有换电站！")
        print(f"  │")
        print(f"  │  📍 {sc.get('name','?')}")
        print(f"  │  ⚡ 第 {sc.get('generation','?')} 代站 · 换电约 {sc.get('swap_minutes','?')} 分钟")
        print(f"  │  🔋 当前可用电池 {sc.get('available_batteries','?')}/{sc.get('total_slots','?')}，无需排队")
        print(f"  │  🅿  免费停车 {sc.get('free_parking_hours','?')} 小时")
        print(f"  │  💡 {sc.get('note','')}")
        print(f"  │")
        print(f"  │  ▶ 顺路去换，无需导航，2 分钟步行距离")
        print(f"  └──────────────────────────────────────────────────────────")

        # 将推送事件写入记忆，供后续行为分析
        client.add(
            content={
                "event_type": "proactive_swap_push",
                "trigger": "familiar_workplace_low_battery",
                "location_label": loc_label,
                "visit_count": visit_count,
                "station_id": sc.get("station_id"),
                "battery_at_trigger": battery,
                "push_reason": (
                    f"用户本月到访「{loc_label}」{visit_count}次，"
                    f"旁边 200m 有高质量换电站未被主动发现"
                ),
                "geo": {
                    "lat": cur_pos.lat, "lon": cur_pos.lon,
                    "geo_type": "push_event",
                },
            },
            scope=f"gis_memory/{DRIVER_ID}/events",
            source="swap_recommendation_engine",
            source_type=cs.SourceType.agent_inference,
            stage=cs.Stage.knowledge,
            stability=cs.Stability.transient,
            tags=["proactive_push", "workplace_adjacent"],
            check_conflicts=False,
        )
    else:
        print("  换电站较远（>500m）或位置类型不符，降级为常规推荐")
else:
    print("\n  ⚠ 未找到位置记忆（建议先运行 gis_memory.py）")
    print("  降级：查找附近换电站并按常规距离优先排序")
    fallback = query_nearby_stations(cur_pos, radius_km=3.0, k=3)
    for h in fallback:
        fc = h.item.content if isinstance(h.item.content, dict) else {}
        print(f"    [{h.score:.3f}] {fc.get('name','?')} | "
              f"等待{fc.get('est_wait_min','?')}min")


# =============================================================================
# 场景 2：孩子周末课外班——换电 + 顺便商场逛逛
# =============================================================================
print("\n" + "═" * 62)
print("  场景 2：周六上午 — 孩子课外班，推荐商场换电站")
print("  （孩子上课 2 小时，家长在附近等，换电 + 逛商场一举两得）")
print("═" * 62)

cur_pos_2 = GeoPoint(lat=WEEKEND_CLASS.lat + 0.0002, lon=WEEKEND_CLASS.lon - 0.0003)
battery_2 = 44

print(f"\n  当前位置 ({cur_pos_2.lat:.4f}, {cur_pos_2.lon:.4f})  |  电量 {battery_2}%")
print(f"  时段: 周六 09:33，刚送孩子进课外班")

loc_memory_2 = query_location_memory(cur_pos_2, radius_km=0.8)
nearby_2     = query_nearby_stations(cur_pos_2, radius_km=2.0, k=5)

if loc_memory_2:
    lc2    = loc_memory_2[0].item.content if isinstance(loc_memory_2[0].item.content, dict) else {}
    wait_h = lc2.get("avg_dwell_hours", 2)
    wait_behavior = lc2.get("typical_wait_behavior", "")
    print(f"\n  位置记忆命中：「{lc2.get('label','?')}」，孩子课时约 {wait_h*60:.0f} 分钟")
    print(f"  历史等待行为：{wait_behavior}")

    # 优先找有商场停车的站点
    mall_hits = [
        h for h in nearby_2
        if isinstance(h.item.content, dict) and h.item.content.get("mall_name")
    ]

    if mall_hits:
        mc  = mall_hits[0].item.content
        print(f"\n  ┌─── 个性化推荐 ────────────────────────────────────────────")
        print(f"  │  孩子上课 {wait_h:.0f}h，正好去换个电，顺便逛逛")
        print(f"  │")
        print(f"  │  📍 {mc.get('name','?')}")
        print(f"  │  🏬 {mc.get('mall_name','?')}（{mc.get('mall_brands','')}）")
        print(f"  │  ⚡ 换电约 {mc.get('swap_minutes','?')} 分钟")
        print(f"  │  🅿  商场免停 {mc.get('free_parking_hours','?')} 小时（换电时间已包含在内）")
        print(f"  │  💡 {mc.get('note','')}")
        print(f"  └──────────────────────────────────────────────────────────")
    elif nearby_2:
        nc = nearby_2[0].item.content if isinstance(nearby_2[0].item.content, dict) else {}
        print(f"\n  最近换电站：{nc.get('name','?')}（无商场停车，常规推荐）")
else:
    print("\n  ⚠ 未找到位置记忆，按距离推荐附近换电站")
    for h in nearby_2[:2]:
        nc = h.item.content if isinstance(h.item.content, dict) else {}
        print(f"    [{h.score:.3f}] {nc.get('name','?')}")


# =============================================================================
# 场景 3：陌生区域——开导航，常规推荐
# =============================================================================
print("\n" + "═" * 62)
print("  场景 3：陌生区域 — 开导航，最近最快")
print("═" * 62)

# 远离日常活动半径的位置
unfamiliar_pos = GeoPoint(lat=30.9800, lon=121.2500)
battery_3      = 28  # 电量较低，有紧迫感

print(f"\n  当前位置 ({unfamiliar_pos.lat:.4f}, {unfamiliar_pos.lon:.4f})  |  电量 {battery_3}%")
print(f"  时段: 工作日 14:15")

loc_memory_3  = query_location_memory(unfamiliar_pos, radius_km=2.0)
in_zone_3     = is_in_activity_zone(unfamiliar_pos)
nearby_3      = query_nearby_stations(unfamiliar_pos, radius_km=5.0, k=3)

# loc_memory_3 via RRF can surface text-similar items regardless of geo distance;
# use the distance-based zone check as the authoritative familiarity signal.
familiar = in_zone_3
print(f"  位置记忆：{'命中' if loc_memory_3 else '无'}  "
      f"活动半径：{'熟悉' if in_zone_3 else '陌生'}")

if not familiar:
    print(f"\n  ┌─── 陌生区域推荐 ──────────────────────────────────────────")
    print(f"  │  您在不常去的区域，电量 {battery_3}%，建议尽快换电")
    if nearby_3:
        nc3 = nearby_3[0].item.content if isinstance(nearby_3[0].item.content, dict) else {}
        print(f"  │")
        print(f"  │  📍 {nc3.get('name','?')}")
        print(f"  │  🔋 可用电池 {nc3.get('available_batteries','?')}/{nc3.get('total_slots','?')}")
        print(f"  │  ⏱ 预计等待 {nc3.get('est_wait_min','?')} 分钟")
        print(f"  │  🗺  已开启导航（路线优先：最近 + 排队少）")
    else:
        print(f"  │  🗺  附近无换电站，已开启导航寻找更远的站点")
    print(f"  └──────────────────────────────────────────────────────────")
else:
    # 虽然不是常去地点但在熟悉半径内
    print(f"\n  在熟悉半径内（非高频地点），按距离+等待时间推荐")
    for h in nearby_3[:2]:
        nc = h.item.content if isinstance(h.item.content, dict) else {}
        print(f"    [{h.score:.3f}] {nc.get('name','?')} | "
              f"等待{nc.get('est_wait_min','?')}min | "
              f"电池{nc.get('available_batteries','?')}/{nc.get('total_slots','?')}")


# =============================================================================
# 场景 4：高速长途——沿途播报 + 长续航短租
# =============================================================================
print("\n" + "═" * 62)
print("  场景 4：高速长途（上海→杭州）— 沿途站点实时播报")
print("═" * 62)

battery_4    = 88  # 出发时充足
trip_km      = 180  # 沪杭约 180km
consumption  = 22   # 预计电耗 22%（高速工况）

print(f"\n  出发位置：上海（内环）| 电量 {battery_4}%")
print(f"  目的地：杭州（约 {trip_km}km）| 预计电耗约 {consumption}%")
print(f"  出发后预计剩余：{battery_4 - consumption}%（安全但建议沿途换一次）")

# 沿途路线（lat lon 格式，SRID 4326）
highway_route = (
    "LINESTRING("
    "31.1500 121.3500, "
    "31.0500 121.2000, "
    "30.9500 121.0200, "
    "30.8800 120.9200, "
    "30.7500 120.7500"
    ")"
)

route_hits = client.retrieve(
    query="沪杭高速沿途哪里可以停下来换电",
    scope=SCOPE_SWAP,
    k=10,
    full=True,
    geo_query=GeoQuery(route_wkt=highway_route, buffer_km=3.0,
                       geo_type_filter=["swap_station"]),
)

print(f"\n  沿途换电站（共 {len(route_hits)} 个）：")
for h in route_hits:
    sc4    = h.item.content if isinstance(h.item.content, dict) else {}
    avail  = sc4.get("available_batteries", "?")
    slots  = sc4.get("total_slots", "?")
    wait   = sc4.get("est_wait_min", "?")
    queue  = sc4.get("queuing_vehicles", 0)
    km     = sc4.get("km_from_sh", "?")
    bar    = "■" * avail + "□" * (slots - avail) if isinstance(avail, int) and isinstance(slots, int) else ""
    queue_str = f"⚠ 排队{queue}辆" if isinstance(queue, int) and queue >= 3 else "✓ 无等待" if queue == 0 else f"排队{queue}辆"
    print(f"    [{h.score:.3f}] {sc4.get('name','?'):22s} "
          f"距出发{km}km  {bar} {avail}/{slots}  {queue_str}  等待{wait}min")

# 智能建议：优先推荐真正的高速服务区站点（有 highway 字段）
best_hw = min(
    (h for h in route_hits
     if isinstance(h.item.content, dict)
     and h.item.content.get("highway")      # 仅高速服务区
     and h.item.content.get("est_wait_min", 99) < 20),
    key=lambda h: h.item.content.get("est_wait_min", 99),
    default=None,
)
if best_hw:
    bc = best_hw.item.content
    print(f"\n  ★ 推荐换电点：{bc.get('name','?')}")
    print(f"     距出发 {bc.get('km_from_sh','?')}km，此时电量约剩 {battery_4 - bc.get('km_from_sh',0)//10}%，换完无忧到杭州")
    print(f"     等待时间仅 {bc.get('est_wait_min','?')} 分钟，全程无焦虑")

# 长续航短租提示
print(f"\n  💡 换电还有另一个选择：")
print(f"     当前 100kWh 标准电池，沪杭单程消耗约 22%，往返若不换电剩余较少")
print(f"     蔚来 150kWh 超长续航短租 ¥129/天 → 沪杭往返全程不用停换，单次省 5 分钟")
print(f"     长假或多次往返时性价比更高，出发前一站换入返回换出即可")


print("\n✓ 智能换电推荐示例完成")
print("\n推荐逻辑分层总结：")
print("  熟悉高频地点（公司/家）→ 主动推送最近最优站，直接操作，无需导航")
print("  熟悉场景（课外班等候）  → 结合等待行为，推荐能顺便做事的站点")
print("  熟悉半径内非高频地点    → 距离+等待时间排序，普通推荐")
print("  陌生区域                 → 导航引导，保守稳健，最近最快")
print("  高速长途                 → 路线规划，实时站况播报，可叠加长续航短租")
