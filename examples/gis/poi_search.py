"""GIS 地图 POI 搜索示例 — OceanBase 后端。

演示内容：
1. 批量写入 POI，LLM 自动生成 L0 摘要（Summarizer）
2. 混合搜索：phrase + terms + vector + geo 四路召回 + LLM 重排
3. 利用 geo_decay_score 对远距离 POI 降权
4. 向量语义搜索对比：关键词 vs 自然语言查询效果差异
5. 聚合附近 POI 到 knowledge 层（GeoAwareMerger）

运行前提：
- STORAGE_BACKEND=oceanbase（OB >= 4.2.2）
- GEO_ENABLED=true
- LLM_PROVIDER=langchain（LLM 重排 + 摘要生成）
- EMBEDDING_PROVIDER=langchain（向量召回）
"""

import os
from dotenv import load_dotenv
load_dotenv()

os.environ.setdefault("STORAGE_BACKEND", "oceanbase")
os.environ.setdefault("GEO_ENABLED", "true")
os.environ.setdefault("EMBEDDING_PROVIDER", "langchain")
os.environ.setdefault("SUMMARIZER_PROVIDER", "llm")
os.environ.setdefault("RETRIEVAL_RERANKER_MODE", "llm")
os.environ.setdefault("GEO_DEFAULT_RADIUS_KM", "3.0")
os.environ.setdefault("GEO_DISTANCE_DECAY_KM", "0.5")
os.environ.setdefault("RETRIEVAL_RECALL_ROUTES", '["phrase","terms","vector","geo"]')

import contextseek as cs
from contextseek.domain.geo import GeoPoint, GeoQuery
from contextseek.policies.decay import geo_decay_score

SCOPE = "map/poi_demo"

client = cs.ContextSeek.from_settings()

# =============================================================================
# 1. 写入 POI 数据
# =============================================================================
print("=== 写入 POI 数据 ===")

pois = [
    # 餐厅
    {"name": "海底捞火锅（西单店）", "category": "restaurant", "lat": 39.9122, "lon": 116.3726, "rating": 4.8},
    {"name": "全聚德烤鸭（前门店）", "category": "restaurant", "lat": 39.8982, "lon": 116.3975, "rating": 4.7},
    {"name": "庆丰包子铺（西长安街店）", "category": "restaurant", "lat": 39.9053, "lon": 116.3720, "rating": 4.3},
    # 加油站
    {"name": "中石化加油站（长安街西）", "category": "gas_station", "lat": 39.9085, "lon": 116.3680, "rating": 4.0},
    {"name": "中石油加油站（复兴门）", "category": "gas_station", "lat": 39.9107, "lon": 116.3588, "rating": 4.1},
    # 地铁站
    {"name": "西单地铁站（1号线/4号线）", "category": "metro", "lat": 39.9114, "lon": 116.3728, "rating": 4.5},
    {"name": "复兴门地铁站（1号线/2号线）", "category": "metro", "lat": 39.9105, "lon": 116.3589, "rating": 4.4},
    # 购物中心
    {"name": "西单大悦城", "category": "mall", "lat": 39.9130, "lon": 116.3725, "rating": 4.6},
    {"name": "君太百货", "category": "mall", "lat": 39.9120, "lon": 116.3732, "rating": 4.2},
]

for p in pois:
    item = client.add(
        content={
            "name": p["name"],
            "category": p["category"],
            "rating": p["rating"],
            "geo": {"lat": p["lat"], "lon": p["lon"], "geo_type": "poi"},
        },
        scope=SCOPE,
        tags=["poi", p["category"]],
        stage=cs.Stage.knowledge,
        stability=cs.Stability.stable,
        source="amap_poi",
        source_type=cs.SourceType.external_api,
        confidence=0.92,
        check_conflicts=False,
    )
    print(f"  写入 POI: {p['name']} ({p['category']}) @ ({p['lat']}, {p['lon']})")
    if item.abstract:
        print(f"    ↳ L0: {item.abstract}")

# =============================================================================
# 2. 混合搜索：用户在西单附近搜索"烤鸭"
# =============================================================================
print("\n=== 混合搜索：烤鸭（西单附近） ===")

user_pos = GeoPoint(lat=39.9110, lon=116.3720)
geo_q = GeoQuery(center=user_pos, radius_km=5.0, geo_type_filter=["poi"])

hits = client.retrieve(
    query="烤鸭 餐厅",
    scope=SCOPE,
    k=5,
    full=True,
    geo_query=geo_q,
)

print(f"  搜索结果（共 {len(hits)} 条）：")
for h in hits:
    content = h.item.content if isinstance(h.item.content, dict) else {}
    geo = content.get("geo", {})
    # 手动计算地理衰减展示
    geo_factor = geo_decay_score(
        {"lat": geo.get("lat"), "lon": geo.get("lon")},
        user_pos,
        decay_km=0.5,
    )
    print(
        f"    [{h.score:.3f}] {content.get('name','?')} "
        f"评分={content.get('rating','?')} "
        f"地理衰减={geo_factor:.3f}"
    )

# =============================================================================
# 3. 按类别过滤的地理搜索：附近加油站
# =============================================================================
print("\n=== 附近加油站搜索 ===")

geo_q2 = GeoQuery(center=user_pos, radius_km=3.0, geo_type_filter=["poi"])

hits = client.retrieve(
    query="加油站",
    scope=SCOPE,
    k=5,
    full=True,
    tags=["gas_station"],
    geo_query=geo_q2,
)

print(f"  附近加油站（共 {len(hits)} 个）：")
for h in hits:
    content = h.item.content if isinstance(h.item.content, dict) else {}
    print(
        f"    [{h.score:.3f}] {content.get('name','?')} "
        f"@ ({content.get('geo', {}).get('lat','?')}, {content.get('geo', {}).get('lon','?')})"
    )

# =============================================================================
# 4. 向量语义搜索对比：关键词 vs 自然语言
# =============================================================================
print("\n=== 向量语义搜索对比 ===")
print("  场景：用户不记得具体商家名，用自然语言描述意图")

# 关键词查询（FTS 依赖词语精确匹配）
kw_hits = client.retrieve(query="餐厅 吃饭 好吃", scope=SCOPE, k=3, full=True, geo_query=geo_q)
# 自然语言查询（向量召回理解意图语义，LLM 重排综合相关性）
nl_hits = client.retrieve(
    query="附近找个口碑好的地方吃饭，最好评分高一点",
    scope=SCOPE, k=3, full=True, geo_query=geo_q,
)

print(f"\n  关键词查询「餐厅 吃饭 好吃」：")
for h in kw_hits:
    c = h.item.content if isinstance(h.item.content, dict) else {}
    print(f"    [{h.score:.3f}] {c.get('name','?')}  评分={c.get('rating','?')}")

print(f"\n  自然语言查询「附近找个口碑好的地方吃饭，最好评分高一点」：")
for h in nl_hits:
    c = h.item.content if isinstance(h.item.content, dict) else {}
    abstract = h.item.abstract or ""
    print(f"    [{h.score:.3f}] {c.get('name','?')}  评分={c.get('rating','?')}"
          + (f"\n           摘要: {abstract}" if abstract else ""))

# =============================================================================
# 5. POI 聚合演示（GeoAwareMerger）
# =============================================================================
print("\n=== POI 聚合：将西单周边相似 POI 合并为知识节点 ===")

from contextseek.evolution.merger import GeoAwareMerger

# 查询西单附近的 POI 作为待聚合候选
geo_q3 = GeoQuery(center=GeoPoint(lat=39.9120, lon=116.3728), radius_km=0.3, geo_type_filter=["poi"])
candidates_hits = client.retrieve(query="购物 地铁 餐厅", scope=SCOPE, k=20, full=True, geo_query=geo_q3)
candidate_items = [h.item for h in candidates_hits]

if len(candidate_items) >= 2:
    merger = GeoAwareMerger(
        similarity_threshold=0.5,
        min_cluster_size=2,
        spatial_merge_threshold_m=200.0,  # 200m 内的同类 POI 触发合并
    )
    kept, archived = merger.merge(candidate_items)
    print(f"  候选 {len(candidate_items)} 条 → 合并后保留 {len(kept)} 条，归档 {len(archived)} 条")
    knowledge_items = [it for it in kept if it.stage == cs.Stage.knowledge]
    print(f"  新增 knowledge 节点 {len(knowledge_items) - len([it for it in candidate_items if it.stage == cs.Stage.knowledge])} 个")
else:
    print(f"  候选条数不足（{len(candidate_items)}），跳过聚合演示")

print("\n✓ 地图 POI 搜索示例完成")
