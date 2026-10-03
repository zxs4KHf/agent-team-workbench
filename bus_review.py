"""立即派审查（「催审」不再只是给 Leader 留言）。

以前点「催审」= 往 Leader 指令队列塞一句「请尽快审查」，要等 Leader 下一圈心跳、还得它记得去派，
于是「进行中」里一排待审的单一直卡着。这里直接走 bus_build 的派审查路径：
  · 施工单：每个「已提交、门禁通过、还没审、没合并」的成员，若没有正在进行的审查，就立刻派给在线 Reviewer；
  · 审查单：如果它的 Reviewer 全都离线 / 过期，换一批在线 Reviewer 重派，旧审查单归档。
正在有人审的不重复派。业务逻辑都在这里，bus_web 只做转发。
"""
import os

import bus_core as B
import bus_build as BB


def _review_target_of(meta: dict) -> dict:
    """审查单绑定的被审对象。新单在 meta 里有 review_of；旧单只在 Reviewer 的收件箱副本里有。"""
    if isinstance(meta.get("review_of"), dict):
        return meta["review_of"]
    for peer in meta.get("peers") or []:
        for ext in (".json", ".taken", ".json.expired"):
            t = B.read_json(os.path.join(B.INBOX, peer, f"{meta.get('id')}{ext}"), None)
            if isinstance(t, dict) and isinstance(t.get("review_of"), dict):
                return t["review_of"]
    return {}


def _review_alive(meta: dict) -> bool:
    """审查单还有人在审：有 Reviewer 没回、手里副本没过期、而且人在线。全都离线或过期 = 卡住了。"""
    tid = meta.get("id", "")
    peers = meta.get("peers") or []
    if any(os.path.exists(os.path.join(B.OUTBOX, f"{tid}.{p}.md")) for p in peers):
        return False   # 已有 Reviewer 回复 = 这一轮已出结论（reply 写进施工记录），不算「还在审」
    return any(B._copy_alive(tid, p) and B.peer_state(p).get("online") for p in peers)


def open_reviews() -> list:
    """所有还开着的审查单 [(meta, target)]。"""
    out = []
    for f in os.listdir(B.OUTBOX):
        if not f.endswith(".meta.json"):
            continue
        meta = B.read_json(os.path.join(B.OUTBOX, f), None)
        if isinstance(meta, dict) and meta.get("mode") == "review":
            out.append((meta, _review_target_of(meta)))
    return out


def _live_review_for(task_id: str, teammate: str, reviews: list) -> str:
    for meta, target in reviews:
        if target.get("task_id") == task_id and target.get("teammate") == teammate and _review_alive(meta):
            return meta["id"]
    return ""


def pending_targets(task_id: str = "") -> list:
    """待审的施工成员 [(task_id, teammate)]：已提交、门禁通过、有改动、没审过、没合并、施工单没关。"""
    out = []
    if not os.path.isdir(BB.BUILD_DIR):
        return out
    for f in sorted(os.listdir(BB.BUILD_DIR)):
        if not f.endswith(".json"):
            continue
        rec = B.read_json(os.path.join(BB.BUILD_DIR, f), None) or {}
        tid = rec.get("task_id") or f[:-5]
        if task_id and tid != task_id:
            continue
        if rec.get("status") not in (None, "building") or rec.get("closed"):
            continue
        for name, tm in (rec.get("teammates") or {}).items():
            if not tm.get("commit") or tm.get("merged") or tm.get("review"):
                continue
            if tm.get("commit_unbound") or not tm.get("changed_files") or not (tm.get("gates") or {}).get("ok"):
                continue
            out.append((tid, name))
    return out


def review_now(task_id: str = "", by: str = "user", ttl_min: int = 15) -> dict:
    """立刻派审查。task_id 为空 = 全部待审；是施工单号 = 只派这一单；是审查单号 = 它卡住了就换人重派。"""
    reviews = open_reviews()
    targets = pending_targets(task_id)
    replaced = []
    if task_id and not targets:
        meta = B.read_json(os.path.join(B.OUTBOX, f"{task_id}.meta.json"), None)
        if isinstance(meta, dict) and meta.get("mode") == "review":
            tgt = _review_target_of(meta)
            if not tgt.get("task_id"):
                return {"dispatched": [], "skipped": [], "errors": [{"task_id": task_id, "error": "这张审查单找不到被审的施工提交"}]}
            if _review_alive(meta):
                return {"dispatched": [], "skipped": [{"task_id": task_id, "reason": "Reviewer 在线、正在审"}], "errors": []}
            targets = [(tgt["task_id"], tgt["teammate"])]
            replaced.append(meta)
    if task_id and not targets and not replaced:
        # 点了某一单的「催审」但它没有可审的提交：说清是谁没交，而不是一句「没有待审的提交」让人以为按钮坏了
        rec = B.read_json(os.path.join(BB.BUILD_DIR, f"{task_id}.json"), None) or {}
        tms = rec.get("teammates") or {}
        if tms and not any(tm.get("commit") for tm in tms.values()):
            meta = B.read_json(os.path.join(B.OUTBOX, f"{task_id}.meta.json"), None) or {}
            who = "、".join(meta.get("peers") or list(tms)[-1:])
            return {"dispatched": [], "skipped": [], "errors": [{"task_id": task_id, "error": f"施工人 {who} 还没交提交，没有东西可审；先改派施工或取消这张单"}]}
    dispatched, skipped, errors = [], [], []
    for tid, mate in targets:
        # 换人重派也要去重，只是把被替换的那张旧单排除在外（c204：原来这条路径整个跳过去重，守护每圈给同一对象再派一张，
        # 56 秒里同一施工对象被派审 4 次）
        gone = {m.get("id") for m in replaced}
        live = _live_review_for(tid, mate, [x for x in reviews if x[0].get("id") not in gone])
        if live:
            skipped.append({"task_id": tid, "teammate": mate, "reason": f"已有审查在进行（{live}）"})
            continue
        try:
            r = BB.dispatch_review(tid, mate, ttl_min=ttl_min, by="controller")
            dispatched.append(r)
        except Exception as exc:  # noqa: BLE001  一单失败不挡其他单
            errors.append({"task_id": tid, "teammate": mate, "error": str(exc)})
    for meta in replaced:
        if dispatched:
            # 旧审查单归档、收件箱副本撤掉：Reviewer 回来也不会再审一张作废的单
            for peer in meta.get("peers") or []:
                B._drop_inbox(meta["id"], peer)
            B.archive(meta["id"])
    if dispatched or replaced:
        try:
            import bus_events as E
            E.emit("review.dispatched", {"by": by, "count": len(dispatched), "tasks": [d["task_id"] for d in dispatched]})
        except Exception:  # noqa: BLE001  事件记录失败不影响派审
            pass
    return {"dispatched": dispatched, "skipped": skipped, "errors": errors}
