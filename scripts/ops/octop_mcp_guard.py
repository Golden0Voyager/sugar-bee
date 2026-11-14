#!/usr/bin/env python3
"""Octop MCP 连接器「卡死」自愈守卫。

## 为什么需要它（2026-11-14 事故）

老愚（爸爸）的健康管家 JMG40 有一阵子每轮对话都报「无法加载 MCP 工具：garmin」，
用户点多少次重试都没用。排查得到的确凿事实：

- 直接 `aload_mcp_tools`、线上 `POST /connectors/custom-mcp/test` 都能拉到 28 个工具
- 但真实对话那一轮，从「三个连接器全缺」到「只剩 garmin 缺」只花了 **211ms**
  （真启动 stdio 子进程列 28 个工具要 ~2.5s），说明它根本没去加载
- 整份日志没有 `Failed to load MCP tools`，也没有 `cache store ... tools=0`

日志已轮转，当时的 spec 取不到了，**确切机制无法定案**（最可能是
`custom_harness_configs()` 那次没给出带 `transport` 的 spec，于是
`prepare_chat_mcp` 里那个 `continue` 静默跳过）。唯一可靠的结论是：

> 这类「工具缺失」状态**不会自愈**，只有 `invalidate_mcp_tool_cache()` +
> 重建 agent 才能解开。而 `POST /agents/{id}/reload` 不清缓存，清了也没用。

所以本脚本不去猜机制，只负责把「卡住」这件事本身修掉。

## 做什么

发现某轮对话结束后仍报 `tools still missing for [...]`，就调官方入口：

    PATCH /api/connectors/custom-mcp/servers/<name>   {"enabled": true}

该入口内部走 `_schedule_connector_reload` → `reload_connectors_for_user()`
→ 第一行 `invalidate_mcp_tool_cache(user_id)` + 重建该用户所有 agent。
**不重启 Octop、不重启容器、不动微信通道。**

## 硬约束

- 不写生产库：jwt 密钥与用户行都从 `sqlite3.backup()` 快照读
- 令牌只活 300 秒，不落盘、不进日志、不进进程参数
- 每个 server 30 分钟内最多自愈 1 次，避免把真故障刷成风暴
- 健康时完全静默（与 health_guard.sh 的约定一致）
- 自愈失败只告警，不降级成重启

## 用法

    /root/.octop/venv/bin/python /opt/sugar_bee/octop_mcp_guard.py --dry-run -v
    /root/.octop/venv/bin/python /opt/sugar_bee/octop_mcp_guard.py

cron（由 install_ops.sh 幂等写入）：

    */10 * * * * /root/.octop/venv/bin/python /opt/sugar_bee/octop_mcp_guard.py >/dev/null 2>&1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta

LOG_DEFAULT = "/root/.octop/logs/octop.log"
API_DEFAULT = "http://127.0.0.1:8088"
DB_DEFAULT = "/root/.octop/octop.db"
BARK_KEY_FILE = "/opt/sugar_bee/.bark_key"
STATE_DEFAULT = "/var/lib/octop_mcp_guard/state.json"

TAG_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3}\s")
START_RE = re.compile(
    r"prepare_chat_mcp agent=(?P<agent>\S+) connector_user_id=(?P<uid>\S+) "
    r"requested=\[(?P<req>[^\]]*)\] tool_count=(?P<count>\d+) missing=\[(?P<miss>[^\]]*)\]"
)
MISSING_RE = re.compile(r"prepare_chat_mcp agent=(?P<agent>\S+): tools still missing for \[(?P<miss>[^\]]*)\]")
READY_RE = re.compile(r"prepare_chat_mcp agent=(?P<agent>\S+): MCP already ready")
STORE_RE = re.compile(r"mcp tool cache store user=(?P<uid>\S+) server=(?P<server>\S+) fingerprint=")


def _names(raw: str) -> list[str]:
    return [p.strip().strip("'\"") for p in raw.split(",") if p.strip().strip("'\"")]


def read_events(path: str, cutoff: datetime) -> tuple[list, list, list, list]:
    """返回 (starts, missings, readys, stores)，均已按 cutoff 过滤。"""
    starts: list[tuple[datetime, str, int]] = []
    missings: list[tuple[datetime, str, list[str]]] = []
    readys: list[tuple[datetime, str]] = []
    stores: list[tuple[datetime, str]] = []

    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 4 * 1024 * 1024))
            blob = fh.read().decode("utf-8", "replace")
    except OSError as exc:
        print(f"WARN 读不到日志 {path}: {exc}", file=sys.stderr)
        return starts, missings, readys, stores

    for line in blob.splitlines():
        m = TAG_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts < cutoff:
            continue
        if (mm := MISSING_RE.search(line)) is not None:
            missings.append((ts, mm.group("agent"), _names(mm.group("miss"))))
            continue
        if (rm := READY_RE.search(line)) is not None:
            readys.append((ts, rm.group("agent")))
            continue
        if (sm := STORE_RE.search(line)) is not None:
            stores.append((ts, sm.group("server")))
            continue
        if (st := START_RE.search(line)) is not None:
            uid = st.group("uid")
            if uid.isdigit():
                starts.append((ts, st.group("agent"), int(uid)))
    return starts, missings, readys, stores


def pending_servers(starts, missings, readys, stores) -> dict[str, int]:
    """找出「报了缺失、其后既没 store 也没 ready」的 server → user_id。"""
    pending: dict[str, int] = {}
    for ts, agent, servers in missings:
        uid = 0
        for sts, s_agent, s_uid in starts:
            if s_agent == agent and sts <= ts:
                uid = s_uid
        healed_by_ready = any(ts2 >= ts and a2 == agent for ts2, a2 in readys)
        if healed_by_ready:
            continue
        for server in servers:
            if any(ts2 >= ts and s2 == server for ts2, s2 in stores):
                continue
            pending[server] = uid
    return pending


def mint_token(db_path: str, user_id: int, ttl: int = 300) -> str:
    """用 Octop 自己的 sign_token() 签一个短寿命 access token，只读快照取密钥。"""
    from octop.infra.auth.tokens import PURPOSE_ACCESS, sign_token  # noqa: PLC0415

    fd, snap = tempfile.mkstemp(suffix=".db", prefix="octop_mint_")
    os.close(fd)
    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)
        finally:
            src.close()
        dst.row_factory = sqlite3.Row
        sec = dst.execute("SELECT v FROM secrets WHERE k = 'jwt'").fetchone()
        row = dst.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        dst.close()
        if sec is None:
            raise RuntimeError("secrets.jwt 缺失，无法签令牌")
        if row is None:
            raise RuntimeError(f"用户 {user_id} 不存在")
        return sign_token(
            bytes(sec["v"]),
            sub=int(row["id"]),
            uname=row["username"],
            role=row["role"],
            purpose=PURPOSE_ACCESS,
            ttl_seconds=ttl,
            mfa_version=int(row["mfa_version"] or 0),
            password_version=int(row["password_version"] or 0),
        )
    finally:
        try:
            os.unlink(snap)
        except OSError:
            pass


def patch_server(api: str, token: str, name: str) -> int:
    body = json.dumps({"enabled": True}).encode()
    req = urllib.request.Request(
        f"{api}/api/connectors/custom-mcp/servers/{name}",
        data=body,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        resp.read()
        return resp.status


def notify(title: str, body: str) -> None:
    try:
        key = open(BARK_KEY_FILE, encoding="utf-8").read().strip()
    except OSError:
        print(f"WARN 无 Bark key，跳过推送: {title}", file=sys.stderr)
        return
    payload = json.dumps(
        {"title": title, "body": body[:400], "level": "critical", "group": "octop"}
    ).encode()
    req = urllib.request.Request(
        "https://api.day.app/" + key,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()  # noqa: S310
    except Exception as exc:  # noqa: BLE001
        print(f"WARN Bark 推送失败: {exc}", file=sys.stderr)


def load_state(path: str) -> dict[str, float]:
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict[str, float]) -> None:
    try:
        os.makedirs(os.path.dirname(path), mode=0o750, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        print(f"WARN 状态写不进去 {path}: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Octop MCP 工具缺失自愈守卫")
    ap.add_argument("--log", default=LOG_DEFAULT)
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--db", default=DB_DEFAULT)
    ap.add_argument("--state", default=STATE_DEFAULT)
    ap.add_argument("--window", type=int, default=20, help="回看分钟数（默认 20）")
    ap.add_argument("--rate", type=int, default=30, help="同一 server 自愈冷却分钟数（默认 30）")
    ap.add_argument("--dry-run", action="store_true", help="只报不修")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    now = time.time()
    cutoff = datetime.now() - timedelta(minutes=args.window)
    starts, missings, readys, stores = read_events(args.log, cutoff)
    pending = pending_servers(starts, missings, readys, stores)

    if args.verbose:
        print(
            f"窗口 {args.window} 分钟：缺失事件 {len(missings)}、已就绪 {len(readys)}、"
            f"缓存写入 {len(stores)} → 待处理 {sorted(pending) or '无'}"
        )

    if not pending:
        return 0

    state = load_state(args.state)
    cooled = [s for s in pending if now - state.get(s, 0) >= args.rate * 60]
    if not cooled:
        if args.verbose:
            print(f"冷却中，跳过: {sorted(pending)}")
        return 0

    if args.dry_run:
        print(f"[dry-run] 将对 {sorted(cooled)} 触发 connector reload（user={pending[cooled[0]]}）")
        return 0

    uid = next((pending[s] for s in cooled if pending[s]), 0)
    if not uid:
        print("WARN 拿不到 user_id，跳过自愈", file=sys.stderr)
        return 1

    try:
        token = mint_token(args.db, uid)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR 签令牌失败: {exc}", file=sys.stderr)
        notify("Octop MCP 自愈失败", f"签令牌失败，{sorted(cooled)} 仍缺失：{exc}")
        return 1

    healed: list[str] = []
    failed: dict[str, str] = {}
    for server in sorted(cooled):
        try:
            status = patch_server(args.api, token, server)
            healed.append(f"{server}(HTTP {status})")
            state[server] = now
        except Exception as exc:  # noqa: BLE001
            failed[server] = str(exc)

    save_state(args.state, state)

    if healed:
        print(f"[{datetime.now():%F %T}] 已自愈: {', '.join(healed)} (user={uid})")
        notify(
            "Octop MCP 已自动恢复",
            f"检测到工具缺失，已触发 connector reload：{', '.join(healed)}。"
            "无需人工介入，但值得看一眼是不是 garmin token 出了问题。",
        )
    if failed:
        detail = "; ".join(f"{k}: {v}" for k, v in failed.items())
        print(f"ERROR 自愈失败: {detail}", file=sys.stderr)
        notify("Octop MCP 自愈失败", detail)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
