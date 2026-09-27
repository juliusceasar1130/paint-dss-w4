# -*- coding: utf-8 -*-
"""数据驱动型涂装车间拓扑网络（严格基于 PG 数据库 ods.carbody_history 推断）

核心优势：
1. 真实性：不凭图片猜测，完全基于 8.4 万辆产品车（78202%）真实过站时序事件流推断。
2. 揭示隐蔽物理事实：
   - 喷房内部真实工序：先色漆 (L3ABC, Base Coat, ~28min) -> 后清漆 (L3ACC, Clear Coat, ~7min)，图纸文字排反。
   - 控制缓存400站：实际为 *IS260 主干旁的盲端/回转暂存线 (*IS260 <-> *IS400)，非串联出口。
   - 缓存出炉回流：20IS225/21IS580 下线后存在明确的返修重磨流回打磨入口 (*IS015)。
3. 边属性携带工业特征：过车量 (car_count)、转移概率 (prob)、中位节拍 (p50_sec)。
"""

import os
import sys
import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# 引入数据库连接
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'spike_w1'))
try:
    from . import common
except ImportError:
    import common

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 关注的30个面漆读写站全集
STATIONS_30 = [
    # 3线
    '15IS015', '15IS075', '15IS260', '15IS400', 'L3ACC31IS02', 'L3ABC31IS02', '18IS225', '18IS345', '21IS010', '21IS580',
    # 2线
    '14IS015', '14IS075', '14IS260', '14IS400', 'L3ACC21IS02', 'L3ABC21IS02', '17IS200', '17IS305', '20IS010', '20IS225',
    # 1线
    '13IS015', '13IS075', '13IS260', '13IS400', 'L3ACC11IS02', 'L3ABC11IS02', '16IS200', '16IS305', '19IS010', '19IS225'
]

# 物理位置参考 (基于车间相对工序布局排版)
STATION_METADATA = {
    # 3线
    "15IS015": {"name": "L3F11", "section": "面漆打磨3", "line": "3线", "pos": (0.0, 3.0)},
    "15IS075": {"name": "L3F11", "section": "面漆打磨3", "line": "3线", "pos": (1.1, 3.0)},
    "15IS260": {"name": "L3F11", "section": "面漆控制缓存3", "line": "3线", "pos": (2.3, 3.0)},
    "15IS400": {"name": "L3F11", "section": "面漆暂存3", "line": "3线", "pos": (2.3, 3.45)},
    "L3ABC31IS02": {"name": "L3ABC31", "section": "色漆喷房3", "line": "3线", "pos": (3.8, 3.0)},
    "L3ACC31IS02": {"name": "L3ACC31", "section": "清漆喷房3", "line": "3线", "pos": (4.8, 3.0)},
    "18IS225": {"name": "L3FCC3", "section": "面漆烘房3", "line": "3线", "pos": (6.0, 3.0)},
    "18IS345": {"name": "L3FCC3", "section": "面漆烘房3", "line": "3线", "pos": (6.9, 3.0)},
    "21IS010": {"name": "L3F14", "section": "面漆缓存3", "line": "3线", "pos": (7.9, 3.0)},
    "21IS580": {"name": "L3F14", "section": "面漆缓存3", "line": "3线", "pos": (8.8, 3.0)},

    # 2线
    "14IS015": {"name": "L3F10", "section": "面漆打磨2", "line": "2线", "pos": (0.0, 2.0)},
    "14IS075": {"name": "L3F10", "section": "面漆打磨2", "line": "2线", "pos": (1.1, 2.0)},
    "14IS260": {"name": "L3F10", "section": "面漆控制缓存2", "line": "2线", "pos": (2.3, 2.0)},
    "14IS400": {"name": "L3F10", "section": "面漆暂存2", "line": "2线", "pos": (2.3, 2.45)},
    "L3ABC21IS02": {"name": "L3ABC21", "section": "色漆喷房2", "line": "2线", "pos": (3.8, 2.0)},
    "L3ACC21IS02": {"name": "L3ACC21", "section": "清漆喷房2", "line": "2线", "pos": (4.8, 2.0)},
    "17IS200": {"name": "L3FCC2", "section": "面漆烘房2", "line": "2线", "pos": (6.0, 2.0)},
    "17IS305": {"name": "L3FCC2", "section": "面漆烘房2", "line": "2线", "pos": (6.9, 2.0)},
    "20IS010": {"name": "L3F13", "section": "面漆缓存2", "line": "2线", "pos": (7.9, 2.0)},
    "20IS225": {"name": "L3F13", "section": "面漆缓存2", "line": "2线", "pos": (8.8, 2.0)},

    # 1线
    "13IS015": {"name": "L3F07", "section": "面漆打磨1", "line": "1线", "pos": (0.0, 1.0)},
    "13IS075": {"name": "L3F07", "section": "面漆打磨1", "line": "1线", "pos": (1.1, 1.0)},
    "13IS260": {"name": "L3F07", "section": "面漆控制缓存1", "line": "1线", "pos": (2.3, 1.0)},
    "13IS400": {"name": "L3F07", "section": "面漆暂存1", "line": "1线", "pos": (2.3, 1.45)},
    "L3ABC11IS02": {"name": "L3ABC11", "section": "色漆喷房1", "line": "1线", "pos": (3.8, 1.0)},
    "L3ACC11IS02": {"name": "L3ACC11", "section": "清漆喷房1", "line": "1线", "pos": (4.8, 1.0)},
    "16IS200": {"name": "L3FCC1", "section": "面漆烘房1", "line": "1线", "pos": (6.0, 1.0)},
    "16IS305": {"name": "L3FCC1", "section": "面漆烘房1", "line": "1线", "pos": (6.9, 1.0)},
    "19IS010": {"name": "L3F12", "section": "面漆缓存1", "line": "1线", "pos": (7.9, 1.0)},
    "19IS225": {"name": "L3F12", "section": "面漆缓存1", "line": "1线", "pos": (8.8, 1.0)},
}


def query_db_transitions(min_cars: int = 50) -> pd.DataFrame:
    """从数据库聚合计算连续转移边数据"""
    conn = common.connect()
    cur = conn.cursor()
    st_tuple = "('" + "','".join(STATIONS_30) + "')"

    sql = f"""
    WITH filtered AS (
        SELECT 
            "BODY_ID" AS body,
            "DATE_EVT" AS t,
            "ID" AS id,
            "RW_STATION_ID" AS station
        FROM ods.carbody_history
        WHERE "BODY_ID" LIKE '78202%'
          AND "RW_STATION_ID" IN {st_tuple}
    ),
    ordered AS (
        SELECT 
            body, t, id, station,
            LAG(station) OVER (PARTITION BY body ORDER BY t, id) AS prev_station,
            LAG(t) OVER (PARTITION BY body ORDER BY t, id) AS prev_t
        FROM filtered
    )
    SELECT 
        prev_station AS from_station,
        station AS to_station,
        COUNT(*) AS pass_count,
        COUNT(DISTINCT body) AS car_count,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (t - prev_t)))::numeric, 1) AS p50_seconds
    FROM ordered
    WHERE prev_station IS NOT NULL
      AND prev_station <> station
    GROUP BY prev_station, station
    HAVING COUNT(DISTINCT body) >= {min_cars}
    ORDER BY prev_station, car_count DESC;
    """
    cur.execute(sql)
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=['from_station', 'to_station', 'pass_count', 'car_count', 'p50_seconds'])

    # 计算出站转移概率
    total_cars_per_source = df.groupby('from_station')['car_count'].transform('sum')
    df['prob'] = (df['car_count'] / total_cars_per_source).round(4)
    return df


def build_data_driven_graph(min_cars: int = 100) -> nx.DiGraph:
    """基于数据库转移事实构建 NetworkX 有向图"""
    G = nx.DiGraph()

    # 1. 添加节点与物理坐标
    for station_id, meta in STATION_METADATA.items():
        G.add_node(station_id, **meta)

    # 2. 从数据库抽取并添加边
    df_edges = query_db_transitions(min_cars=min_cars)

    for _, row in df_edges.iterrows():
        u = row['from_station']
        v = row['to_station']
        if u in G and v in G:
            prob = row['prob']
            cars = int(row['car_count'])
            p50 = float(row['p50_seconds'])

            # 分类边性质
            if prob >= 0.4:
                flow_type = "MAJOR_FLOW"       # 主工艺直行
            elif "400" in u or "400" in v:
                flow_type = "BUFFER_SHUTTLE"   # 暂存区往复
            elif p50 > 1800 and ("015" in v or "075" in v):
                flow_type = "REWORK_RETURN"    # 返修重磨回流
            else:
                flow_type = "DISPATCH_BRANCH"  # 调度分流支线

            G.add_edge(u, v,
                       pass_count=int(row['pass_count']),
                       car_count=cars,
                       p50_seconds=p50,
                       prob=prob,
                       flow_type=flow_type)

    return G


def visualize_data_driven_graph(G: nx.DiGraph, output_file: str = "output/paint_shop_data_driven.png"):
    """渲染数据驱动的真实流向图，线宽与过车量成正比，颜色体现流向类型"""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    fig, ax = plt.subplots(figsize=(20, 9), dpi=200)

    pos = nx.get_node_attributes(G, 'pos')

    # 背景工段分块
    stages_box = [
        ("面漆打磨段", -0.5, 1.8, "#E8F5E9", "#81C784"),
        ("控制缓存/暂存", 1.9, 1.4, "#E1F5FE", "#4FC3F7"),
        ("面漆喷房 (先Base色漆->后Clear清漆)", 3.4, 1.8, "#EDE7F6", "#9575CD"),
        ("面漆烘房", 5.6, 1.6, "#FFEBEE", "#E57373"),
        ("面漆下线缓存", 7.5, 1.6, "#E0F2F1", "#4DB6AC")
    ]
    for label, x_start, width, bgcolor, bordercolor in stages_box:
        rect = patches.FancyBboxPatch((x_start, 0.4), width, 3.3,
                                      boxstyle="round,pad=0.1",
                                      facecolor=bgcolor, edgecolor=bordercolor,
                                      linestyle="--", alpha=0.45, zorder=0)
        ax.add_patch(rect)
        ax.text(x_start + width/2, 3.8, label, fontsize=10.5, fontweight='bold',
                ha='center', va='center', color='#37474F')

    # 绘制边 (线宽按车流对数比例缩放，颜色区分类型)
    for u, v, d in G.edges(data=True):
        t = d.get("flow_type", "MAJOR_FLOW")
        cars = d.get("car_count", 0)
        p50 = d.get("p50_seconds", 0)
        p_u = pos[u]
        p_v = pos[v]

        # 动态线宽
        lw = 1.0 + (cars / 25000.0)
        lw = min(max(lw, 1.2), 4.5)

        if t == "MAJOR_FLOW":
            color = "#2E7D32" if cars > 10000 else "#37474F"
            style = "-"
            rad = 0.0
            zorder = 3
        elif t == "BUFFER_SHUTTLE":
            color = "#0288D1"
            style = "--"
            rad = 0.2 if p_u[1] < p_v[1] else -0.2
            zorder = 2
        elif t == "REWORK_RETURN":
            color = "#D81B60"
            style = "-."
            rad = -0.3
            zorder = 2
        else:
            color = "#FB8C00"
            style = ":"
            rad = 0.15
            zorder = 2

        ax.annotate("", xy=p_v, xytext=p_u,
                    arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, ls=style,
                                    shrinkA=13, shrinkB=13, connectionstyle=f"arc3,rad={rad}"),
                    zorder=zorder)

    # 绘制节点
    for node, d in G.nodes(data=True):
        x, y = pos[node]
        line = d.get("line", "")

        if line == "3线":
            n_color = "#2E7D32"
        elif line == "2线":
            n_color = "#1976D2"
        else:
            n_color = "#00796B"

        ax.scatter(x, y, s=1100, c=n_color, marker="h", edgecolors="#263238", linewidths=1.2, zorder=4)

        # 标注文本：上方逻辑名称，中心站点ID
        name = d.get("name", "")
        ax.text(x, y + 0.17, name, fontsize=8, fontweight='bold', ha='center', va='bottom', color='#212121')
        disp_id = node if len(node) <= 8 else (node[:7] + "..\n" + node[7:])
        ax.text(x, y, disp_id, fontsize=6.2, fontweight='bold', ha='center', va='center', color='white', zorder=5)

    # 图例
    custom_legend = [
        plt.Line2D([0], [0], color='#2E7D32', lw=3.5, label='主干生产流 (>80,000 车)'),
        plt.Line2D([0], [0], color='#37474F', lw=2.0, label='普通主干流 (8,000~10,000 车)'),
        plt.Line2D([0], [0], color='#0288D1', lw=1.8, ls='--', label='暂存区往复流 (*IS400 盲端暂存)'),
        plt.Line2D([0], [0], color='#FB8C00', lw=1.5, ls=':', label='跨线/调度分流'),
        plt.Line2D([0], [0], color='#D81B60', lw=1.8, ls='-.', label='出炉后返修重磨回流 (流向打磨入口)'),
        plt.Line2D([0], [0], marker='h', color='w', markerfacecolor='#2E7D32', markersize=10, label='3线工位'),
        plt.Line2D([0], [0], marker='h', color='w', markerfacecolor='#1976D2', markersize=10, label='2线工位'),
        plt.Line2D([0], [0], marker='h', color='w', markerfacecolor='#00796B', markersize=10, label='1线工位'),
    ]
    ax.legend(handles=custom_legend, loc='lower right', framealpha=0.92, fontsize=8.5)

    ax.set_title("涂装车间面漆读写站拓扑图 (基于 PG 数据库 8.4 万辆产品车真实事件流推断)", fontsize=15, pad=15, fontweight='bold')
    ax.set_xlim(-0.7, 9.6)
    ax.set_ylim(0.4, 4.0)
    ax.axis('off')
    plt.tight_layout()
    plt.savefig(output_file, bbox_inches='tight')
    plt.close()
    print(f"\n[可视化输出] 数据驱动拓扑图已生成并保存至: {output_file}")


if __name__ == "__main__":
    print("正在从 PG 数据库抽取产品车转移流...")
    g = build_data_driven_graph(min_cars=100)
    print(f"成功构建数据驱动拓扑图：{g.number_of_nodes()} 个节点, {g.number_of_edges()} 条真实转移边")
    visualize_data_driven_graph(g, "output/paint_shop_data_driven.png")
