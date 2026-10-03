"""One-shot activity pulse; no daemon, acquisition, or stale-lease revival."""
import argparse
import json
import bus_core as B


def pulse(epoch, task_id="", status="busy"):
    if status not in ("busy", "idle"):
        raise ValueError("invalid activity status")
    current = B.lease()
    if current.get("holder") != "controller" or current.get("epoch") != epoch:
        raise RuntimeError("LEASE_LOST: return to console_wait for explicit handback")
    renewed = B.renew_lease("controller")
    if renewed.get("holder") != "controller" or renewed.get("epoch") != epoch:
        raise RuntimeError("LEASE_CHANGED: stop controller work")
    # Strict heartbeat path: unlike wait, do not silently report success on an I/O error.
    B._heartbeat("controller", status, "controller", task_id, "", 0)
    state = B.peer_state("controller")
    confirmed = B.lease()
    if (confirmed.get("holder") != "controller" or confirmed.get("epoch") != epoch
            or not state.get("online") or state.get("status") != status
            or state.get("task_id") != task_id):
        raise RuntimeError("ACTIVITY_NOT_CONFIRMED: stop controller work")
    return {"ok": True, "holder": "controller", "epoch": epoch,
            "status": status, "last_seen": state["last_seen"], "expires": confirmed["expires"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--task", default="")
    parser.add_argument("--status", choices=("busy", "idle"), default="busy")
    args = parser.parse_args()
    print(json.dumps(pulse(args.epoch, args.task, args.status), ensure_ascii=False))


if __name__ == "__main__":
    main()
