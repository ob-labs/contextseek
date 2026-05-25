# GIS Examples

地理空间场景示例，展示 ContextSeek 在位置感知应用中的能力。

## 运行前提（所有示例通用）

- OceanBase >= 4.2.2
- 环境变量：

```bash
export STORAGE_BACKEND=oceanbase
export GEO_ENABLED=true
# OceanBase 连接参数
export OB_HOST=127.0.0.1
export OB_PORT=2881
export OB_USER=root@test
export OB_PASSWORD=
export OB_DB_NAME=contextseek
```

也可以将上述变量写入项目根目录的 `.env` 文件（参考 `.env.example` 的第 1.1 节）。

---

## 学习路径

```
【GIS 检索基础】                        【GIS 记忆应用】（先跑基础，再跑这里）

  poi_search.py                           gis_memory.py   ← 必须先运行
     半径搜索 + geo_decay_score                GPS 轨迹 → 地点事实 → 行为知识
         ↓                                        ↓
  ride_hailing.py                         smart_swap.py
     多边形 + 路线走廊召回                     GIS 记忆驱动的分层换电推荐

  autonomous_driving.py
     ODD 区域判断 + HD 地图 + 事件证据链
```

**两类性质不同，独立运行：**
- **GIS 检索基础**：演示各类地理查询 API 的用法（半径、多边形、走廊、区域判断），各文件相互独立，可任意选读
- **GIS 记忆应用**：演示如何从真实行驶轨迹中积累空间知识，再将其用于智能推荐；`gis_memory.py` 是 `smart_swap.py` 的前置条件

---

## poi_search.py — 地图 POI 搜索

```bash
uv run python examples/gis/poi_search.py
```

演示：
- 批量写入 POI（餐厅、加油站、地铁站等）
- 关键词 + 地理位置双路混合召回（phrase + geo）
- `geo_decay_score` 对远距离 POI 降权
- `GeoAwareMerger` 聚合附近 POI 到 knowledge 层

---

## ride_hailing.py — 打车调度场景

```bash
uv run python examples/gis/ride_hailing.py
```

演示：
- 写入司机位置（driver）、乘客订单（order）、热力区域（zone）
- 按乘客位置召回附近司机（半径搜索）
- 按多边形区域召回区域内活跃订单
- 沿路线走廊搜索途经司机（route_wkt）

---

## autonomous_driving.py — 智能驾驶场景

```bash
uv run python examples/gis/autonomous_driving.py
```

演示：
- 写入高精地图要素：HD 道路、车道线、交叉路口
- 写入 ODD 区域（Operational Design Domain）
- 写入实时道路事件（施工、事故、限速）
- ODD 边界判断：车辆是否在可运营区域内（`is_point_within_zone`）
- 自动驾驶决策点附近的上下文召回
- 证据链：路况事件如何影响决策层知识

---

## gis_memory.py — GIS 记忆生成（运行 smart_swap.py 前必跑）

```bash
uv run python examples/gis/gis_memory.py
```

演示：
- 写入原始 GPS 停靠点（raw 层）：5 个工作日 + 1 个周末，共 28 条
- 提炼高频地点（extracted 层）：家、公司、孩子学校、配偶公司、课外班
- 生成行为知识（knowledge 层）：
  - 工作日通勤链：送娃(07:45) → 送人(08:15) → 公司(08:55)
  - 周末活动模式：孩子课外班 2h，家长在附近逛商场等待
  - 15km 日常生活圈（覆盖 87% 行程）
- 验证：基于 GIS 记忆回答"最近常去哪里、周末做什么"

---

## smart_swap.py — GIS 记忆驱动的智能换电推荐

> **前提：先运行 `gis_memory.py`**，否则场景 1/2 降级为常规推荐

```bash
uv run python examples/gis/gis_memory.py   # 写入位置记忆
uv run python examples/gis/smart_swap.py   # 使用记忆做推荐
```

核心问题：公司旁边 200m 就有第四代换电站（3 分钟、免停 1 小时），来回跑了一个月，从来没被推过——为什么？因为系统不知道你常来这里，也不知道把两件事联系起来。

演示（4 个场景）：

| 场景 | 位置记忆状态 | 推荐策略 |
|------|-------------|---------|
| 工作日下班 | 公司（本月 20 次） | **主动推送**旁边 200m 第四代站，无需导航 |
| 周末课外班 | 孩子课外班（家长等待） | 推荐商场换电站，顺便逛 2 小时 |
| 陌生区域 | 超出 15km 生活圈 | 开导航，最近站 + 等待时间排序 |
| 高速长途 | 沪杭高速路线 | 沿途站况播报 + 推荐最优服务区 + 长续航短租提示 |
