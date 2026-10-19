"""Verify actor-approved PR merges and the exact head's required Actions checks."""
from datetime import datetime
import re


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z", value):
        raise ValueError("merge and check timestamps must be valid GitHub UTC timestamps")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("merge and check timestamps must be valid GitHub UTC timestamps") from None


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _required_checks(request_json, prefix: str, policy: dict, head: str, merged_at: datetime) -> None:
    checks = request_json(f"{prefix}/commits/{head}/check-runs?filter=all&per_page=100")
    rows = checks.get("check_runs") if isinstance(checks, dict) else None
    total = checks.get("total_count") if isinstance(checks, dict) else None
    if (not isinstance(rows, list) or type(total) is not int or total != len(rows)
            or not 0 <= total <= 100 or any(not isinstance(row, dict) for row in rows)):
        raise ValueError("bounded complete exact-head check history unavailable")
    required = set(policy["requiredChecks"])
    latest = {}
    seen = set()
    for row in rows:
        name = row.get("name")
        if not isinstance(name, str) or name not in required:
            continue
        check_id = row.get("id")
        app = row.get("app")
        if (type(check_id) is not int or check_id <= 0 or check_id in seen
                or row.get("head_sha") != head or not isinstance(app, dict)
                or type(app.get("id")) is not int or app["id"] != policy["requiredCheckAppId"]):
            raise ValueError("required checks must be unique exact-head GitHub Actions checks")
        seen.add(check_id)
        # Check-run IDs identify newer attempts even when a new run has not started yet.
        if name not in latest or check_id > latest[name]["id"]:
            latest[name] = row
    if set(latest) != required:
        raise ValueError("merged PR head lacks a required GitHub Actions check")
    for row in latest.values():
        if row.get("status") != "completed" or row.get("conclusion") != "success":
            raise ValueError("latest required exact-head check is not completed successfully")
        if not _timestamp(row.get("started_at")) <= _timestamp(row.get("completed_at")) <= merged_at:
            raise ValueError("latest required check was not successful before the PR merge")


def require_approved_merge(request_json, policy: dict, commit: str, base_branch: str) -> None:
    """Require a normal authorized merge; caller validates the exact release policy first."""
    if not _sha(commit) or base_branch not in {"main", "release/native"}:
        raise ValueError("exact release commit and canonical base branch required")
    prefix = f"repos/{policy['repository']}"
    pulls = request_json(f"{prefix}/commits/{commit}/pulls?per_page=100")
    if (not isinstance(pulls, list) or len(pulls) >= 100
            or any(not isinstance(pr, dict) for pr in pulls)):
        raise ValueError("bounded exact-commit pull request history unavailable")
    matches = [pr for pr in pulls if pr.get("merge_commit_sha") == commit and pr.get("merged_at")
               and isinstance(pr.get("base"), dict) and pr["base"].get("ref") == base_branch]
    if len(matches) != 1:
        raise ValueError("release commit lacks one exact merged PR")
    association = matches[0]
    number = association.get("number")
    if type(number) is not int or number <= 0:
        raise ValueError("merged PR number unavailable")
    pr = request_json(f"{prefix}/pulls/{number}")
    if not isinstance(pr, dict):
        raise ValueError("authoritative merged PR metadata unavailable")
    base, head, actor = pr.get("base"), pr.get("head"), pr.get("merged_by")
    associated_head = association.get("head")
    if (type(pr.get("number")) is not int or pr["number"] != number
            or pr.get("merged") is not True or pr.get("state") != "closed"
            or pr.get("draft") is not False or pr.get("merge_commit_sha") != commit
            or not isinstance(base, dict) or base.get("ref") != base_branch
            or not isinstance(base.get("repo"), dict) or base["repo"].get("full_name") != policy["repository"]
            or not isinstance(head, dict) or not _sha(head.get("sha"))
            or not isinstance(head.get("ref"), str) or not head["ref"]
            or not isinstance(head.get("repo"), dict) or head["repo"].get("full_name") != policy["repository"]
            or not isinstance(associated_head, dict) or associated_head.get("sha") != head["sha"]
            or not isinstance(actor, dict) or actor.get("login") not in policy["operators"]):
        raise ValueError("release commit requires a canonical PR merged by an authorized actor")
    merged_at = _timestamp(pr.get("merged_at"))
    if _timestamp(association.get("merged_at")) != merged_at:
        raise ValueError("merged PR association and authoritative merge time differ")
    _required_checks(request_json, prefix, policy, head["sha"], merged_at)
