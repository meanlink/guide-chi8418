"""场馆导览方案的独立性核验与分区转化口径。"""
import math
import random
import numpy as np
from scipy import stats


def metadata():
    return {"name": "场馆导览转化核验", "short": "guide-chi", "tagline": "把进区人数与完成讲解分开，核验导览方案的关联强度。", "client": "拾光科学馆 · 24 人运营团队，比较两套自助导览提示", "workflow": "运营员汇总分区入场和完成讲解人数，以转化率高低选择导览牌。", "pains": ["高客流分区掩盖其他区域", "未核对列联表期望频数", "把关联误写成方案因果效果"], "upstream": "匿名分区客流 SG_guide_counts.csv", "downstream": "展陈运营组据分区证据安排随机导览试点",
            "entities": ["分区导览计数", "独立性核验", "展陈评审简报"], "dataset_name": "三展区 · 20 日 A/B 导览计数",
            "columns": [{"key": "record", "label": "记录", "type": "string"}, {"key": "zone", "label": "展区", "type": "string"}, {"key": "arm", "label": "导览方案", "type": "string"}, {"key": "visitors", "label": "进区人数", "type": "number"}, {"key": "completed", "label": "完成讲解人数", "type": "number"}],
            "parameters": [{"key": "zone", "label": "分析展区", "type": "select", "default": "全部", "options": [{"value": x, "label": x} for x in ["全部", "星空厅", "水世界", "机械坊"]]}, {"key": "alpha", "label": "显著性水平", "type": "select", "default": "0.05", "options": [{"value": str(x), "label": str(x)} for x in [.01, .05, .1]]}],
            "workbench_title": "导览列联核验", "action_label": "构建列联表并检验", "unit": "人", "accent": "oklch(56% 0.15 18)", "style": "gallery",
            "seed_story": ["08:30 导入 SG_guide_counts.csv，三展区共 120 条日计数。", "08:45 核对完成数不超过进区人数。", "09:00 选择全部展区，显著性水平 0.05。", "09:05 运行核验，查看完成/未完成列联表。", "09:20 检查最小期望频数及 Cramer V。", "09:40 分别选水世界与机械坊，比较分区差异。", "10:00 导出评审简报，计划按访客随机分组的后续试点。"],
            "method": "汇总 A/B×完成/未完成 2×2 列联表，Pearson χ² 不使用连续性校正；Cramer V=√(χ²/N)；最小期望频数低于 5 时提示近似条件不足。", "limitations": ["按日汇总可能存在访客重复和聚类", "全部展区汇总可能受分区构成影响", "关联不等同于因果提升"]}


def seed_records():
    rng = random.Random(1005)
    rows = []
    for zone, base, uplift in [("星空厅", .42, .10), ("水世界", .63, .025), ("机械坊", .31, -.015)]:
        for day in range(20):
            for arm in ("A", "B"):
                visitors = rng.randint(55, 100)
                p = base + (uplift if arm == "B" else 0)
                completed = sum(rng.random() < p for _ in range(visitors))
                rows.append({"record": f"{zone}-{day+1:02}-{arm}", "zone": zone, "arm": arm, "visitors": visitors, "completed": completed})
    return rows


def analyze(records, params):
    zone = str(params.get("zone", "全部"))
    try:
        alpha = float(params.get("alpha", .05))
    except (ValueError, TypeError):
        raise ValueError("显著性水平必须为 0.01/0.05/0.1")
    if alpha not in (.01, .05, .1) or zone not in ("全部", "星空厅", "水世界", "机械坊"):
        raise ValueError("未知展区或显著性水平")
    total = {"A": [0, 0], "B": [0, 0]}
    zones = {}
    for r in records:
        try:
            v, c = float(r["visitors"]), float(r["completed"])
            name, arm = str(r["zone"]), str(r["arm"])
            if arm not in total or not all(math.isfinite(x) and x.is_integer() for x in (v, c)) or v <= 0 or not 0 <= c <= v:
                raise ValueError()
            if zone != "全部" and zone != name:
                continue
            total[arm][0] += int(c)
            total[arm][1] += int(v - c)
            bucket = zones.setdefault((name, arm), [0, 0])
            bucket[0] += int(v)
            bucket[1] += int(c)
        except (ValueError, TypeError, KeyError):
            raise ValueError("方案须为 A/B，人数为整数，且 0≤完成≤进区")
    observed = np.array([total["A"], total["B"]], dtype=float)
    if np.any(observed.sum(axis=0) == 0) or np.any(observed.sum(axis=1) == 0):
        raise ValueError("两方案与两结果类别均须有样本")
    chi, p, df, expected = stats.chi2_contingency(observed, correction=False)
    n, min_expected = int(observed.sum()), float(expected.min())
    v = math.sqrt(float(chi) / n)
    rates = {a: total[a][0] / sum(total[a]) * 100 for a in ("A", "B")}
    rows = [{"zone": name, "arm": arm, "visitors": counts[0], "completed": counts[1], "incomplete": counts[0] - counts[1], "rate": round(counts[1] / counts[0] * 100, 4)} for (name, arm), counts in sorted(zones.items())]
    warnings = ["汇总分析未控制分区和日期的访客构成，不能直接归因于导览设计。"]
    if min_expected < 5:
        warnings.append("至少一个期望频数小于 5；χ² 渐近 p 值可靠性不足，请扩大样本或采用精确检验。")
    return {"summary": f"{zone}：B−A 完成率差 {rates['B']-rates['A']:.3f} 个百分点，χ²={chi:.3f}，{'检出关联' if p < alpha else '未检出关联'}。",
            "metrics": [{"label": "Pearson χ²", "value": round(float(chi), 5)}, {"label": "p 值", "value": float(f"{p:.6g}")}, {"label": "Cramer V", "value": round(v, 5)}, {"label": "最小期望频数", "value": round(min_expected, 3)}],
            "table": {"columns": [{"key": "zone", "label": "展区"}, {"key": "arm", "label": "方案"}, {"key": "visitors", "label": "进区"}, {"key": "completed", "label": "完成"}, {"key": "incomplete", "label": "未完成"}, {"key": "rate", "label": "完成率 %"}], "rows": rows},
            "chart": {"type": "bar", "labels": ["A 方案", "B 方案"], "series": [{"name": "完成率 %", "values": [round(rates["A"], 4), round(rates["B"], 4)]}]},
            "findings": [f"A 完成 {total['A'][0]} / {sum(total['A'])}，B 完成 {total['B'][0]} / {sum(total['B'])}。", f"有效样本 N={n}，自由度={int(df)}；检验 α={alpha:g}。", "逐展区复核构成差异后再设计下一轮随机试点。"], "method": metadata()["method"], "warnings": warnings}
