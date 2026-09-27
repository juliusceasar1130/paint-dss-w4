import os
import json
import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# 1. 字体配置 (Windows 兼容)
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Segoe UI', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 2. 加载节点数据
catalog_path = 'topology/stations_catalog.json'
with open(catalog_path, 'r', encoding='utf-8') as f:
    catalog = json.load(f)

# 过滤掉纯空雪橇站点
active_nodes = {x['station_id']: x for x in catalog if x.get('status') != 'EXCLUDED_EMPTY_SKID'}

# 3. 加载边数据
transitions_path = 'topology/major_transitions.csv'
df_edges = pd.read_csv(transitions_path)
# 只保留端点在活跃节点中的边
valid_edges = df_edges[(df_edges['from_station'].isin(active_nodes)) & (df_edges['to_station'].isin(active_nodes))].copy()

# 4. 构建 NetworkX 有向图
G = nx.DiGraph()

for sid, info in active_nodes.items():
    G.add_node(
        sid,
        name=info.get('name', ''),
        section=info.get('section', ''),
        area_code=info.get('area_code', ''),
        stage=info.get('stage', '未明确'),
        car_count=int(info.get('db_car_count', 0)),
        events=int(info.get('db_total_events', 0)),
        note=info.get('note', '')
    )

for _, r in valid_edges.iterrows():
    G.add_edge(
        r['from_station'],
        r['to_station'],
        car_count=int(r['car_count']),
        transition_count=int(r['transition_count']),
        duration_median=float(r['median_duration_min']),
        duration_p90=float(r['p90_duration_min']),
        weight=round(float(r['median_duration_min']), 2)
    )

print(f"=== 涂装全车间拓扑网络构建成功 ===")
print(f"活跃车身节点数: {G.number_of_nodes()} 站")
print(f"数据驱动有效边数: {G.number_of_edges()} 条")

# 5. 拓扑结构与图论指标分析
in_degrees = dict(G.in_degree())
out_degrees = dict(G.out_degree())

sources = [n for n, deg in in_degrees.items() if deg == 0 and out_degrees[n] > 0]
sinks = [n for n, deg in out_degrees.items() if deg == 0 and in_degrees[n] > 0]
isolated = [n for n, deg in in_degrees.items() if deg == 0 and out_degrees[n] == 0]

# 识别关键分流与合流枢纽
split_hubs = sorted([(n, out_degrees[n], G.nodes[n]['car_count']) for n in G.nodes() if out_degrees[n] >= 3], key=lambda x: x[1], reverse=True)
merge_hubs = sorted([(n, in_degrees[n], G.nodes[n]['car_count']) for n in G.nodes() if in_degrees[n] >= 3], key=lambda x: x[1], reverse=True)

# 强连通分量 (返修与重循环)
sccs = [c for c in nx.strongly_connected_components(G) if len(c) > 1]

# 6. 导出标准数据格式
os.makedirs('output', exist_ok=True)

# 6.1 导出 GraphML (Gephi / yEd 格式)
graphml_path = 'topology/shop_topology.graphml'
nx.write_graphml(G, graphml_path)
print(f"已导出 GraphML 拓扑文件: {graphml_path}")

# 6.2 导出 Cytoscape / ECharts JSON 格式
graph_json = {
    "nodes": [
        {
            "id": n,
            "label": f"{n}\n({d['section']})",
            "section": d['section'],
            "stage": d['stage'],
            "car_count": d['car_count'],
            "events": d['events'],
            "in_degree": in_degrees[n],
            "out_degree": out_degrees[n]
        }
        for n, d in G.nodes(data=True)
    ],
    "links": [
        {
            "source": u,
            "target": v,
            "car_count": d['car_count'],
            "duration_median": d['duration_median'],
            "duration_p90": d['duration_p90']
        }
        for u, v, d in G.edges(data=True)
    ]
}
with open('topology/shop_topology.json', 'w', encoding='utf-8') as f:
    json.dump(graph_json, f, ensure_ascii=False, indent=2)
print("已导出 JSON 拓扑文件: topology/shop_topology.json")

# 7. 工艺阶段层次化坐标布局算法 (Stage Hierarchical Layout)
# 完整映射现有 catalog 中的 36 种细分 stage 字符串
stage_order = {
    # 0. 预处理与车身上线
    "车身上线": 0, "预处理": 0, "前处理": 0, "工位交接": 0,
    # 1. 电泳工艺
    "电泳": 1, "电泳缓存": 1, "电泳检查": 1,
    # 2. 雪橇转运
    "雪橇转运": 2, "雪橇缓存": 2,
    # 3. 粗密封与PVC
    "粗密封": 3, "PVC转运": 3,
    # 4. UBS与底盘
    "UBS喷涂": 4, "UBS缓存": 4, "底盘烘干": 4, "底盘喷涂/烘房": 4,
    # 5. 细密封与准备
    "细密封": 5, "UBS审计": 5, "返修准备": 5, "辅助工艺": 5, "未明确": 5,
    # 6. 色漆喷涂与打磨
    "面漆打磨": 6, "面漆喷房": 6, "面漆控制缓存": 6, "面漆缓存": 6, "色漆喷涂": 6,
    # 7. 面漆烘房与检查
    "面漆烘房": 7, "面漆烘烤": 7, "面漆检查": 7,
    # 8. 双色漆与修补
    "点修补": 8, "双色漆/修补": 8, "面漆修补": 8,
    # 9. 报交与出站
    "报交": 9, "报交检查": 9, "报交下线": 9, "车身下线": 9, "出站连廊": 9,
    # 10. 空腔注蜡 (HRK)
    "空腔注蜡": 10, "HRK": 10, "注蜡下线": 10
}

# 颜色映射 (11 个阶段)
stage_colors = {
    0: "#2980b9",  # 预处理 / 上线 - 蓝
    1: "#1abc9c",  # 电泳 - 青
    2: "#16a085",  # 雪橇转运 - 蓝绿
    3: "#27ae60",  # 粗密封 - 绿
    4: "#f39c12",  # UBS / 底盘 - 橙黄
    5: "#e67e22",  # 细密封 - 橙
    6: "#e74c3c",  # 面漆喷涂 - 红
    7: "#c0392b",  # 面漆烘烤 - 深红
    8: "#8e44ad",  # 修补/双色 - 紫
    9: "#2c3e50",  # 报交/下线 - 蓝灰
    10: "#34495e"  # 注蜡 - 深灰
}

# 分组并计算每个节点的 (x, y) 坐标
pos = {}
stage_buckets = {i: [] for i in range(11)}

# 先按阶段和车流量排序
for n in sorted(G.nodes(), key=lambda x: (stage_order.get(G.nodes[x]['stage'], 5), -G.nodes[x]['car_count'])):
    s_idx = stage_order.get(G.nodes[n]['stage'], 5)
    stage_buckets[s_idx].append(n)

for s_idx, nodes_in_stage in stage_buckets.items():
    k = len(nodes_in_stage)
    if k == 0:
        continue
    # Y 轴跨度从 -11 到 11 均匀分布
    for rank, n in enumerate(nodes_in_stage):
        y = 11.5 - (23.0 * rank / max(k - 1, 1)) if k > 1 else 0.0
        # 错列微调，增加视觉清晰度
        x = s_idx * 5.2 + ((rank % 2) - 0.5) * 0.7
        pos[n] = (x, y)

# 8. 绘制高清全厂拓扑图
plt.figure(figsize=(42, 24), dpi=220)
ax = plt.gca()
ax.set_facecolor('#fafbfc')

# 绘制阶段背景分隔竖线与标签
stage_names = [
    "1. 上线/预处理", "2. 电泳工艺", "3. 雪橇转运", "4. 粗密封/PVC", 
    "5. UBS与底盘", "6. 细密封/准备", "7. 色漆喷涂", "8. 面漆烘房/强冷", 
    "9. 双色漆/修补", "10. 终检与报交", "11. 空腔注蜡"
]
for idx, name in enumerate(stage_names):
    x_center = idx * 5.2
    plt.axvline(x=x_center - 2.6, color='#e2e8f0', linestyle='--', linewidth=1.2, zorder=0)
    plt.text(x_center, 13.5, name, fontsize=16, fontweight='bold', color='#1e293b', 
             ha='center', va='center', bbox=dict(boxstyle='round,pad=0.5', facecolor='#eef2f6', edgecolor='#cbd5e1'))

# 绘制边 (按车流量设定宽度与透明度)
for u, v, d in G.edges(data=True):
    car_cnt = d['car_count']
    x_from = pos[u][0]
    x_to = pos[v][0]
    # 判断是否为返修边 (反向跨越工段)
    is_feedback = x_to < x_from - 1.5
    
    if is_feedback:
        edge_color = '#e74c3c'  # 鲜红醒目返修线
        width = min(max(car_cnt / 10000.0, 1.2), 4.5)
        alpha = 0.75
        rad = 0.28
    else:
        edge_color = '#64748b'  # 标准冷灰工序线
        width = min(max(car_cnt / 16000.0, 0.8), 5.0)
        alpha = min(max(car_cnt / 85000.0, 0.28), 0.85)
        rad = 0.04
    
    nx.draw_networkx_edges(
        G, pos, edgelist=[(u, v)],
        width=width, alpha=alpha, edge_color=edge_color,
        arrows=True, arrowsize=13, arrowstyle='-|>',
        connectionstyle=f'arc3,rad={rad}',
        ax=ax
    )

# 绘制节点 (按车辆数设定大小)
node_colors = [stage_colors.get(stage_order.get(G.nodes[n]['stage'], 5), "#94a3b8") for n in G.nodes()]
node_sizes = [min(max(G.nodes[n]['car_count'] / 50.0, 220), 2400) for n in G.nodes()]

nx.draw_networkx_nodes(
    G, pos,
    node_size=node_sizes,
    node_color=node_colors,
    edgecolors='#0f172a',
    linewidths=1.2,
    alpha=0.92,
    ax=ax
)

# 绘制标签 (只显示 station_id)
labels = {n: n for n in G.nodes()}
nx.draw_networkx_labels(
    G, pos,
    labels=labels,
    font_size=7.5,
    font_family='Microsoft YaHei',
    font_weight='bold',
    font_color='#0f172a',
    ax=ax
)

# 图表标题与注释
plt.title("涂装车间全厂读写站拓扑网络 (Data-Driven NetworkX Topology Graph)\n基于 84,274 辆真实车身过站时序差分挖掘 | 184 活跃站点 · 306 条转移边 | 红线为返修/回流回路", 
          fontsize=22, fontweight='bold', pad=32, color='#0f172a')

# 添加图例
legend_elements = [
    plt.Line2D([0], [0], color='#64748b', lw=3, label='标准前向工艺流'),
    plt.Line2D([0], [0], color='#e74c3c', lw=3, label='返修/回流循环 (Feedback)'),
    plt.scatter([0], [0], s=300, c='#e74c3c', edgecolors='#0f172a', label='高流量主干站 (≥5万车)'),
    plt.scatter([0], [0], s=120, c='#3498db', edgecolors='#0f172a', label='中频工艺站 (5千~5万车)')
]
ax.legend(handles=legend_elements, loc='upper right', bbox_to_anchor=(0.99, 0.98), fontsize=13, framealpha=0.95)

plt.xlim(-4.0, 56.0)
plt.ylim(-13.0, 15.0)
plt.axis('off')
plt.tight_layout()

output_png = 'output/full_shop_topology.png'
plt.savefig(output_png, dpi=220, bbox_inches='tight')
plt.close()
print(f"已成功渲染并保存全车间高清拓扑可视化图: {output_png}")
