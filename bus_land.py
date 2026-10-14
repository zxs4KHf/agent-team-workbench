"""落地推进器（2026-10-02 用户：「已采纳待落地卡了非常多的待审，根本推不动。不需要我决策的地方他们自己推，
需要我决策的地方卡在那儿让我决策」）。

实测 143 条「待审」里一条真正待审的都没有：83 条审查退回在等返修、22 条审查通过在等合并、23 条提交里没有改动、
15 条施工单已关却没合并。界面一律写「待审」，「全部催审」自然什么也派不出去。这里把它们分开：

  merge_ready  审查通过、门禁过、施工单开着  → 守护每圈自动合并（bus_build.merge：只认绑定在当前提交上的 PASS，
               合并后跑集成门禁，任何一步失败 main 不动）。合并受阻的记下原因，转成「受阻」等返修。
  returned     审查退回                        → 用户在待落地页批量选：重新派施工（带上一版提交与退回意见）/ 不再落地
  blocked      自动合并受阻（冲突、集成门禁没过、main 前进）→ 同上
  empty        提交里没有改动（多半 main 已经有了）→ 同上（「不再落地」= 标记无需实施）
  closed       施工单已关但没合并                → 同上
  reviewing    真正待审（有提交、门禁过、没审）→ 交给催审 / 自动分配，这里不碰

重新派施工不经 Leader：直接 bus_core.dispatch(mode="build") 给一个「正在等任务、属于能施工分组」的 Agent，
新施工单从当前 main 开工作副本，任务包里写明上一版的分支、提交和退回意见；旧施工单关闭（工作副本保留），
决策改关联新施工单（implementation_task_id）。

已在 main 自动结案（2026-10-02 接手积压时实测：139 条需要判断的里 47 条的提交合进 main 根本不产生变化——内容早已经
别的分支落地，只是这张施工单没被标记）：守护每圈对需要判断的几类做一次合并模拟（git merge-tree），结果树等于 main
的就把决策标成已落地（execution=released），写明依据。不碰 main、不碰施工记录；判断按（提交, main 头）缓存。
"""
import os
import subprocess
import time

import bus_core as B

CATS = ("merge_ready", "stale", "returned", "blocked", "empty", "closed", "reviewing", "merged", "stalled", "abandoned", "unbuilt", "building")
STALE_REVIEW_HOURS = 48   # 审查超过这么久才合：main 早变了，先重审，不自动合
CAT_LABEL = {"merge_ready": "审查通过·自动合并中", "stale": "审查过期·需重审", "returned": "审查退回·待返修", "blocked": "合并受阻·待返修",
             "empty": "空交付（提交里没有改动）", "closed": "施工单已关·没合并", "reviewing": "等审查",
             # 2026-10-02 接手积压：下面几类原来在待落地页里看不见——22 条早已合入 main 的决策一直挂着「待实施」，
             # 22 张施工单开着几天没人交提交、8 张关了也从没交过、7 条采纳后根本没开施工单
             "merged": "已合入·自动结案中", "stalled": "施工停滞·超过一天没交提交", "abandoned": "施工单已关·从未交提交",
             "unbuilt": "还没开施工单", "building": "施工中"}
STALL_HOURS = 24
AUTO_MERGE_PER_CYCLE = 3
AUTO_RELEASE_PER_CYCLE = 30
JUDGE_CATS = ("stale", "returned", "blocked", "empty", "closed")   # 需要人判断的几类：先排除「其实已经在 main 了」的
_IN_MAIN = {}   # (repo, commit, main_head) -> bool


def _git(repo: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=60, creationflags=0x08000000 if os.name == "nt" else 0)


def in_main(repo: str, commit: str) -> bool:
    """把 commit 合进 main 会不会产生任何变化：merge-tree 无冲突且结果树就是 main 的树 → 内容已经在 main。
    git 太旧（没有 merge-tree --write-tree）、对象不存在、历史不相关一律返回 False（交给人判断，不冒进）。"""
    if not repo or not commit:
        return False
    head = _git(repo, "rev-parse", "main").stdout.strip()
    if not head:
        return False
    key = (repo, commit, head)
    if key not in _IN_MAIN:
        r = _git(repo, "merge-tree", "--write-tree", "--no-messages", head, commit)
        tree = (r.stdout.splitlines() or [""])[0].strip()
        _IN_MAIN[key] = r.returncode == 0 and bool(tree) and tree == _git(repo, "rev-parse", head + "^{tree}").stdout.strip()
    return _IN_MAIN[key]


def _build_rec(bid: str) -> dict:
    import bus_build as BB
    return B.read_json(BB.record_path(bid), {}) or {}


def classify(rec: dict, peer: str) -> str:
    """一条施工记录里某个成员的提交处在哪一类。没有提交返回 ''（还在施工，不归这里管）。"""
    tm = (rec.get("teammates") or {}).get(peer) or {}
    if not tm.get("commit") or tm.get("merged"):
        return ""
    if rec.get("closed") or rec.get("status") not in (None, "building"):
        return "closed"
    if tm.get("land_block"):
        return "blocked"
    rv = str(tm.get("review") or "").upper()
    if rv.startswith("RETURN"):
        return "returned"
    if not tm.get("changed_files"):
        return "empty"
    if rv.startswith("PASS"):
        at = B.parse_ts(tm.get("review_at"))
        if at is None or time.time() - at.timestamp() > STALE_REVIEW_HOURS * 3600:
            return "stale"
        return "merge_ready"
    return "reviewing"


def scan(projects=None) -> list:
    """所有「已采纳、有提交、没合并」的决策，按类别标好。projects 缺省读全部项目。"""
    import bus_projects as P
    out = []
    for slug in (projects or [p["slug"] for p in P.list_projects()]):
        for d in P.decisions(slug):
            if d.get("status") != "adopted" or d.get("execution") in ("released", "n-a"):
                continue
            bid = d.get("implementation_task_id") or d["task_id"]
            rec = _build_rec(bid)
            tms = rec.get("teammates") or {}
            created = B.parse_ts(rec.get("created") or d.get("updated") or d.get("created"))
            base = {"project": slug, "task_id": d["task_id"], "title": d.get("title") or d["task_id"], "build_id": bid if tms else "",
                    "age_days": round((time.time() - created.timestamp()) / 86400, 1) if created else None}
            if not tms:
                out.append({**base, "peer": "", "commit": "", "branch": "", "cat": "unbuilt", "note": ""})
                continue
            done = next(((p, tm) for p, tm in tms.items() if tm.get("merged")), None)
            if done:
                out.append({**base, "peer": done[0], "commit": str(done[1].get("commit") or "")[:7], "branch": done[1].get("branch", ""), "cat": "merged", "note": ""})
                continue
            hit = False
            for peer, tm in tms.items():
                cat = classify(rec, peer)
                if not cat:
                    continue
                out.append({**base, "peer": peer, "commit": str(tm.get("commit") or "")[:7], "branch": tm.get("branch", ""), "cat": cat,
                            "note": str(tm.get("land_block") or tm.get("review_note") or "")[:600]})
                hit = True
                break   # 一条决策只算一个成员（与 implementation_of 取第一个提交者一致）
            if hit:
                continue
            # 没有任何成员交过提交
            peer = next(iter(tms), "")
            if rec.get("closed") or rec.get("status") not in (None, "building"):
                cat = "abandoned"
            elif base["age_days"] is not None and base["age_days"] * 24 > STALL_HOURS:
                cat = "stalled"
            else:
                cat = "building"
            out.append({**base, "peer": peer, "commit": "", "branch": (tms.get(peer) or {}).get("branch", ""), "cat": cat, "note": ""})
    return out


def summary(items=None) -> dict:
    items = scan() if items is None else items
    out = {c: {"n": 0, "label": CAT_LABEL[c]} for c in CATS}
    for it in items:
        out[it["cat"]]["n"] += 1
    return out


def auto_merge(limit: int = AUTO_MERGE_PER_CYCLE, by: str = "land") -> list:
    """审查通过的自动合并。失败的记 land_block（原因写进施工记录），下一圈归到「合并受阻」，不反复撞。"""
    import bus_build as BB
    done = []
    for it in [x for x in scan() if x["cat"] == "merge_ready"][:max(0, limit)]:
        try:
            r = BB.merge(it["build_id"], it["peer"], by=by)
        except Exception as exc:  # noqa: BLE001  一单失败不挡其他单
            r = {"ok": False, "reason": str(exc)[:300]}
        if not r.get("ok"):
            rec = _build_rec(it["build_id"])
            tm = (rec.get("teammates") or {}).get(it["peer"])
            if tm is not None:
                reason = r.get("reason") or "合并失败"
                if r.get("conflicts"):
                    reason += "：" + "、".join(map(str, r["conflicts"]))[:300]
                tm["land_block"] = reason
                BB.save_record(it["build_id"], rec)
        else:
            import bus_projects as P
            P.upsert_decision(it["project"], it["task_id"], execution="released", execution_by=by, execution_at=B.now_iso(),
                              execution_note=f"落地推进器自动合并 {it['peer']} 的提交 {it['commit']}（审查通过、集成门禁通过）")
        done.append({**it, "result": {k: v for k, v in r.items() if k in ("ok", "reason", "merge_commit", "conflicts")}})
    return done


def _rework_question(it: dict, title: str) -> str:
    why = {"returned": "审查退回", "blocked": "自动合并受阻", "empty": "上一版提交里没有改动", "closed": "上一张施工单已关闭但没合并"}.get(it["cat"], "需要返修")
    return (f"返修落地：{title}\n\n上一版施工 {it['build_id']}（{it['peer']}，分支 {it['branch'] or '—'}，提交 {it['commit'] or '—'}）没能合入：{why}。\n"
            f"{('退回 / 受阻原因：' + it['note']) if it['note'] else ''}\n\n"
            "请在你的新工作副本（从当前 main 开出）里完成这件事：上一版的提交可以 cherry-pick 过来再修，也可以重做；"
            "先确认 main 是否已经包含所需改动，已包含就在回复里说明并给出证据（文件与行号），不要做空提交。")


def rework(it: dict, by: str = "user") -> dict:
    """重新派施工给一个正在等任务的施工 Agent（不经 Leader）。旧施工单关闭（工作副本保留），决策改关联新单。"""
    import bus_build as BB
    import bus_projects as P
    import bus_reassign as RA
    target = RA.pick_free_target(it["peer"], [], role="builder")
    if not target:
        raise ValueError("现在没有正在等任务的施工 Agent")
    rec = next((d for d in P.decisions(it["project"]) if d["task_id"] == it["task_id"]), {}) or {}
    ctx = [rec["merge_path"]] if rec.get("merge_path") else []
    r = B.dispatch("controller", [target], "build", _rework_question(it, rec.get("title") or it["title"]), ctx, ttl_min=180, project=it["project"], repo_dir=P.load_project(it["project"]).get("repo_dir") or "")
    try:
        BB.close(it["build_id"], remove_worktrees=False)
    except Exception:  # noqa: BLE001  旧单关不掉不挡新单：新单已经派出，决策改关联新单即可
        pass
    P.upsert_decision(it["project"], it["task_id"], implementation_task_id=r["task_id"], execution="pending",
                      execution_by=by, execution_at=B.now_iso())
    return {"to": target, "build_id": r["task_id"]}


def direct(it: dict, by: str = "user") -> dict:
    """还没开施工单的：按合并稿最终方案直接派给正在等任务的施工 Agent（bus_projects.dispatch_implement）。
    没人在等或合并稿读不出方案时不入 Leader 队列，直接报错，留在这一类里等下次。"""
    import bus_projects as P
    import bus_reassign as RA
    if not RA.pick_free_target("", [], role="builder"):
        raise ValueError("现在没有正在等任务的施工 Agent")
    r = P.dispatch_implement(it["project"], it["task_id"], by=by)
    if not r.get("direct"):
        raise ValueError(r.get("note") or "没有派出去")
    return {"to": r["builder"], "build_id": r["build_task_id"]}


def rereview(it: dict, by: str = "user") -> dict:
    """审查过期的：作废旧结论，按当前 main 重新派审查（bus_build.dispatch_review，派给在线 Reviewer）。"""
    import bus_build as BB
    rec = _build_rec(it["build_id"])
    tm = (rec.get("teammates") or {}).get(it["peer"])
    if tm is None:
        raise ValueError("施工记录里没有这个成员")
    for k in ("review", "review_note", "reviewed_commit", "review_at", "review_main", "reviewer"):
        tm.pop(k, None)
    BB.save_record(it["build_id"], rec)
    r = BB.dispatch_review(it["build_id"], it["peer"], by="controller")
    return {"review_task": (r or {}).get("task_id", ""), "reviewers": (r or {}).get("reviewers", [])}


def drop(it: dict, by: str = "user") -> dict:
    """不再落地：标记无需实施（execution=n-a），决策移到「已完成」；施工单与工作副本原样保留，可随时改回。"""
    import bus_projects as P
    P.upsert_decision(it["project"], it["task_id"], execution="n-a", execution_by=by, execution_at=B.now_iso())
    return {"ok": True}


def bulk(cat: str, action: str, by: str = "user", limit: int = 50) -> dict:
    """用户在待落地页对一类批量处理：rework（重新派施工）/ drop（不再落地）。逐条结果都返回，派不出去的写清原因。"""
    if cat not in ("stale", "returned", "blocked", "empty", "closed", "stalled", "abandoned", "unbuilt"):
        raise ValueError("只能批量处理：审查过期 / 审查退回 / 合并受阻 / 空交付 / 施工单已关 / 施工停滞 / 从未交提交 / 还没开施工单")
    allowed = {"stale": ("rework", "drop", "rereview"), "unbuilt": ("direct", "drop")}.get(cat, ("rework", "drop"))
    if action not in allowed:
        raise ValueError(f"这一类只能：{' / '.join(allowed)}")
    done, failed = [], []
    for it in [x for x in scan() if x["cat"] == cat][:limit]:
        try:
            fn = {"rework": rework, "drop": drop, "rereview": rereview, "direct": direct}[action]
            done.append({**it, **fn(it, by)})
        except Exception as exc:  # noqa: BLE001
            failed.append({"task_id": it["task_id"], "title": it["title"], "error": str(exc)[:200]})
            if action in ("rework", "direct") and "没有正在等任务" in str(exc):
                break   # 没人可派：后面的都会一样，不再逐条撞
    return {"done": done, "failed": failed, "remaining": len([x for x in scan() if x["cat"] == cat])}


def auto_release(limit: int = AUTO_RELEASE_PER_CYCLE, by: str = "land") -> list:
    """需要判断的几类里，提交内容其实已经在 main 的：标成已落地，写明依据，不再占用户的注意力。"""
    import bus_projects as P
    done = []
    for it in [x for x in scan() if x["cat"] in JUDGE_CATS + ("merged",)]:
        if len(done) >= limit:
            break
        if it["cat"] == "merged":
            P.upsert_decision(it["project"], it["task_id"], execution="released", execution_by=by, execution_at=B.now_iso(),
                              execution_note=f"施工已合入 main（{it['peer']} 的提交 {it['commit']}），只是决策没回写")
            done.append(it)
            continue
        rec = _build_rec(it["build_id"])
        tm = (rec.get("teammates") or {}).get(it["peer"]) or {}
        if not in_main(rec.get("repo") or B.WORKSPACE, tm.get("commit") or ""):
            continue
        P.upsert_decision(it["project"], it["task_id"], execution="released", execution_by=by, execution_at=B.now_iso(),
                          execution_note=f"{it['peer']} 的提交 {it['commit']} 合进当前 main 不产生任何变化：内容已由别的分支落地（原归类：{CAT_LABEL[it['cat']]}）")
        done.append(it)
    return done


def tick() -> dict:
    """守护每圈调用：只做不需要人判断的事——已在 main 的结案、审查通过的自动合并。
    开关：roles.json policy.land_auto_release / land_auto_merge（缺省都开）。"""
    import bus_build as BB
    released = auto_release() if BB.policy("land_auto_release", True) is not False else []
    if BB.policy("land_auto_merge", True) is False:
        return {"merged": [], "released": released, "off": True}
    return {"merged": auto_merge(), "released": released}
