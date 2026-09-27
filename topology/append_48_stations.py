import json
import pandas as pd

# 1. 加载现有 catalog
with open('topology/stations_catalog.json', 'r', encoding='utf-8') as f:
    catalog = json.load(f)

existing_dict = {x['station_id']: x for x in catalog}
df_db = pd.read_csv('output/crops_fullmap/db_product_car_stations.csv')
df_major = pd.read_csv('topology/major_transitions.csv')

candidates = df_db[~df_db['station_id'].isin(existing_dict.keys()) & (df_db['car_count'] >= 5000) & (df_db['car_count'] < 50000)].sort_values(by='car_count', ascending=False)

def infer_station_props(sid, car_count, total_events):
    upstream = df_major[df_major['to_station'] == sid].sort_values(by='car_count', ascending=False)
    downstream = df_major[df_major['from_station'] == sid].sort_values(by='car_count', ascending=False)
    
    up_s = upstream.iloc[0]['from_station'] if len(upstream) > 0 else None
    down_s = downstream.iloc[0]['to_station'] if len(downstream) > 0 else None
    
    up_info = existing_dict.get(up_s, {})
    down_info = existing_dict.get(down_s, {})
    
    stage = up_info.get('stage') or down_info.get('stage') or "辅助工艺"
    area_code = up_info.get('area_code') or down_info.get('area_code') or ""
    
    if sid.startswith('01IS'):
        stage = "预处理"
        area_code = "L100_2" if '2' in sid or '2' in str(up_info.get('section')) else "L100_1"
        name = "L3F01"
        section = "预处理线过渡站"
    elif sid.startswith('02IS'):
        stage = "电泳"
        area_code = "L200_1"
        name = "L3F02"
        section = "电泳1线工艺站"
    elif sid.startswith('03IS'):
        stage = "电泳"
        area_code = "L200_2"
        name = "L3F02"
        section = "电泳2线工艺站"
    elif sid.startswith('06IS'):
        stage = "雪橇转运"
        area_code = "L400_1"
        name = "L3F04"
        section = "雪橇到面漆控制线移载站"
    elif sid.startswith('09IS'):
        stage = "UBS喷涂"
        area_code = "L500_1"
        name = "L3FUB"
        section = "UBS缓存与准备线站"
    elif sid.startswith('10IS'):
        stage = "粗密封"
        area_code = "L600_1"
        name = "L3F06"
        section = "PVC胶房/烘房站"
    elif sid.startswith('11IS'):
        stage = "细密封"
        area_code = "L700_1"
        name = "L3F07"
        section = "UBS缓存移载站"
    elif sid.startswith('13IS'):
        stage = "色漆喷涂"
        area_code = "L800_1"
        name = "L3FP1"
        section = "面漆1线打磨与喷房前移载站"
    elif sid.startswith('15IS'):
        stage = "色漆喷涂"
        area_code = "L800_2"
        name = "L3FP2"
        section = "面漆2线辅助移载站"
    elif sid.startswith('16IS'):
        stage = "面漆烘烤"
        area_code = "L900_1"
        name = "L3F09"
        section = "面漆1线烘房内部检测站"
    elif sid.startswith('18IS'):
        stage = "面漆检查"
        area_code = "L900_1"
        name = "L3F09"
        section = "面漆强冷/检查支线站"
    elif sid.startswith('21IS'):
        stage = "双色漆/修补"
        area_code = "L1100_1"
        name = "L3F11"
        section = "双色漆/修补喷房站"
    elif sid.startswith('22IS'):
        stage = "面漆修补"
        area_code = "L1100_1"
        name = "L3F11"
        section = "点修补返修循环枢纽站"
    elif sid.startswith('23IS'):
        stage = "报交检查"
        area_code = "L1200_1"
        name = "L3F12"
        section = "终检/报交检查站"
    elif sid.startswith('25IS'):
        stage = "报交下线"
        area_code = "L1200_1"
        name = "L3F12"
        section = "面漆报交下线移载站"
    elif sid.startswith('32IS'):
        stage = "空腔注蜡"
        area_code = "L1300_1"
        name = "L3FWX"
        section = "HRK注蜡前缓存站"
    elif sid.startswith('33IS'):
        stage = "空腔注蜡"
        area_code = "L1300_1"
        name = "L3FWX"
        section = "HRK注蜡输送线站"
    elif sid.startswith('35IS'):
        stage = "空腔注蜡"
        area_code = "L1300_1"
        name = "L3FWX"
        section = "HRK注蜡主线站"
    elif sid.startswith('41IS') or sid.startswith('42IS'):
        stage = "底盘烘干"
        area_code = "L700_2"
        name = "L3F07"
        section = "底盘高温烘房2线站" if sid.startswith('42IS') else "底盘高温烘房1线站"
    elif sid.startswith('1L360'):
        stage = "车身上线"
        name = "1L360"
        section = "涂装第2上线点"
    elif sid.startswith('1J440'):
        stage = "车身下线"
        name = "1J440"
        section = "涂装第2下线点"
    elif sid.startswith('1F250'):
        stage = "工位交接"
        name = "1F250"
        section = "预处理过渡工位"
    elif sid.startswith('1C470'):
        stage = "雪橇转运"
        name = "1C470"
        section = "电泳缓存至雪橇交接工位"
    elif 'L3ATK' in sid:
        stage = "空腔注蜡"
        name = "L3FWX"
        section = "HRK注蜡出站站"
    else:
        stage = "辅助工艺"
        name = "L3F"
        section = "辅助过渡工位"

    note = f"现场高频过车站（{car_count}车），暂定名：{section}，后续人工需要核准正式的名称"
    
    return {
        "station_id": sid,
        "name": name,
        "section": section,
        "area_code": area_code,
        "stage": stage,
        "status": "MATCHED",
        "db_total_events": int(total_events),
        "db_car_count": int(car_count),
        "note": note
    }

new_items = []
for _, r in candidates.iterrows():
    new_items.append(infer_station_props(r['station_id'], r['car_count'], r['total_events']))

print(f"原 Catalog 站点数: {len(catalog)}")
print(f"本次追加站点数: {len(new_items)}")

catalog.extend(new_items)

# 校验所有站点的字段
keys_expected = ["station_id", "name", "section", "area_code", "stage", "status", "db_total_events", "db_car_count", "note"]
for item in catalog:
    assert list(item.keys()) == keys_expected, f"字段不匹配: {item}"

# 检查是否有重复 station_id
sids = [x['station_id'] for x in catalog]
assert len(sids) == len(set(sids)), "存在重复 station_id！"

with open('topology/stations_catalog.json', 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f"成功写入！新 Catalog 站点总数: {len(catalog)}")
