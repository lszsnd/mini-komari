#!/usr/bin/env python3
"""Mini Komari master + agent probe.

Stdlib-only Linux server monitor.

Modes:
  master: web dashboard + receive agent reports
  agent : collect local metrics and report to master
  standalone: single-node dashboard for quick local use

Routes in master/standalone:
  /                 HTML dashboard
  /api/nodes        JSON node list
  /api/status       alias of /api/nodes for compatibility
  /api/report       POST endpoint for agents
  /health           OK
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import platform
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Tuple
from urllib.parse import urlparse

START_TIME = time.time()
PREV_NET = None
PREV_CPU = None
PREV_SAMPLE_TIME = None
NODES: Dict[str, Dict[str, object]] = {}
NODES_LOCK = threading.Lock()
DATA_FILE = Path(os.environ.get("MINI_KOMARI_DATA_FILE", "/opt/mini-komari/nodes.json"))


def read_text(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(errors="ignore")
    except Exception:
        return default


def human_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    n = float(n)
    for unit in units:
        if abs(n) < 1024.0 or unit == units[-1]:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} PB"


def human_seconds(sec: float) -> str:
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}天 {h}小时 {m}分"
    if h:
        return f"{h}小时 {m}分"
    if m:
        return f"{m}分 {s}秒"
    return f"{s}秒"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_uptime() -> float:
    txt = read_text("/proc/uptime", "0 0").split()
    try:
        return float(txt[0])
    except Exception:
        return 0.0


def get_loadavg() -> Tuple[float, float, float]:
    try:
        return os.getloadavg()
    except Exception:
        return (0.0, 0.0, 0.0)


def parse_meminfo() -> Dict[str, int | float]:
    data: Dict[str, int] = {}
    for line in read_text("/proc/meminfo").splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.strip().split()
        if parts:
            data[key] = int(parts[0]) * 1024
    total = data.get("MemTotal", 0)
    avail = data.get("MemAvailable", data.get("MemFree", 0))
    used = max(0, total - avail)
    swap_total = data.get("SwapTotal", 0)
    swap_free = data.get("SwapFree", 0)
    return {
        "total": total,
        "available": avail,
        "used": used,
        "percent": round((used / total * 100) if total else 0, 1),
        "swap_total": swap_total,
        "swap_used": max(0, swap_total - swap_free),
        "swap_percent": round(((swap_total - swap_free) / swap_total * 100) if swap_total else 0, 1),
    }


def read_cpu_times() -> Tuple[int, int]:
    line = read_text("/proc/stat").splitlines()[0]
    parts = [int(x) for x in line.split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    return idle, sum(parts)


def get_cpu_percent() -> float:
    global PREV_CPU
    idle, total = read_cpu_times()
    if PREV_CPU is None:
        PREV_CPU = (idle, total)
        time.sleep(0.12)
        idle, total = read_cpu_times()
    prev_idle, prev_total = PREV_CPU
    PREV_CPU = (idle, total)
    total_delta = total - prev_total
    idle_delta = idle - prev_idle
    if total_delta <= 0:
        return 0.0
    return round((1.0 - idle_delta / total_delta) * 100, 1)


def get_cpu_info() -> Dict[str, object]:
    cpuinfo = read_text("/proc/cpuinfo")
    model = "Unknown CPU"
    for line in cpuinfo.splitlines():
        if line.lower().startswith(("model name", "hardware")) and ":" in line:
            val = line.split(":", 1)[1].strip()
            if val:
                model = val
                break
    return {
        "model": model,
        "cores": os.cpu_count() or 1,
        "percent": get_cpu_percent(),
        "load": [round(x, 2) for x in get_loadavg()],
    }


def get_disk(path: str = "/") -> Dict[str, object]:
    usage = shutil.disk_usage(path)
    used = usage.total - usage.free
    return {
        "path": path,
        "total": usage.total,
        "used": used,
        "free": usage.free,
        "percent": round((used / usage.total * 100) if usage.total else 0, 1),
    }


def get_net_totals() -> Dict[str, int]:
    rx = tx = 0
    for line in read_text("/proc/net/dev").splitlines()[2:]:
        if ":" not in line:
            continue
        iface, data = line.split(":", 1)
        if iface.strip() == "lo":
            continue
        parts = data.split()
        if len(parts) >= 16:
            rx += int(parts[0])
            tx += int(parts[8])
    return {"rx": rx, "tx": tx}


def get_network() -> Dict[str, object]:
    global PREV_NET, PREV_SAMPLE_TIME
    now = time.time()
    totals = get_net_totals()
    if PREV_NET is None:
        PREV_NET = totals.copy()
        PREV_SAMPLE_TIME = now
        rx_speed = tx_speed = 0.0
    else:
        dt = max(0.001, now - (PREV_SAMPLE_TIME or now))
        rx_speed = max(0.0, (totals["rx"] - PREV_NET["rx"]) / dt)
        tx_speed = max(0.0, (totals["tx"] - PREV_NET["tx"]) / dt)
        PREV_NET = totals.copy()
        PREV_SAMPLE_TIME = now
    return {
        "rx_total": totals["rx"],
        "tx_total": totals["tx"],
        "rx_speed": round(rx_speed, 1),
        "tx_speed": round(tx_speed, 1),
    }


def collect_status(node_id: str | None = None, name: str | None = None, group: str | None = None) -> Dict[str, object]:
    hostname = socket.gethostname()
    node_id = node_id or hostname
    name = name or hostname
    group = group or os.environ.get("MINI_KOMARI_NODE_GROUP", "默认")
    uptime = get_uptime()
    return {
        "id": node_id,
        "name": name,
        "group": group,
        "hostname": hostname,
        "time": now_iso(),
        "agent_uptime": human_seconds(time.time() - START_TIME),
        "system": {
            "os": platform.platform(),
            "kernel": platform.release(),
            "arch": platform.machine(),
            "python": platform.python_version(),
            "uptime_seconds": int(uptime),
            "uptime": human_seconds(uptime),
            "boot_time_utc": datetime.fromtimestamp(time.time() - uptime, tz=timezone.utc).isoformat(),
        },
        "cpu": get_cpu_info(),
        "memory": parse_meminfo(),
        "disk": get_disk("/"),
        "network": get_network(),
    }


def sign_body(body: bytes, token: str) -> str:
    return hmac.new(token.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, token: str, signature: str) -> bool:
    if not token:
        return True
    expected = sign_body(body, token)
    return hmac.compare_digest(expected, signature or "")


def set_data_file(path: str | Path) -> None:
    global DATA_FILE
    DATA_FILE = Path(path)


def load_nodes() -> None:
    if not DATA_FILE.exists():
        return
    try:
        payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        raw_nodes = payload.get("nodes", payload)
        if not isinstance(raw_nodes, dict):
            raise ValueError("nodes data must be an object")
        restored: Dict[str, Dict[str, object]] = {}
        for node_id, node in raw_nodes.items():
            if isinstance(node, dict):
                node = dict(node)
                node.setdefault("id", str(node_id))
                restored[str(node_id)] = node
        with NODES_LOCK:
            NODES.clear()
            NODES.update(restored)
        print(f"Loaded {len(restored)} nodes from {DATA_FILE}", flush=True)
    except Exception as exc:
        print(f"Failed to load nodes from {DATA_FILE}: {exc}", file=sys.stderr, flush=True)


def save_nodes() -> None:
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with NODES_LOCK:
            snapshot = {node_id: dict(node) for node_id, node in NODES.items()}
        tmp = DATA_FILE.with_name(f".{DATA_FILE.name}.tmp")
        payload = {"version": 1, "saved_at": now_iso(), "nodes": snapshot}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, DATA_FILE)
    except Exception as exc:
        print(f"Failed to save nodes to {DATA_FILE}: {exc}", file=sys.stderr, flush=True)


def node_public_view(node: Dict[str, object]) -> Dict[str, object]:
    last_seen = float(node.get("last_seen_ts", 0))
    age = max(0, time.time() - last_seen) if last_seen else 999999
    status = dict(node)
    status["online"] = age <= int(os.environ.get("MINI_KOMARI_OFFLINE_AFTER", "90"))
    status["last_seen_age"] = round(age, 1)
    status.pop("last_seen_ts", None)
    return status


def list_nodes() -> Dict[str, object]:
    with NODES_LOCK:
        nodes = [node_public_view(v) for v in NODES.values()]
    nodes.sort(key=lambda x: (not bool(x.get("online")), str(x.get("name", ""))))
    return {"server_time": now_iso(), "count": len(nodes), "nodes": nodes}


def pct_bar(percent: float) -> str:
    p = max(0.0, min(100.0, float(percent)))
    return f'<div class="bar"><span style="width:{p}%"></span></div>'


def render_html(data: Dict[str, object], refresh: int, public_url: str = "", raw_base: str = "", token_hint: str = "") -> bytes:
    nodes = data.get("nodes", [])
    total_nodes = len(nodes) if isinstance(nodes, list) else int(data.get("count", 0) or 0)
    online_nodes = sum(1 for n in nodes if isinstance(n, dict) and bool(n.get("online")))
    offline_nodes = max(0, total_nodes - online_nodes)
    group_names = {str(n.get("group", "默认") or "默认") for n in nodes if isinstance(n, dict)}
    group_count = len(group_names)
    public_url = public_url.rstrip("/")
    raw_base = raw_base.rstrip("/")
    token_hint = token_hint or "你的TOKEN"
    install_url = f"{raw_base}/install.sh" if raw_base else "https://raw.githubusercontent.com/你的用户名/你的仓库/main/install.sh"
    master_url = public_url or "http://主控IP:6060"
    agent_cmd = f"curl -fsSL {install_url} | bash -s -- agent {master_url} {token_hint} 节点名 默认"
    grouped_cards: Dict[str, list[str]] = {}
    for n in nodes:
        cpu = n.get("cpu", {})
        mem = n.get("memory", {})
        disk = n.get("disk", {})
        net = n.get("network", {})
        sysinfo = n.get("system", {})
        online = bool(n.get("online"))
        badge = "ONLINE" if online else "OFFLINE"
        badge_cls = "online" if online else "offline"
        group = str(n.get("group", "默认")) or "默认"
        node_id = html.escape(str(n.get('id', '')))
        grouped_cards.setdefault(group, []).append(f"""
        <section class="node" data-node-id="{node_id}">
          <div class="node-head">
            <div><h2>{html.escape(str(n.get('name','node')))}</h2><p>{html.escape(str(n.get('hostname','')))} · {html.escape(str(sysinfo.get('arch','')))} · {html.escape(group)}</p></div>
            <div class="actions"><span class="badge {badge_cls}">{badge}</span><button class="danger" onclick="deleteNode('{node_id}')">删除</button></div>
          </div>
          <div class="metrics">
            <div><b>CPU</b><strong>{cpu.get('percent',0)}%</strong>{pct_bar(float(cpu.get('percent',0)))}<small>{cpu.get('cores','?')} 核 · Load {cpu.get('load',[0,0,0])[0]}</small></div>
            <div><b>内存</b><strong>{mem.get('percent',0)}%</strong>{pct_bar(float(mem.get('percent',0)))}<small>{human_bytes(float(mem.get('used',0)))} / {human_bytes(float(mem.get('total',0)))}</small></div>
            <div><b>磁盘</b><strong>{disk.get('percent',0)}%</strong>{pct_bar(float(disk.get('percent',0)))}<small>{human_bytes(float(disk.get('used',0)))} / {human_bytes(float(disk.get('total',0)))}</small></div>
            <div><b>网络</b><strong>↓ {human_bytes(float(net.get('rx_speed',0)))}/s</strong><small>↑ {human_bytes(float(net.get('tx_speed',0)))}/s</small><small>总↓ {human_bytes(float(net.get('rx_total',0)))} · 总↑ {human_bytes(float(net.get('tx_total',0)))}</small></div>
          </div>
          <div class="foot">系统运行 {html.escape(str(sysinfo.get('uptime','')))} · 最后上报 {n.get('last_seen_age','?')} 秒前 · {html.escape(str(sysinfo.get('os','')))}</div>
        </section>
        """)
    group_html = "".join(
        f'<section class="group"><h2>{html.escape(group)} <span>{len(cards)} 台</span></h2>{"".join(cards)}</section>'
        for group, cards in sorted(grouped_cards.items())
    )
    empty = "" if grouped_cards else '<section class="empty">还没有 Agent 上报。先在上面生成安装命令，再去被控机执行。</section>'
    body = f"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mini Komari Master</title>
<style>
:root {{ color-scheme: light; --bg:#f5f7fb; --card:#ffffff; --card-soft:#f9fafc; --muted:#6b7280; --text:#111827; --line:#e5e7eb; --line-strong:#d1d5db; --good:#16a34a; --bad:#dc2626; --accent:#4f6f9f; --accent-soft:#eef3fb; --shadow:0 12px 32px rgba(15,23,42,.08); }}
*{{box-sizing:border-box}} body{{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:linear-gradient(180deg,#ffffff,#f3f6fb 42%,#eef2f7);color:var(--text)}}
.wrap{{max-width:1180px;margin:0 auto;padding:30px 16px}} .hero{{display:flex;justify-content:space-between;gap:14px;align-items:flex-end;margin-bottom:18px;padding:18px 20px;background:rgba(255,255,255,.78);border:1px solid var(--line);border-radius:24px;box-shadow:var(--shadow);backdrop-filter:blur(10px)}} h1{{margin:0;font-size:31px;letter-spacing:-.03em}} .sub,p,small{{color:var(--muted)}} a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
.node,.generator{{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:18px;margin:14px 0;box-shadow:var(--shadow)}} .stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0}} .stat{{background:linear-gradient(180deg,#fff,#f8fafc);border:1px solid var(--line);border-radius:20px;padding:16px;box-shadow:0 8px 22px rgba(15,23,42,.06)}} .stat b{{font-size:13px;color:var(--muted);font-weight:650}} .stat strong{{font-size:31px;margin:4px 0 0;letter-spacing:-.03em}} .stat.online strong{{color:var(--good)}} .stat.offline strong{{color:var(--bad)}} .group>h2{{margin:24px 0 9px;font-size:18px;color:#1f2937}} .group>h2 span{{color:var(--muted);font-size:13px;font-weight:500}} .node-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}} h2{{margin:0;font-size:22px;letter-spacing:-.02em}} p{{margin:4px 0 0}} .actions{{display:flex;gap:8px;align-items:center}} .badge{{padding:6px 10px;border-radius:999px;font-weight:800;font-size:12px;letter-spacing:.02em}} .online{{background:#dcfce7;color:#166534;border:1px solid #bbf7d0}} .offline{{background:#fee2e2;color:#991b1b;border:1px solid #fecaca}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}} .metrics>div{{border:1px solid var(--line);background:var(--card-soft);border-radius:16px;padding:13px}} b{{display:block;color:var(--muted);font-size:13px}} strong{{display:block;font-size:24px;margin:6px 0;letter-spacing:-.02em}} small{{display:block;font-size:12px;line-height:1.5;overflow-wrap:anywhere}} .bar{{height:8px;border-radius:999px;background:#e5e7eb;overflow:hidden;margin:9px 0}} .bar span{{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#94a3b8,var(--accent))}} .foot{{margin-top:12px;color:var(--muted);font-size:13px}} .empty{{background:#fff;border:1px dashed var(--line-strong);border-radius:18px;padding:22px;color:var(--muted)}} .refresh-note{{margin-top:4px;color:var(--muted);font-size:12px;text-align:right}}
.form{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:12px}} label{{display:block;color:var(--muted);font-size:13px;margin-bottom:5px}} input{{width:100%;border:1px solid var(--line-strong);background:#fff;color:var(--text);border-radius:12px;padding:10px 11px;outline:none;transition:border-color .15s,box-shadow .15s}} input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}} .inline-field{{display:flex;gap:7px}} .inline-field input{{min-width:0}} .inline-field button{{white-space:nowrap;padding:10px 11px}} pre{{white-space:pre-wrap;word-break:break-all;background:#f8fafc;border:1px solid var(--line);border-radius:14px;padding:13px;color:#334155}} button{{border:1px solid #cbd5e1;background:linear-gradient(180deg,#fff,#eef2f7);color:#1f2937;font-weight:800;border-radius:12px;padding:10px 13px;cursor:pointer;box-shadow:0 3px 10px rgba(15,23,42,.06)}} button:hover{{background:linear-gradient(180deg,#fff,#e5eaf2)}} button.danger{{background:#fff;color:var(--bad);border:1px solid #fecaca;padding:6px 9px;box-shadow:none}}
@media(max-width:860px){{.metrics,.stats{{grid-template-columns:1fr 1fr}}.hero{{flex-direction:column;align-items:flex-start}}.refresh-note{{text-align:left}}}} @media(max-width:520px){{.metrics,.stats,.form{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<div class="hero"><div><h1>Mini Komari Master</h1><div class="sub">主控面板 · 节点 {total_nodes} 个 · 在线 {online_nodes} · 离线 {offline_nodes} · <a href="/api/nodes">JSON API</a></div></div><div><div class="sub">自动刷新 {refresh}s</div><div class="refresh-note" id="refreshNote">输入时自动暂停刷新</div></div></div>
<section class="stats" aria-label="节点统计">
  <div class="stat"><b>总节点</b><strong>{total_nodes}</strong><small>当前已登记节点</small></div>
  <div class="stat online"><b>在线</b><strong>{online_nodes}</strong><small>最近上报正常</small></div>
  <div class="stat offline"><b>离线</b><strong>{offline_nodes}</strong><small>超过离线阈值</small></div>
  <div class="stat"><b>分组</b><strong>{group_count}</strong><small>节点分组数量</small></div>
</section>
<section class="generator">
  <h2>生成被控 Agent 安装命令</h2>
  <p>先确认主控地址能被被控 VPS 访问，然后填写节点名，复制命令到被控 VPS 执行。</p>
  <div class="form">
    <div><label>主控地址</label><input id="masterUrl" value="{html.escape(master_url)}"></div>
    <div><label>节点名</label><input id="nodeName" value="hk-node-1"></div>
    <div><label>分组</label><input id="nodeGroup" value="香港"></div>
    <div><label>Token</label><div class="inline-field"><input id="token" type="password" autocomplete="off" value="{html.escape(token_hint)}"><button type="button" onclick="toggleToken()" id="toggleTokenBtn">显示</button></div></div>
  </div>
  <pre id="agentCmd">{html.escape(agent_cmd)}</pre>
  <button onclick="copyCmd()">复制安装命令</button>
  <small>安装脚本地址：{html.escape(install_url)}</small>
</section>
{group_html}{empty}
<script>
const installUrl = {json.dumps(install_url)};
const refreshSeconds = {int(refresh)};
let editing = false;
let lastEditAt = 0;
function buildCmd() {{
  const master = document.getElementById('masterUrl').value.trim().replace(/\/$/, '');
  const node = document.getElementById('nodeName').value.trim() || 'node-1';
  const group = document.getElementById('nodeGroup').value.trim() || '默认';
  const token = document.getElementById('token').value.trim() || '你的TOKEN';
  document.getElementById('agentCmd').textContent = `curl -fsSL ${{installUrl}} | bash -s -- agent ${{master}} ${{token}} ${{node}} ${{group}}`;
}}
function fallbackCopy(text) {{
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  ta.setSelectionRange(0, ta.value.length);
  let ok = false;
  try {{ ok = document.execCommand('copy'); }} catch (e) {{ ok = false; }}
  document.body.removeChild(ta);
  return ok;
}}
async function copyCmd() {{
  buildCmd();
  const text = document.getElementById('agentCmd').textContent;
  try {{
    if (navigator.clipboard && window.isSecureContext) {{
      await navigator.clipboard.writeText(text);
      alert('已复制');
      return;
    }}
  }} catch (e) {{}}
  if (fallbackCopy(text)) {{
    alert('已复制');
  }} else {{
    prompt('自动复制失败，请手动复制下面这条命令：', text);
  }}
}}
function toggleToken() {{
  const input = document.getElementById('token');
  const btn = document.getElementById('toggleTokenBtn');
  const hidden = input.type === 'password';
  input.type = hidden ? 'text' : 'password';
  btn.textContent = hidden ? '隐藏' : '显示';
}}
function deleteNode(id) {{
  if (!confirm(`确定删除节点 ${{id}}？Agent 如果还在运行，稍后会上报回来。`)) return;
  fetch('/api/delete', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{id}})}})
    .then(r => r.ok ? location.reload() : r.text().then(t => alert('删除失败：' + t)));
}}
function markEditing() {{
  editing = true;
  lastEditAt = Date.now();
  const note = document.getElementById('refreshNote');
  if (note) note.textContent = '正在输入，已暂停自动刷新';
}}
function markIdleSoon() {{
  lastEditAt = Date.now();
  window.setTimeout(() => {{
    if (Date.now() - lastEditAt >= 15000 && !document.querySelector('input:focus')) {{
      editing = false;
      const note = document.getElementById('refreshNote');
      if (note) note.textContent = '输入结束，自动刷新已恢复';
    }}
  }}, 15000);
}}
['masterUrl','nodeName','nodeGroup','token'].forEach(id => {{
  const el = document.getElementById(id);
  el.addEventListener('input', () => {{ buildCmd(); markEditing(); }});
  el.addEventListener('focus', markEditing);
  el.addEventListener('blur', markIdleSoon);
}});
window.setInterval(() => {{
  if (!editing && !document.querySelector('input:focus')) location.reload();
}}, Math.max(1, refreshSeconds) * 1000);
buildCmd();
</script>
</div></body></html>"""
    return body.encode("utf-8")


class MasterHandler(BaseHTTPRequestHandler):
    server_version = "MiniKomari/0.2"

    def log_message(self, fmt: str, *args) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(fmt, *args)

    def send_body(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def auth_required(self) -> bool:
        return bool(getattr(self.server, "auth_user", "") or getattr(self.server, "auth_pass", ""))

    def check_basic_auth(self) -> bool:
        if not self.auth_required():
            return True
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth.split(" ", 1)[1], validate=True).decode("utf-8")
            user, password = decoded.split(":", 1)
        except Exception:
            return False
        expected_user = str(getattr(self.server, "auth_user", ""))
        expected_pass = str(getattr(self.server, "auth_pass", ""))
        return hmac.compare_digest(user, expected_user) and hmac.compare_digest(password, expected_pass)

    def require_basic_auth(self) -> bool:
        if self.check_basic_auth():
            return True
        body = b"Authentication required\n"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Mini Komari"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        return False

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self.send_body(200, b"OK\n", "text/plain; charset=utf-8")
            return
        if not self.require_basic_auth():
            return
        data = list_nodes()
        if path in ("/api/nodes", "/api/status"):
            self.send_body(200, json.dumps(data, ensure_ascii=False, indent=2).encode(), "application/json; charset=utf-8")
        elif path == "/":
            self.send_body(200, render_html(
                data,
                int(getattr(self.server, "refresh", 3)),
                str(getattr(self.server, "public_url", "")),
                str(getattr(self.server, "raw_base", "")),
                str(getattr(self.server, "token_hint", "")),
            ), "text/html; charset=utf-8")
        else:
            self.send_body(404, b"Not Found\n", "text/plain; charset=utf-8")

    def handle_delete(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
            node_id = str(payload.get("id") or "")
            if not node_id:
                self.send_body(400, b"missing id\n", "text/plain; charset=utf-8")
                return
            with NODES_LOCK:
                existed = node_id in NODES
                NODES.pop(node_id, None)
            save_nodes()
            self.send_body(200, json.dumps({"ok": True, "deleted": existed}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        except Exception as exc:
            self.send_body(400, f"bad json: {exc}\n".encode(), "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/delete":
            if not self.require_basic_auth():
                return
            self.handle_delete()
            return
        if path != "/api/report":
            self.send_body(404, b"Not Found\n", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 2_000_000:
            self.send_body(400, b"bad length\n", "text/plain; charset=utf-8")
            return
        body = self.rfile.read(length)
        token = getattr(self.server, "token", "")
        sig = self.headers.get("X-Mini-KOMARI-Signature", "")
        if not verify_signature(body, token, sig):
            self.send_body(401, b"bad signature\n", "text/plain; charset=utf-8")
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            node_id = str(payload.get("id") or payload.get("hostname") or "unknown")
            payload["last_seen"] = now_iso()
            payload["last_seen_ts"] = time.time()
            with NODES_LOCK:
                NODES[node_id] = payload
            save_nodes()
            self.send_body(200, b"OK\n", "text/plain; charset=utf-8")
        except Exception as exc:
            self.send_body(400, f"bad json: {exc}\n".encode(), "text/plain; charset=utf-8")


def run_master(args: argparse.Namespace, standalone: bool = False) -> None:
    set_data_file(getattr(args, "data_file", "") or os.environ.get("MINI_KOMARI_DATA_FILE", DATA_FILE))
    load_nodes()
    if standalone:
        status = collect_status(args.node_id, args.name, getattr(args, "group", "默认"))
        status["last_seen"] = now_iso()
        status["last_seen_ts"] = time.time()
        with NODES_LOCK:
            NODES[str(status["id"])] = status
        save_nodes()
        def updater() -> None:
            while True:
                try:
                    s = collect_status(args.node_id, args.name, getattr(args, "group", "默认"))
                    s["last_seen"] = now_iso()
                    s["last_seen_ts"] = time.time()
                    with NODES_LOCK:
                        NODES[str(s["id"])] = s
                    save_nodes()
                except Exception as exc:
                    print(f"standalone update failed: {exc}", file=sys.stderr, flush=True)
                time.sleep(max(1, args.interval))
        threading.Thread(target=updater, daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), MasterHandler)
    httpd.refresh = max(1, args.refresh)
    httpd.quiet = args.quiet
    httpd.token = args.token or os.environ.get("MINI_KOMARI_TOKEN", "")
    httpd.public_url = getattr(args, "public_url", "") or os.environ.get("MINI_KOMARI_PUBLIC_URL", "")
    httpd.raw_base = getattr(args, "raw_base", "") or os.environ.get("MINI_KOMARI_RAW_BASE", "")
    httpd.token_hint = httpd.token
    httpd.auth_user = getattr(args, "auth_user", "") or os.environ.get("MINI_KOMARI_AUTH_USER", "")
    httpd.auth_pass = getattr(args, "auth_pass", "") or os.environ.get("MINI_KOMARI_AUTH_PASS", "")
    print(f"Mini Komari master listening on http://{args.host}:{args.port}", flush=True)
    httpd.serve_forever()


def post_json(url: str, payload: Dict[str, object], token: str, timeout: int = 10) -> Tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "MiniKomariAgent/0.2"}
    if token:
        headers["X-Mini-KOMARI-Signature"] = sign_body(body, token)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "ignore")


def run_agent(args: argparse.Namespace) -> None:
    report_url = args.master.rstrip("/") + "/api/report"
    token = args.token or os.environ.get("MINI_KOMARI_TOKEN", "")
    print(f"Mini Komari agent reporting to {report_url}", flush=True)
    while True:
        try:
            payload = collect_status(args.node_id, args.name, args.group)
            code, text = post_json(report_url, payload, token)
            if not args.quiet:
                print(f"reported {payload['id']} -> HTTP {code} {text.strip()}", flush=True)
        except Exception as exc:
            print(f"report failed: {exc}", file=sys.stderr, flush=True)
        if args.once:
            break
        time.sleep(max(1, args.interval))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mini Komari master + agent probe")
    sub = parser.add_subparsers(dest="mode")

    p_master = sub.add_parser("master", help="run master dashboard")
    p_master.add_argument("--host", default=os.environ.get("MINI_KOMARI_HOST", "0.0.0.0"))
    p_master.add_argument("--port", type=int, default=int(os.environ.get("MINI_KOMARI_PORT", "6060")))
    p_master.add_argument("--refresh", type=int, default=int(os.environ.get("MINI_KOMARI_REFRESH", "3")))
    p_master.add_argument("--token", default=os.environ.get("MINI_KOMARI_TOKEN", ""))
    p_master.add_argument("--public-url", default=os.environ.get("MINI_KOMARI_PUBLIC_URL", ""), help="public master URL shown in generated agent command")
    p_master.add_argument("--raw-base", default=os.environ.get("MINI_KOMARI_RAW_BASE", ""), help="GitHub raw base URL for install.sh")
    p_master.add_argument("--data-file", default=os.environ.get("MINI_KOMARI_DATA_FILE", str(DATA_FILE)), help="node persistence JSON file")
    p_master.add_argument("--auth-user", default=os.environ.get("MINI_KOMARI_AUTH_USER", ""), help="Basic Auth username for dashboard/API")
    p_master.add_argument("--auth-pass", default=os.environ.get("MINI_KOMARI_AUTH_PASS", ""), help="Basic Auth password for dashboard/API")
    p_master.add_argument("--quiet", action="store_true", default=os.environ.get("MINI_KOMARI_QUIET") == "1")

    p_agent = sub.add_parser("agent", help="run agent reporter")
    p_agent.add_argument("--master", required=True, help="master base URL, e.g. http://1.2.3.4:6060")
    p_agent.add_argument("--token", default=os.environ.get("MINI_KOMARI_TOKEN", ""))
    p_agent.add_argument("--node-id", default=os.environ.get("MINI_KOMARI_NODE_ID") or socket.gethostname())
    p_agent.add_argument("--name", default=os.environ.get("MINI_KOMARI_NODE_NAME") or socket.gethostname())
    p_agent.add_argument("--group", default=os.environ.get("MINI_KOMARI_NODE_GROUP", "默认"))
    p_agent.add_argument("--interval", type=int, default=int(os.environ.get("MINI_KOMARI_INTERVAL", "5")))
    p_agent.add_argument("--once", action="store_true")
    p_agent.add_argument("--quiet", action="store_true", default=os.environ.get("MINI_KOMARI_QUIET") == "1")

    p_single = sub.add_parser("standalone", help="single-node dashboard")
    p_single.add_argument("--host", default=os.environ.get("MINI_KOMARI_HOST", "0.0.0.0"))
    p_single.add_argument("--port", type=int, default=int(os.environ.get("MINI_KOMARI_PORT", "6060")))
    p_single.add_argument("--refresh", type=int, default=int(os.environ.get("MINI_KOMARI_REFRESH", "3")))
    p_single.add_argument("--interval", type=int, default=int(os.environ.get("MINI_KOMARI_INTERVAL", "3")))
    p_single.add_argument("--node-id", default=os.environ.get("MINI_KOMARI_NODE_ID") or socket.gethostname())
    p_single.add_argument("--name", default=os.environ.get("MINI_KOMARI_NODE_NAME") or socket.gethostname())
    p_single.add_argument("--group", default=os.environ.get("MINI_KOMARI_NODE_GROUP", "默认"))
    p_single.add_argument("--token", default="")
    p_single.add_argument("--public-url", default=os.environ.get("MINI_KOMARI_PUBLIC_URL", ""))
    p_single.add_argument("--raw-base", default=os.environ.get("MINI_KOMARI_RAW_BASE", ""))
    p_single.add_argument("--data-file", default=os.environ.get("MINI_KOMARI_DATA_FILE", str(DATA_FILE)))
    p_single.add_argument("--auth-user", default=os.environ.get("MINI_KOMARI_AUTH_USER", ""))
    p_single.add_argument("--auth-pass", default=os.environ.get("MINI_KOMARI_AUTH_PASS", ""))
    p_single.add_argument("--quiet", action="store_true", default=os.environ.get("MINI_KOMARI_QUIET") == "1")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.mode is None:
        args = parser.parse_args(["standalone"])
    if args.mode == "master":
        run_master(args)
    elif args.mode == "agent":
        run_agent(args)
    elif args.mode == "standalone":
        run_master(args, standalone=True)
    else:
        parser.error("unknown mode")


if __name__ == "__main__":
    main()
