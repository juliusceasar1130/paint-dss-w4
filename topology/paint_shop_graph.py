# -*- coding: utf-8 -*-
"""涂装车间面漆读写站拓扑图建模（基于 NetworkX，v3 精确业务定制版）

规则调整：
1. 面漆打磨1线直连面漆喷房1线 (13IS075 -> L3ACC11IS02)，中间可分流进入面漆控制缓存线1 (13IS260 -> 13IS400 -> L3ACC11IS02)
2. 不连接至 L3F07（返修准备线1），完全移除返修节点与外部虚拟节点
3. 21IS010 不需要外部虚线流入，保持主线直通
4. 包含 3 条产线共 30 个真实物理读写站（每线 10 站），结构高度清晰对称
"""

import os
import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# 设置中文字体（Windows 环境常用字体回退）
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def build_paint_shop_graph() -> nx.DiGraph:
    """构建涂装车间面漆读写站有向拓扑图"""
    G = nx.DiGraph()

    # 1. 30个物理读写站定义 (x=工序阶段, y=产线高度)
    nodes_data = [
        # ======= 3线 (上层) =======
        ("15IS015", {"name": "L3F11", "section": "面漆打磨3", "line": "3线", "stage": "打磨", "role": "入口", "pos": (0.0, 3.0)}),
        ("15IS075", {"name": "L3F11", "section": "面漆打磨3", "line": "3线", "stage": "打磨", "role": "出口", "pos": (1.1, 3.0)}),
        ("15IS260", {"name": "L3F11", "section": "面漆控制缓存线3", "line": "3线", "stage": "控制缓存", "role": "入口", "pos": (2.3, 3.0)}),
        ("15IS400", {"name": "L3F11", "section": "面漆控制缓存线3", "line": "3线", "stage": "控制缓存", "role": "出口", "pos": (3.1, 3.0)}),
        ("L3ACC31IS02", {"name": "L3ACC31", "section": "面漆喷房3", "line": "3线", "stage": "喷房ACC", "role": "色漆/中涂", "pos": (4.3, 3.0)}),
        ("L3ABC31IS02", {"name": "L3ABC31", "section": "面漆喷房3", "line": "3线", "stage": "喷房ABC", "role": "清漆/面漆", "pos": (5.1, 3.0)}),
        ("18IS225", {"name": "L3FCC3", "section": "面漆烘房线3", "line": "3线", "stage": "烘房", "role": "入口升温", "pos": (6.1, 3.0)}),
        ("18IS345", {"name": "L3FCC3", "section": "面漆烘房线3", "line": "3线", "stage": "烘房", "role": "出口降温", "pos": (6.9, 3.0)}),
        ("21IS010", {"name": "L3F14", "section": "面漆缓存线3", "line": "3线", "stage": "面漆缓存", "role": "入口", "pos": (7.9, 3.0)}),
        ("21IS580", {"name": "L3F14", "section": "面漆缓存线3", "line": "3线", "stage": "面漆缓存", "role": "出口", "pos": (8.8, 3.0)}),

        # ======= 2线 (中层) =======
        ("14IS015", {"name": "L3F10", "section": "面漆打磨2", "line": "2线", "stage": "打磨", "role": "入口", "pos": (0.0, 2.0)}),
        ("14IS075", {"name": "L3F10", "section": "面漆打磨2", "line": "2线", "stage": "打磨", "role": "出口", "pos": (1.1, 2.0)}),
        ("14IS260", {"name": "L3F10", "section": "面漆控制缓存线2", "line": "2线", "stage": "控制缓存", "role": "入口", "pos": (2.3, 2.0)}),
        ("14IS400", {"name": "L3F10", "section": "面漆控制缓存线2", "line": "2线", "stage": "控制缓存", "role": "出口", "pos": (3.1, 2.0)}),
        ("L3ACC21IS02", {"name": "L3ACC21", "section": "面漆喷房2", "line": "2线", "stage": "喷房ACC", "role": "色漆/中涂", "pos": (4.3, 2.0)}),
        ("L3ABC21IS02", {"name": "L3ABC21", "section": "面漆喷房2", "line": "2线", "stage": "喷房ABC", "role": "清漆/面漆", "pos": (5.1, 2.0)}),
        ("17IS200", {"name": "L3FCC2", "section": "面漆烘房线2", "line": "2线", "stage": "烘房", "role": "入口升温", "pos": (6.1, 2.0)}),
        ("17IS305", {"name": "L3FCC2", "section": "面漆烘房线2", "line": "2线", "stage": "烘房", "role": "出口降温", "pos": (6.9, 2.0)}),
        ("20IS010", {"name": "L3F13", "section": "面漆缓存线2", "line": "2线", "stage": "面漆缓存", "role": "入口", "pos": (7.9, 2.0)}),
        ("20IS225", {"name": "L3F13", "section": "面漆缓存线2", "line": "2线", "stage": "面漆缓存", "role": "出口", "pos": (8.8, 2.0)}),

        # ======= 1线 (下层) =======
        ("13IS015", {"name": "L3F07", "section": "面漆打磨1", "line": "1线", "stage": "打磨", "role": "入口", "pos": (0.0, 0.9)}),
        ("13IS075", {"name": "L3F07", "section": "面漆打磨1", "line": "1线", "stage": "打磨", "role": "出口", "pos": (1.1, 0.9)}),
        # 缓存1作为并联可选缓冲，略微偏上布局，体现直通主路与可选缓存的分流关系
        ("13IS260", {"name": "L3F07", "section": "面漆控制缓存线1", "line": "1线", "stage": "控制缓存", "role": "入口", "pos": (2.3, 1.35)}),
        ("13IS400", {"name": "L3F07", "section": "面漆控制缓存线1", "line": "1线", "stage": "控制缓存", "role": "出口", "pos": (3.1, 1.35)}),
        ("L3ACC11IS02", {"name": "L3ACC11", "section": "面漆喷房1", "line": "1线", "stage": "喷房ACC", "role": "色漆/中涂", "pos": (4.3, 0.9)}),
        ("L3ABC11IS02", {"name": "L3ABC11", "section": "面漆喷房1", "line": "1线", "stage": "喷房ABC", "role": "清漆/面漆", "pos": (5.1, 0.9)}),
        ("16IS200", {"name": "L3FCC1", "section": "面漆烘房线1", "line": "1线", "stage": "烘房", "role": "入口升温", "pos": (6.1, 0.9)}),
        ("16IS305", {"name": "L3FCC1", "section": "面漆烘房线1", "line": "1线", "stage": "烘房", "role": "出口降温", "pos": (6.9, 0.9)}),
        ("19IS010", {"name": "L3F12", "section": "面漆缓存线1", "line": "1线", "stage": "面漆缓存", "role": "入口", "pos": (7.9, 0.9)}),
        ("19IS225", {"name": "L3F12", "section": "面漆缓存线1", "line": "1线", "stage": "面漆缓存", "role": "出口", "pos": (8.8, 0.9)}),
    ]

    for node_id, attrs in nodes_data:
        G.add_node(node_id, **attrs)

    # 2. 前后道工艺连接边定义
    edges_data = [
        # --- 3线 主工艺流 ---
        ("15IS015", "15IS075", {"type": "LINE_PROCESS", "desc": "打磨3加工"}),
        ("15IS075", "15IS015", {"type": "POLISH_LOOP", "desc": "打磨3往复循环"}),
        ("15IS075", "15IS260", {"type": "LINE_PROCESS", "desc": "打磨3出进控制缓存3"}),
        ("15IS260", "15IS400", {"type": "LINE_PROCESS", "desc": "缓存3排队待喷"}),
        ("15IS400", "L3ACC31IS02", {"type": "LINE_PROCESS", "desc": "进喷房3色漆/中涂"}),
        ("L3ACC31IS02", "L3ABC31IS02", {"type": "LINE_PROCESS", "desc": "色漆完成进清漆"}),
        ("L3ABC31IS02", "18IS225", {"type": "LINE_PROCESS", "desc": "喷涂完进烘房3"}),
        ("18IS225", "18IS345", {"type": "LINE_PROCESS", "desc": "烘房3烘烤"}),
        ("18IS345", "21IS010", {"type": "LINE_PROCESS", "desc": "出烘房进面漆缓存3"}),
        ("21IS010", "21IS580", {"type": "LINE_PROCESS", "desc": "面漆缓存3输送"}),

        # --- 2线 主工艺流 ---
        ("14IS015", "14IS075", {"type": "LINE_PROCESS", "desc": "打磨2加工"}),
        ("14IS075", "14IS015", {"type": "POLISH_LOOP", "desc": "打磨2往复循环"}),
        ("14IS075", "14IS260", {"type": "LINE_PROCESS", "desc": "打磨2出进控制缓存2"}),
        ("14IS260", "14IS400", {"type": "LINE_PROCESS", "desc": "缓存2排队待喷"}),
        ("14IS400", "L3ACC21IS02", {"type": "LINE_PROCESS", "desc": "进喷房2色漆/中涂"}),
        ("L3ACC21IS02", "L3ABC21IS02", {"type": "LINE_PROCESS", "desc": "色漆完成进清漆"}),
        ("L3ABC21IS02", "17IS200", {"type": "LINE_PROCESS", "desc": "喷涂完进烘房2"}),
        ("17IS200", "17IS305", {"type": "LINE_PROCESS", "desc": "烘房2烘烤"}),
        ("17IS305", "20IS010", {"type": "LINE_PROCESS", "desc": "出烘房进面漆缓存2"}),
        ("20IS010", "20IS225", {"type": "LINE_PROCESS", "desc": "面漆缓存2输送"}),

        # --- 1线 主工艺流（打磨1直连喷房1，中间可进控制缓存1） ---
        ("13IS015", "13IS075", {"type": "LINE_PROCESS", "desc": "打磨1加工"}),
        ("13IS075", "13IS015", {"type": "POLISH_LOOP", "desc": "打磨1往复循环"}),
        # 直通主路：面漆打磨1直接连接面漆喷房1
        ("13IS075", "L3ACC11IS02", {"type": "DIRECT_MAIN", "desc": "打磨1直连喷房1主干"}),
        # 并联可选缓存路：中间进入控制缓存1
        ("13IS075", "13IS260", {"type": "OPTIONAL_BUFFER", "desc": "分流进入控制缓存1"}),
        ("13IS260", "13IS400", {"type": "LINE_PROCESS", "desc": "缓存1排队待喷"}),
        ("13IS400", "L3ACC11IS02", {"type": "OPTIONAL_BUFFER", "desc": "出缓存1汇入喷房1"}),
        # 1线喷房至缓存直通
        ("L3ACC11IS02", "L3ABC11IS02", {"type": "LINE_PROCESS", "desc": "色漆完成进清漆"}),
        ("L3ABC11IS02", "16IS200", {"type": "LINE_PROCESS", "desc": "喷涂完进烘房1"}),
        ("16IS200", "16IS305", {"type": "LINE_PROCESS", "desc": "烘房1烘烤"}),
        ("16IS305", "19IS010", {"type": "LINE_PROCESS", "desc": "出烘房进面漆缓存1"}),
        ("19IS010", "19IS225", {"type": "LINE_PROCESS", "desc": "面漆缓存1输送"}),

        # --- 垂直分配跨线调度通道 (打磨出口垂直向上提升调度) ---
        ("13IS075", "14IS260", {"type": "CROSS_DISPATCH", "desc": "打磨1向上调度至缓存2"}),
        ("13IS075", "15IS260", {"type": "CROSS_DISPATCH", "desc": "打磨1向上调度至缓存3"}),
        ("14IS075", "15IS260", {"type": "CROSS_DISPATCH", "desc": "打磨2向上调度至缓存3"}),
    ]

    for u, v, attrs in edges_data:
        G.add_edge(u, v, **attrs)

    return G


def query_demo(G: nx.DiGraph):
    """演示常见图算法与拓扑查询"""
    print("\n" + "=" * 55)
    print("      涂装车间 NetworkX 拓扑分析 (v3 业务对齐版)")
    print("=" * 55)
    print(f"总物理读写站数 (Nodes): {G.number_of_nodes()}")
    print(f"总工序流向连接 (Edges): {G.number_of_edges()}")

    # 1. 验证 1线 从打磨1到喷房1的直通与缓存多路径
    p1 = list(nx.all_simple_paths(G, source="13IS075", target="L3ACC11IS02"))
    print(f"\n[1线路由核验] 从打磨1出口(13IS075) 到 喷房1(L3ACC11IS02) 的可行通道数: {len(p1)}")
    for i, path in enumerate(p1, 1):
        route_desc = " -> ".join(path)
        tag = "【直连主通道】" if len(path) == 2 else "【经由控制缓存1通道】"
        print(f"  路径 {i} {tag}: {route_desc}")

    # 2. 验证 21IS010 入口来源（已无虚线）
    in_edges_21 = list(G.predecessors("21IS010"))
    print(f"\n[21IS010 入口核验] 上游来源站点: {in_edges_21} (仅由烘房3 18IS345 流入)")

    # 3. 验证是否包含 L3F07 返修线
    rework_exists = "L3F07_REWORK" in G
    print(f"\n[返修线核验] 是否存在 L3F07 返修节点: {rework_exists} (已按要求完全移除)")


def visualize_paint_shop(G: nx.DiGraph, output_file: str = "output/paint_shop_topology.png"):
    """绘制高可读性的工业工位拓扑图"""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    fig, ax = plt.subplots(figsize=(20, 8), dpi=200)

    pos = nx.get_node_attributes(G, 'pos')

    # 背景区域分块底色标注
    stages_box = [
        ("面漆打磨段", -0.5, 1.8, "#E8F5E9", "#81C784"),
        ("面漆控制缓存", 2.05, 1.3, "#E1F5FE", "#4FC3F7"),
        ("面漆喷房", 3.85, 1.5, "#EDE7F6", "#9575CD"),
        ("面漆烘房", 5.65, 1.5, "#FFEBEE", "#E57373"),
        ("面漆下线缓存", 7.45, 1.6, "#E0F2F1", "#4DB6AC")
    ]
    for label, x_start, width, bgcolor, bordercolor in stages_box:
        rect = patches.FancyBboxPatch((x_start, 0.4), width, 3.1,
                                      boxstyle="round,pad=0.1",
                                      facecolor=bgcolor, edgecolor=bordercolor,
                                      linestyle="--", alpha=0.45, zorder=0)
        ax.add_patch(rect)
        ax.text(x_start + width/2, 3.65, label, fontsize=11, fontweight='bold',
                ha='center', va='center', color='#37474F')

    # 绘制各类型边
    for u, v, d in G.edges(data=True):
        t = d.get("type", "LINE_PROCESS")
        p_u = pos[u]
        p_v = pos[v]

        if t == "DIRECT_MAIN":
            # 1线打磨直通喷房的主干线（加粗醒目蓝绿色实线）
            ax.annotate("", xy=p_v, xytext=p_u,
                        arrowprops=dict(arrowstyle="-|>", color="#00897B", lw=2.4,
                                        shrinkA=13, shrinkB=13), zorder=3)
        elif t == "OPTIONAL_BUFFER":
            # 1线可选进入缓存的支线（蓝绿色短虚线）
            rad = 0.15 if p_u[1] < p_v[1] else -0.15
            ax.annotate("", xy=p_v, xytext=p_u,
                        arrowprops=dict(arrowstyle="-|>", color="#26A69A", lw=1.8, ls="--",
                                        shrinkA=13, shrinkB=13, connectionstyle=f"arc3,rad={rad}"), zorder=2)
        elif t == "LINE_PROCESS":
            # 主线正常工序（深灰实线）
            ax.annotate("", xy=p_v, xytext=p_u,
                        arrowprops=dict(arrowstyle="-|>", color="#37474F", lw=1.8,
                                        shrinkA=13, shrinkB=13), zorder=2)
        elif t == "CROSS_DISPATCH":
            # 垂直跨线调度（橙色点线）
            rad = 0.15
            ax.annotate("", xy=p_v, xytext=p_u,
                        arrowprops=dict(arrowstyle="-|>", color="#FB8C00", lw=1.4, ls=":",
                                        shrinkA=13, shrinkB=13, connectionstyle=f"arc3,rad={rad}"), zorder=2)
        elif t == "POLISH_LOOP":
            # 打磨工位内往复（浅灰弧线）
            ax.annotate("", xy=p_v, xytext=p_u,
                        arrowprops=dict(arrowstyle="-|>", color="#B0BEC5", lw=1.2, ls="--",
                                        shrinkA=12, shrinkB=12, connectionstyle="arc3,rad=-0.35"), zorder=1)

    # 绘制 30 个物理读写站节点（标准六边形）
    for node, d in G.nodes(data=True):
        x, y = pos[node]
        line = d.get("line", "")

        if line == "3线":
            n_color = "#2E7D32"  # 绿色
        elif line == "2线":
            n_color = "#1976D2"  # 蓝色
        else:
            n_color = "#00796B"  # 青绿色

        ax.scatter(x, y, s=1100, c=n_color, marker="h", edgecolors="#263238", linewidths=1.2, zorder=3)

        # 标注文本：上方为逻辑名称，中心为读写站ID
        name = d.get("name", "")
        ax.text(x, y + 0.17, name, fontsize=8, fontweight='bold', ha='center', va='bottom', color='#212121')
        disp_id = node if len(node) <= 8 else (node[:7] + "..\n" + node[7:])
        ax.text(x, y, disp_id, fontsize=6.2, fontweight='bold', ha='center', va='center', color='white', zorder=4)

    # 1线直连说明标签
    ax.text(2.7, 0.72, "1线直达通道 (13IS075 -> L3ACC11IS02)", fontsize=7.5, color="#00796B", ha="center", style="italic")
    ax.text(2.7, 1.72, "可选进缓存1 (13IS260 -> 13IS400)", fontsize=7.5, color="#26A69A", ha="center", style="italic")

    # 自定义图例
    custom_legend = [
        plt.Line2D([0], [0], color='#37474F', lw=2, label='标准主线生产流 (直行)'),
        plt.Line2D([0], [0], color='#00897B', lw=2.4, label='1线打磨直通喷房主干'),
        plt.Line2D([0], [0], color='#26A69A', lw=1.8, ls='--', label='1线可选控制缓存分支'),
        plt.Line2D([0], [0], color='#FB8C00', lw=1.5, ls=':', label='垂直跨线调度 (向2/3线)'),
        plt.Line2D([0], [0], color='#B0BEC5', lw=1.2, ls='--', label='打磨工位内往复 (循环)'),
        plt.Line2D([0], [0], marker='h', color='w', markerfacecolor='#2E7D32', markersize=10, label='3线工位'),
        plt.Line2D([0], [0], marker='h', color='w', markerfacecolor='#1976D2', markersize=10, label='2线工位'),
        plt.Line2D([0], [0], marker='h', color='w', markerfacecolor='#00796B', markersize=10, label='1线工位'),
    ]
    ax.legend(handles=custom_legend, loc='lower right', framealpha=0.92, fontsize=8.5)

    ax.set_title("涂装车间面漆工段读写站拓扑图 (业务定制精准版)", fontsize=15, pad=15, fontweight='bold')
    ax.set_xlim(-0.7, 9.6)
    ax.set_ylim(0.3, 3.9)
    ax.axis('off')
    plt.tight_layout()
    plt.savefig(output_file, bbox_inches='tight')
    plt.close()
    print(f"\n[可视化输出] 拓扑图已更新并保存至: {output_file}")


if __name__ == "__main__":
    g = build_paint_shop_graph()
    query_demo(g)
    visualize_paint_shop(g, "output/paint_shop_topology.png")
