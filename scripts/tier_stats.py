"""任务档位统计（docs/adr/0008）：最近 N 天每个项目派了多少「快速单做」和「多模型对读」。
跑法：python scripts/tier_stats.py [--days 30]。只读 projects/*/decisions.json，不改任何东西。
没记档位的旧记录按 peers 人数推断，单独计为「推断」，不和真实记录混在一起；连 peers 都没存的（多为 Leader 从总线直接派的单）计为「未知」，不硬猜。
派发时在线只有 0–1 个 Agent 的快速单单列为「无法区分」：那时勾「全员在线」也只会派给 1 个人，分不清是你选了单做还是只剩 1 人可派——既不算快速也不算对读（fable2 8bf5 / f5f0）。"""
import argparse
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bus_projects as P  # noqa: E402


def _created(rec):
    try:
        return dt.datetime.fromisoformat(str(rec.get("created", "")).replace(" ", "T", 1))
    except ValueError:
        return None


def stats(days: int = 30) -> dict:
    since = dt.datetime.now() - dt.timedelta(days=days)
    out = {}
    for slug in sorted(os.listdir(P.PROJECTS)) if os.path.isdir(P.PROJECTS) else []:
        if not os.path.isdir(os.path.join(P.PROJECTS, slug)):
            continue
        row = {"fast": 0, "fast_sparse": 0, "multi": 0, "inferred_fast": 0, "inferred_multi": 0, "unknown": 0}
        for rec in P.decisions(slug):
            created = _created(rec)
            if created is None or created.replace(tzinfo=None) < since or rec.get("status") == "superseded":
                continue
            n = rec.get("online_n")
            if rec.get("tier") == "fast" and isinstance(n, int) and 0 <= n <= 1:
                row["fast_sparse"] += 1
            elif rec.get("tier") in P.TIERS:
                row[rec["tier"]] += 1
            elif [x for x in (rec.get("peers") or []) if x]:
                row["inferred_" + P.tier_of(rec.get("peers"))] += 1
            else:
                row["unknown"] += 1
        if any(row.values()):
            out[slug] = row
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args(argv)
    data = stats(a.days)
    if not data:
        print(f"最近 {a.days} 天没有派发记录")
    for slug, r in data.items():
        print(f"{slug}：快速单做 {r['fast']} · 多模型对读 {r['multi']} · 只有 1 人在线、无法区分 {r['fast_sparse']}（旧记录推断：快速 {r['inferred_fast']} · 对读 {r['inferred_multi']} · 未知 {r['unknown']}）")
    return data


if __name__ == "__main__":
    main()
