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
import http.cookies
import json
import os
import platform
import re
import secrets
import shlex
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
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

START_TIME = time.time()
PREV_NET = None
PREV_CPU = None
PREV_SAMPLE_TIME = None
NODES: Dict[str, Dict[str, object]] = {}
NODES_LOCK = threading.Lock()
SESSIONS: Dict[str, float] = {}
SESSIONS_LOCK = threading.Lock()
DATA_FILE = Path(os.environ.get("MINI_KOMARI_DATA_FILE", "/opt/mini-komari/nodes.json"))
USER_FILE = Path(os.environ.get("MINI_KOMARI_USER_FILE", "/opt/mini-komari/user.json"))
SESSION_TTL = 86400 * 7
SAFE_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
PROBE_ICON_SVG_B64 = "PHN2ZyB3aWR0aD0iMTAyNCIgaGVpZ2h0PSIxMDI0IiB2aWV3Qm94PSIwIDAgMTAyNCAxMDI0IiBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgogIDx0aXRsZT5NaW5pIEtvbWFyaSBQcm9iZSBJY29uPC90aXRsZT4KICA8ZGVmcz4KICAgIDxsaW5lYXJHcmFkaWVudCBpZD0iYmciIHgxPSIxODAiIHkxPSIxMjgiIHgyPSI4NDQiIHkyPSI4OTYiIGdyYWRpZW50VW5pdHM9InVzZXJTcGFjZU9uVXNlIj4KICAgICAgPHN0b3Agc3RvcC1jb2xvcj0iI0Y4RkJGRiIvPgogICAgICA8c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiNFRUY3RjUiLz4KICAgIDwvbGluZWFyR3JhZGllbnQ+CiAgICA8bGluZWFyR3JhZGllbnQgaWQ9InByb2JlIiB4MT0iMzQ0IiB5MT0iNzA0IiB4Mj0iNzA2IiB5Mj0iMzMwIiBncmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSI+CiAgICAgIDxzdG9wIHN0b3AtY29sb3I9IiM0RDZCRkYiLz4KICAgICAgPHN0b3Agb2Zmc2V0PSIxIiBzdG9wLWNvbG9yPSIjMTZDOEEwIi8+CiAgICA8L2xpbmVhckdyYWRpZW50PgogICAgPGxpbmVhckdyYWRpZW50IGlkPSJjb3JlIiB4MT0iMzkyIiB5MT0iNjMyIiB4Mj0iNjM4IiB5Mj0iMzg0IiBncmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSI+CiAgICAgIDxzdG9wIHN0b3AtY29sb3I9IiMyNzMwNEYiLz4KICAgICAgPHN0b3Agb2Zmc2V0PSIxIiBzdG9wLWNvbG9yPSIjMTUxQTJEIi8+CiAgICA8L2xpbmVhckdyYWRpZW50PgogICAgPGZpbHRlciBpZD0ic29mdFNoYWRvdyIgeD0iODQiIHk9IjgyIiB3aWR0aD0iODU2IiBoZWlnaHQ9Ijg3MiIgZmlsdGVyVW5pdHM9InVzZXJTcGFjZU9uVXNlIiBjb2xvci1pbnRlcnBvbGF0aW9uLWZpbHRlcnM9InNSR0IiPgogICAgICA8ZmVGbG9vZCBmbG9vZC1vcGFjaXR5PSIwIiByZXN1bHQ9IkJhY2tncm91bmRJbWFnZUZpeCIvPgogICAgICA8ZmVDb2xvck1hdHJpeCBpbj0iU291cmNlQWxwaGEiIHR5cGU9Im1hdHJpeCIgdmFsdWVzPSIwIDAgMCAwIDAgMCAwIDAgMCAwIDAgMCAwIDAgMCAwIDAgMCAxMjcgMCIgcmVzdWx0PSJoYXJkQWxwaGEiLz4KICAgICAgPGZlT2Zmc2V0IGR5PSIxNiIvPgogICAgICA8ZmVHYXVzc2lhbkJsdXIgc3RkRGV2aWF0aW9uPSIyOCIvPgogICAgICA8ZmVDb2xvck1hdHJpeCB0eXBlPSJtYXRyaXgiIHZhbHVlcz0iMCAwIDAgMCAwLjEyIDAgMCAwIDAgMC4xOSAwIDAgMCAwIDAuMzAgMCAwIDAgMC4xNiAwIi8+CiAgICAgIDxmZUJsZW5kIG1vZGU9Im5vcm1hbCIgaW4yPSJCYWNrZ3JvdW5kSW1hZ2VGaXgiIHJlc3VsdD0iZWZmZWN0MV9kcm9wU2hhZG93XzFfMSIvPgogICAgICA8ZmVCbGVuZCBtb2RlPSJub3JtYWwiIGluPSJTb3VyY2VHcmFwaGljIiBpbjI9ImVmZmVjdDFfZHJvcFNoYWRvd18xXzEiIHJlc3VsdD0ic2hhcGUiLz4KICAgIDwvZmlsdGVyPgogICAgPGNsaXBQYXRoIGlkPSJjbGlwIj4KICAgICAgPHJlY3QgeD0iMTI4IiB5PSIxMjgiIHdpZHRoPSI3NjgiIGhlaWdodD0iNzY4IiByeD0iMTg4Ii8+CiAgICA8L2NsaXBQYXRoPgogIDwvZGVmcz4KCiAgPGcgZmlsdGVyPSJ1cmwoI3NvZnRTaGFkb3cpIj4KICAgIDxyZWN0IHg9IjEyOCIgeT0iMTI4IiB3aWR0aD0iNzY4IiBoZWlnaHQ9Ijc2OCIgcng9IjE4OCIgZmlsbD0idXJsKCNiZykiLz4KICAgIDxyZWN0IHg9IjE0OCIgeT0iMTQ4IiB3aWR0aD0iNzI4IiBoZWlnaHQ9IjcyOCIgcng9IjE2OCIgc3Ryb2tlPSIjRkZGRkZGIiBzdHJva2Utd2lkdGg9IjQwIiBzdHJva2Utb3BhY2l0eT0iMC44NiIvPgogIDwvZz4KCiAgPGcgY2xpcC1wYXRoPSJ1cmwoI2NsaXApIj4KICAgIDxwYXRoIGQ9Ik0yMTQgMzUySDMxOEMzNDcuODIzIDM1MiAzNzYuNDI1IDM2My44NSAzOTcuNTE1IDM4NC45MjlMNDM5LjA3MSA0MjYuNDY0QzQ2MC4xNjggNDQ3LjU1MSA0ODguNzgxIDQ1OS40IDUxOC42MTUgNDU5LjRIODEwIiBzdHJva2U9IiNDOUQ4RTgiIHN0cm9rZS13aWR0aD0iMjQiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgogICAgPHBhdGggZD0iTTIxNCA2NzJIMzM2QzM2NS44MjMgNjcyIDM5NC40MjUgNjYwLjE1IDQxNS41MTUgNjM5LjA3MUw0NjAuNDg1IDU5NC4xMjlDNDgxLjU3NSA1NzMuMDUgNTEwLjE3NyA1NjEuMiA1NDAgNTYxLjJIODEwIiBzdHJva2U9IiNDOUQ4RTgiIHN0cm9rZS13aWR0aD0iMjQiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgogICAgPHBhdGggZD0iTTIyOCA1MTJINzkyIiBzdHJva2U9IiNEREU4RjIiIHN0cm9rZS13aWR0aD0iMTgiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWRhc2hhcnJheT0iMiA1NCIvPgogIDwvZz4KCiAgPGNpcmNsZSBjeD0iMzAyIiBjeT0iMzUyIiByPSI0NCIgZmlsbD0iI0ZGRkZGRiIvPgogIDxjaXJjbGUgY3g9IjMwMiIgY3k9IjM1MiIgcj0iMjIiIGZpbGw9IiMxNkM4QTAiLz4KICA8Y2lyY2xlIGN4PSI3NTAiIGN5PSI0NTkiIHI9IjM0IiBmaWxsPSIjRkZGRkZGIi8+CiAgPGNpcmNsZSBjeD0iNzUwIiBjeT0iNDU5IiByPSIxNiIgZmlsbD0iIzRENkJGRiIvPgogIDxjaXJjbGUgY3g9IjMzMiIgY3k9IjY3MiIgcj0iMzYiIGZpbGw9IiNGRkZGRkYiLz4KICA8Y2lyY2xlIGN4PSIzMzIiIGN5PSI2NzIiIHI9IjE1IiBmaWxsPSIjN0E4QUEwIi8+CgogIDxwYXRoIGQ9Ik02NzUuMDE0IDI5OS41MzZDNjk1LjUzOCAyNzkuMDEyIDcyOC44MTIgMjc5LjAxMiA3NDkuMzM2IDI5OS41MzZMNzUyLjQ2NCAzMDIuNjY0Qzc3Mi45ODggMzIzLjE4OCA3NzIuOTg4IDM1Ni40NjIgNzUyLjQ2NCAzNzYuOTg2TDQ5Ny4xNzYgNjMyLjI3NEM0ODkuNzI2IDYzOS43MjQgNDgwLjIwMSA2NDQuNzY1IDQ2OS44NDcgNjQ2LjczN0wzNzQuNzU2IDY2NC44NUMzNTIuMDE1IDY2OS4xODIgMzMyLjgxOCA2NDkuOTg1IDMzNy4xNSA2MjcuMjQ0TDM1NS4yNjMgNTMyLjE1M0MzNTcuMjM1IDUyMS43OTkgMzYyLjI3NiA1MTIuMjc0IDM2OS43MjYgNTA0LjgyNEw2NzUuMDE0IDI5OS41MzZaIiBmaWxsPSJ1cmwoI3Byb2JlKSIvPgogIDxwYXRoIGQ9Ik02NTIuNTI0IDMyNi44NTFMNzI0LjE0OSAzOTguNDc2TDQ4OC45NzkgNjMzLjY0NkM0ODMuNDA4IDYzOS4yMTcgNDc2LjE5OSA2NDIuODU5IDQ2OC40MDkgNjQ0LjAzOUwzODYuMjgxIDY1Ni40NzZDMzc1LjMgNjU4LjEzOSAzNjUuODYxIDY0OC43IDM2Ny41MjQgNjM3LjcxOUwzNzkuOTYxIDU1NS41OTFDMzgxLjE0MSA1NDcuODAxIDM4NC43ODMgNTQwLjU5MiAzOTAuMzU0IDUzNS4wMjFMNjUyLjUyNCAzMjYuODUxWiIgZmlsbD0idXJsKCNjb3JlKSIgZmlsbC1vcGFjaXR5PSIwLjk2Ii8+CiAgPHBhdGggZD0iTTYzNSAzNjVMNjg4LjUgNDE4LjUiIHN0cm9rZT0iI0ZGRkZGRiIgc3Ryb2tlLXdpZHRoPSIyMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2Utb3BhY2l0eT0iMC45MiIvPgogIDxwYXRoIGQ9Ik00MDUgNTg2TDQ2MS41IDY0Mi41IiBzdHJva2U9IiMxNkM4QTAiIHN0cm9rZS13aWR0aD0iMTYiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgogIDxwYXRoIGQ9Ik0zNTUgNjQ2TDM4MSA2MjBMNDA0IDY0M0wzNjUgNjUyQzM1OC41IDY1My41IDM1My4yIDY1MC44IDM1NSA2NDZaIiBmaWxsPSIjMTZDOEEwIi8+CgogIDxjaXJjbGUgY3g9IjUxMiIgY3k9IjUxMiIgcj0iMTI2IiBzdHJva2U9IiNGRkZGRkYiIHN0cm9rZS13aWR0aD0iMTgiIHN0cm9rZS1vcGFjaXR5PSIwLjYyIi8+CiAgPGNpcmNsZSBjeD0iNTEyIiBjeT0iNTEyIiByPSI5MCIgc3Ryb2tlPSIjRERFOEYyIiBzdHJva2Utd2lkdGg9IjE0IiBzdHJva2Utb3BhY2l0eT0iMC44OCIvPgogIDxjaXJjbGUgY3g9IjUxMiIgY3k9IjUxMiIgcj0iMzIiIGZpbGw9IiNGRkZGRkYiLz4KICA8Y2lyY2xlIGN4PSI1MTIiIGN5PSI1MTIiIHI9IjE0IiBmaWxsPSIjMTZDOEEwIi8+Cjwvc3ZnPgo="
PROBE_MARK_SVG_B64 = "PHN2ZyB3aWR0aD0iMTAyNCIgaGVpZ2h0PSIxMDI0IiB2aWV3Qm94PSIwIDAgMTAyNCAxMDI0IiBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgogIDx0aXRsZT5NaW5pIEtvbWFyaSBQcm9iZSBNYXJrPC90aXRsZT4KICA8ZGVmcz4KICAgIDxsaW5lYXJHcmFkaWVudCBpZD0icHJvYmUiIHgxPSIzNDQiIHkxPSI3MDQiIHgyPSI3MDYiIHkyPSIzMzAiIGdyYWRpZW50VW5pdHM9InVzZXJTcGFjZU9uVXNlIj4KICAgICAgPHN0b3Agc3RvcC1jb2xvcj0iIzRENkJGRiIvPgogICAgICA8c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiMxNkM4QTAiLz4KICAgIDwvbGluZWFyR3JhZGllbnQ+CiAgICA8bGluZWFyR3JhZGllbnQgaWQ9ImNvcmUiIHgxPSIzOTIiIHkxPSI2MzIiIHgyPSI2MzgiIHkyPSIzODQiIGdyYWRpZW50VW5pdHM9InVzZXJTcGFjZU9uVXNlIj4KICAgICAgPHN0b3Agc3RvcC1jb2xvcj0iIzI3MzA0RiIvPgogICAgICA8c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiMxNTFBMkQiLz4KICAgIDwvbGluZWFyR3JhZGllbnQ+CiAgICA8ZmlsdGVyIGlkPSJzb2Z0U2hhZG93IiB4PSIyNDEiIHk9IjIyNyIgd2lkdGg9IjU2NyIgaGVpZ2h0PSI1MTQiIGZpbHRlclVuaXRzPSJ1c2VyU3BhY2VPblVzZSIgY29sb3ItaW50ZXJwb2xhdGlvbi1maWx0ZXJzPSJzUkdCIj4KICAgICAgPGZlRmxvb2QgZmxvb2Qtb3BhY2l0eT0iMCIgcmVzdWx0PSJCYWNrZ3JvdW5kSW1hZ2VGaXgiLz4KICAgICAgPGZlQ29sb3JNYXRyaXggaW49IlNvdXJjZUFscGhhIiB0eXBlPSJtYXRyaXgiIHZhbHVlcz0iMCAwIDAgMCAwIDAgMCAwIDAgMCAwIDAgMCAwIDAgMCAwIDAgMTI3IDAiIHJlc3VsdD0iaGFyZEFscGhhIi8+CiAgICAgIDxmZU9mZnNldCBkeT0iMTIiLz4KICAgICAgPGZlR2F1c3NpYW5CbHVyIHN0ZERldmlhdGlvbj0iMjAiLz4KICAgICAgPGZlQ29sb3JNYXRyaXggdHlwZT0ibWF0cml4IiB2YWx1ZXM9IjAgMCAwIDAgMC4xMiAwIDAgMCAwIDAuMTkgMCAwIDAgMCAwLjMwIDAgMCAwIDAuMTggMCIvPgogICAgICA8ZmVCbGVuZCBtb2RlPSJub3JtYWwiIGluMj0iQmFja2dyb3VuZEltYWdlRml4IiByZXN1bHQ9ImVmZmVjdDFfZHJvcFNoYWRvd18xXzEiLz4KICAgICAgPGZlQmxlbmQgbW9kZT0ibm9ybWFsIiBpbj0iU291cmNlR3JhcGhpYyIgaW4yPSJlZmZlY3QxX2Ryb3BTaGFkb3dfMV8xIiByZXN1bHQ9InNoYXBlIi8+CiAgICA8L2ZpbHRlcj4KICA8L2RlZnM+CgogIDxwYXRoIGQ9Ik0yMTQgMzUySDMxOEMzNDcuODIzIDM1MiAzNzYuNDI1IDM2My44NSAzOTcuNTE1IDM4NC45MjlMNDM5LjA3MSA0MjYuNDY0QzQ2MC4xNjggNDQ3LjU1MSA0ODguNzgxIDQ1OS40IDUxOC42MTUgNDU5LjRIODEwIiBzdHJva2U9IiNDOUQ4RTgiIHN0cm9rZS13aWR0aD0iMjQiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgogIDxwYXRoIGQ9Ik0yMTQgNjcySDMzNkMzNjUuODIzIDY3MiAzOTQuNDI1IDY2MC4xNSA0MTUuNTE1IDYzOS4wNzFMNDYwLjQ4NSA1OTQuMTI5QzQ4MS41NzUgNTczLjA1IDUxMC4xNzcgNTYxLjIgNTQwIDU2MS4ySDgxMCIgc3Ryb2tlPSIjQzlEOEU4IiBzdHJva2Utd2lkdGg9IjI0IiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KICA8cGF0aCBkPSJNMjI4IDUxMkg3OTIiIHN0cm9rZT0iI0RERThGMiIgc3Ryb2tlLXdpZHRoPSIxOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtZGFzaGFycmF5PSIyIDU0Ii8+CgogIDxjaXJjbGUgY3g9IjMwMiIgY3k9IjM1MiIgcj0iNDQiIGZpbGw9IiNGRkZGRkYiLz4KICA8Y2lyY2xlIGN4PSIzMDIiIGN5PSIzNTIiIHI9IjIyIiBmaWxsPSIjMTZDOEEwIi8+CiAgPGNpcmNsZSBjeD0iNzUwIiBjeT0iNDU5IiByPSIzNCIgZmlsbD0iI0ZGRkZGRiIvPgogIDxjaXJjbGUgY3g9Ijc1MCIgY3k9IjQ1OSIgcj0iMTYiIGZpbGw9IiM0RDZCRkYiLz4KICA8Y2lyY2xlIGN4PSIzMzIiIGN5PSI2NzIiIHI9IjM2IiBmaWxsPSIjRkZGRkZGIi8+CiAgPGNpcmNsZSBjeD0iMzMyIiBjeT0iNjcyIiByPSIxNSIgZmlsbD0iIzdBOEFBMCIvPgoKICA8ZyBmaWx0ZXI9InVybCgjc29mdFNoYWRvdykiPgogICAgPHBhdGggZD0iTTY3NS4wMTQgMjk5LjUzNkM2OTUuNTM4IDI3OS4wMTIgNzI4LjgxMiAyNzkuMDEyIDc0OS4zMzYgMjk5LjUzNkw3NTIuNDY0IDMwMi42NjRDNzcyLjk4OCAzMjMuMTg4IDc3Mi45ODggMzU2LjQ2MiA3NTIuNDY0IDM3Ni45ODZMNDk3LjE3NiA2MzIuMjc0QzQ4OS43MjYgNjM5LjcyNCA0ODAuMjAxIDY0NC43NjUgNDY5Ljg0NyA2NDYuNzM3TDM3NC43NTYgNjY0Ljg1QzM1Mi4wMTUgNjY5LjE4MiAzMzIuODE4IDY0OS45ODUgMzM3LjE1IDYyNy4yNDRMMzU1LjI2MyA1MzIuMTUzQzM1Ny4yMzUgNTIxLjc5OSAzNjIuMjc2IDUxMi4yNzQgMzY5LjcyNiA1MDQuODI0TDY3NS4wMTQgMjk5LjUzNloiIGZpbGw9InVybCgjcHJvYmUpIi8+CiAgICA8cGF0aCBkPSJNNjUyLjUyNCAzMjYuODUxTDcyNC4xNDkgMzk4LjQ3Nkw0ODguOTc5IDYzMy42NDZDNDgzLjQwOCA2MzkuMjE3IDQ3Ni4xOTkgNjQyLjg1OSA0NjguNDA5IDY0NC4wMzlMMzg2LjI4MSA2NTYuNDc2QzM3NS4zIDY1OC4xMzkgMzY1Ljg2MSA2NDguNyAzNjcuNTI0IDYzNy43MTlMMzc5Ljk2MSA1NTUuNTkxQzM4MS4xNDEgNTQ3LjgwMSAzODQuNzgzIDU0MC41OTIgMzkwLjM1NCA1MzUuMDIxTDY1Mi41MjQgMzI2Ljg1MVoiIGZpbGw9InVybCgjY29yZSkiIGZpbGwtb3BhY2l0eT0iMC45NiIvPgogICAgPHBhdGggZD0iTTYzNSAzNjVMNjg4LjUgNDE4LjUiIHN0cm9rZT0iI0ZGRkZGRiIgc3Ryb2tlLXdpZHRoPSIyMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2Utb3BhY2l0eT0iMC45MiIvPgogICAgPHBhdGggZD0iTTQwNSA1ODZMNDYxLjUgNjQyLjUiIHN0cm9rZT0iIzE2QzhBMCIgc3Ryb2tlLXdpZHRoPSIxNiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+CiAgICA8cGF0aCBkPSJNMzU1IDY0NkwzODEgNjIwTDQwNCA2NDNMMzY1IDY1MkMzNTguNSA2NTMuNSAzNTMuMiA2NTAuOCAzNTUgNjQ2WiIgZmlsbD0iIzE2QzhBMCIvPgogIDwvZz4KCiAgPGNpcmNsZSBjeD0iNTEyIiBjeT0iNTEyIiByPSIxMjYiIHN0cm9rZT0iI0ZGRkZGRiIgc3Ryb2tlLXdpZHRoPSIxOCIgc3Ryb2tlLW9wYWNpdHk9IjAuODIiLz4KICA8Y2lyY2xlIGN4PSI1MTIiIGN5PSI1MTIiIHI9IjkwIiBzdHJva2U9IiNEREU4RjIiIHN0cm9rZS13aWR0aD0iMTQiIHN0cm9rZS1vcGFjaXR5PSIwLjkiLz4KICA8Y2lyY2xlIGN4PSI1MTIiIGN5PSI1MTIiIHI9IjMyIiBmaWxsPSIjRkZGRkZGIi8+CiAgPGNpcmNsZSBjeD0iNTEyIiBjeT0iNTEyIiByPSIxNCIgZmlsbD0iIzE2QzhBMCIvPgo8L3N2Zz4K"


def asset_data_uri(svg_b64: str) -> str:
    return f"data:image/svg+xml;base64,{svg_b64}"


def clean_text(value: Any, default: str = "", limit: int = 160) -> str:
    if value is None:
        value = default
    text = str(value).replace("\x00", "").strip()
    text = " ".join(text.splitlines())
    if not text:
        text = default
    return text[:limit]


def clean_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def clean_int(value: Any, default: int = 0, min_value: int = 0, max_value: int = 10**18) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        number = int(float(value))
    except Exception:
        number = default
    return max(min_value, min(max_value, number))


def clean_float(value: Any, default: float = 0.0, min_value: float = 0.0, max_value: float = 10**18) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
    except Exception:
        number = default
    return round(max(min_value, min(max_value, number)), 1)


def clean_percent(value: Any) -> float:
    return clean_float(value, 0.0, 0.0, 100.0)


def clean_load(value: Any) -> list[float]:
    if not isinstance(value, list):
        return [0.0, 0.0, 0.0]
    cleaned = [clean_float(item, 0.0, 0.0, 100000.0) for item in value[:3]]
    while len(cleaned) < 3:
        cleaned.append(0.0)
    return cleaned


def sanitize_node_id(value: Any) -> str:
    node_id = clean_text(value, "unknown", 128)
    if not SAFE_NODE_ID_RE.fullmatch(node_id):
        raise ValueError("node id may only contain letters, numbers, dot, dash, underscore, colon and @")
    return node_id


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
    if not token or not signature:
        return False
    expected = sign_body(body, token)
    return hmac.compare_digest(expected, signature or "")


def sanitize_report_payload(payload: Any) -> Dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("report payload must be an object")
    node_id = sanitize_node_id(payload.get("id") or payload.get("hostname") or "unknown")
    hostname = clean_text(payload.get("hostname"), node_id, 128)
    system = clean_dict(payload.get("system"))
    cpu = clean_dict(payload.get("cpu"))
    memory = clean_dict(payload.get("memory"))
    disk = clean_dict(payload.get("disk"))
    network = clean_dict(payload.get("network"))
    return {
        "id": node_id,
        "name": clean_text(payload.get("name"), node_id, 128),
        "group": clean_text(payload.get("group"), "默认", 80),
        "hostname": hostname,
        "time": clean_text(payload.get("time"), now_iso(), 64),
        "agent_uptime": clean_text(payload.get("agent_uptime"), "", 64),
        "system": {
            "os": clean_text(system.get("os"), "", 240),
            "kernel": clean_text(system.get("kernel"), "", 120),
            "arch": clean_text(system.get("arch"), "", 80),
            "python": clean_text(system.get("python"), "", 40),
            "uptime_seconds": clean_int(system.get("uptime_seconds"), 0),
            "uptime": clean_text(system.get("uptime"), "", 80),
            "boot_time_utc": clean_text(system.get("boot_time_utc"), "", 80),
        },
        "cpu": {
            "model": clean_text(cpu.get("model"), "", 160),
            "cores": clean_int(cpu.get("cores"), 1, 1, 4096),
            "percent": clean_percent(cpu.get("percent")),
            "load": clean_load(cpu.get("load")),
        },
        "memory": {
            "total": clean_int(memory.get("total")),
            "available": clean_int(memory.get("available")),
            "used": clean_int(memory.get("used")),
            "percent": clean_percent(memory.get("percent")),
            "swap_total": clean_int(memory.get("swap_total")),
            "swap_used": clean_int(memory.get("swap_used")),
            "swap_percent": clean_percent(memory.get("swap_percent")),
        },
        "disk": {
            "path": clean_text(disk.get("path"), "/", 120),
            "total": clean_int(disk.get("total")),
            "used": clean_int(disk.get("used")),
            "free": clean_int(disk.get("free")),
            "percent": clean_percent(disk.get("percent")),
        },
        "network": {
            "rx_total": clean_int(network.get("rx_total")),
            "tx_total": clean_int(network.get("tx_total")),
            "rx_speed": clean_float(network.get("rx_speed")),
            "tx_speed": clean_float(network.get("tx_speed")),
        },
    }


def set_data_file(path: str | Path) -> None:
    global DATA_FILE
    DATA_FILE = Path(path)


def set_user_file(path: str | Path) -> None:
    global USER_FILE
    USER_FILE = Path(path)


def password_hash(password: str, salt: str | None = None) -> Tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    return salt, digest


def load_user() -> Dict[str, object] | None:
    if not USER_FILE.exists():
        return None
    try:
        payload = json.loads(USER_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) and payload.get("username") else None
    except Exception as exc:
        print(f"Failed to load user from {USER_FILE}: {exc}", file=sys.stderr, flush=True)
        return None


def save_user(username: str, password: str) -> None:
    username = username.strip()
    if not username or not password:
        raise ValueError("username and password are required")
    USER_FILE.parent.mkdir(parents=True, exist_ok=True)
    salt, digest = password_hash(password)
    payload = {"version": 1, "username": username, "salt": salt, "password_hash": digest, "created_at": now_iso()}
    tmp = USER_FILE.with_name(f".{USER_FILE.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, USER_FILE)
    try:
        USER_FILE.chmod(0o600)
    except Exception:
        pass


def verify_user(username: str, password: str) -> bool:
    user = load_user()
    if not user:
        return False
    salt = str(user.get("salt", ""))
    stored = str(user.get("password_hash", ""))
    _, digest = password_hash(password, salt)
    return hmac.compare_digest(username, str(user.get("username", ""))) and hmac.compare_digest(digest, stored)


def ensure_legacy_user(username: str = "", password: str = "") -> None:
    if USER_FILE.exists() or not username or not password:
        return
    try:
        save_user(username, password)
        print(f"Created dashboard user {username!r} from legacy auth args", flush=True)
    except Exception as exc:
        print(f"Failed to create legacy dashboard user: {exc}", file=sys.stderr, flush=True)


def create_session() -> str:
    sid = secrets.token_urlsafe(32)
    with SESSIONS_LOCK:
        SESSIONS[sid] = time.time() + SESSION_TTL
    return sid


def valid_session(sid: str) -> bool:
    if not sid:
        return False
    now = time.time()
    with SESSIONS_LOCK:
        exp = SESSIONS.get(sid, 0)
        if exp <= now:
            SESSIONS.pop(sid, None)
            return False
        SESSIONS[sid] = now + SESSION_TTL
        return True


def clear_session(sid: str) -> None:
    if not sid:
        return
    with SESSIONS_LOCK:
        SESSIONS.pop(sid, None)


def parse_form(body: bytes) -> Dict[str, str]:
    from urllib.parse import parse_qs
    parsed = parse_qs(body.decode("utf-8", "ignore"), keep_blank_values=True)
    return {k: v[-1] if v else "" for k, v in parsed.items()}


def render_auth_page(mode: str, error: str = "") -> bytes:
    is_register = mode == "register"
    title = "注册管理员" if is_register else "登录面板"
    action = "/register" if is_register else "/login"
    button = "创建账号" if is_register else "登录"
    hint = "首次访问请创建管理员账号。账号信息保存在本机，不会上传。" if is_register else "请输入安装后注册的管理员账号。"
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    body = f"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/svg+xml" href="{asset_data_uri(PROBE_ICON_SVG_B64)}">
<link rel="apple-touch-icon" href="{asset_data_uri(PROBE_ICON_SVG_B64)}">
<title>{title} · Mini Komari</title>
<style>
:root {{ color-scheme: light; --bg:#f5f7fb; --card:#fff; --text:#111827; --muted:#6b7280; --line:#e5e7eb; --accent:#4f6f9f; --danger:#dc2626; }}
*{{box-sizing:border-box}} body{{margin:0;min-height:100vh;display:grid;place-items:center;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:linear-gradient(180deg,#fff,#eef2f7);color:var(--text)}}
.card{{width:min(420px,calc(100vw - 28px));background:var(--card);border:1px solid var(--line);border-radius:24px;padding:26px;box-shadow:0 18px 50px rgba(15,23,42,.10)}} h1{{margin:0 0 8px;font-size:28px;letter-spacing:-.03em}} p{{margin:0 0 18px;color:var(--muted);line-height:1.6}} label{{display:block;color:var(--muted);font-size:13px;margin:12px 0 6px}} input{{width:100%;border:1px solid #d1d5db;border-radius:13px;padding:12px;background:#fff;color:var(--text);outline:none}} input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px #eef3fb}} button{{width:100%;margin-top:18px;border:1px solid #cbd5e1;background:linear-gradient(180deg,#fff,#eef2f7);color:#1f2937;font-weight:800;border-radius:13px;padding:12px;cursor:pointer}} .error{{background:#fee2e2;color:#991b1b;border:1px solid #fecaca;border-radius:12px;padding:10px;margin:12px 0}}
</style></head><body><form class="card" method="post" action="{action}">
<h1>{title}</h1><p>{hint}</p>{error_html}
<label>用户名</label><input name="username" autocomplete="username" required autofocus>
<label>密码</label><input name="password" type="password" autocomplete="{'new-password' if is_register else 'current-password'}" required>
<button type="submit">{button}</button>
</form></body></html>"""
    return body.encode("utf-8")


def redirect_body(location: str) -> bytes:
    return f'Redirecting to {html.escape(location)}\n'.encode()


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


def render_agent_sections(data: Dict[str, object]) -> Dict[str, object]:
    nodes = data.get("nodes", [])
    total_nodes = len(nodes) if isinstance(nodes, list) else int(data.get("count", 0) or 0)
    online_nodes = sum(1 for n in nodes if isinstance(n, dict) and bool(n.get("online")))
    offline_nodes = max(0, total_nodes - online_nodes)
    group_names = {str(n.get("group", "默认") or "默认") for n in nodes if isinstance(n, dict)}
    group_count = len(group_names)
    grouped_cards: Dict[str, list[str]] = {}
    for n in nodes:
        cpu = clean_dict(n.get("cpu"))
        mem = clean_dict(n.get("memory"))
        disk = clean_dict(n.get("disk"))
        net = clean_dict(n.get("network"))
        sysinfo = clean_dict(n.get("system"))
        cpu_percent = clean_percent(cpu.get("percent"))
        load = clean_load(cpu.get("load"))
        mem_percent = clean_percent(mem.get("percent"))
        disk_percent = clean_percent(disk.get("percent"))
        online = bool(n.get("online"))
        badge = "ONLINE" if online else "OFFLINE"
        badge_cls = "online" if online else "offline"
        group = str(n.get("group", "默认")) or "默认"
        node_id_raw = str(n.get("id", ""))
        node_id = html.escape(node_id_raw)
        node_id_arg = html.escape(json.dumps(node_id_raw), quote=True)
        grouped_cards.setdefault(group, []).append(f"""
        <section class="node" data-node-id="{node_id}">
          <div class="node-head">
            <div><h2>{html.escape(str(n.get('name','node')))}</h2><p>{html.escape(str(n.get('hostname','')))} · {html.escape(str(sysinfo.get('arch','')))} · {html.escape(group)}</p></div>
            <div class="actions"><span class="badge {badge_cls}">{badge}</span><button class="danger" onclick="deleteNode({node_id_arg})">删除</button></div>
          </div>
          <div class="metrics">
            <div><b>CPU</b><strong>{cpu_percent}%</strong>{pct_bar(cpu_percent)}<small>{clean_int(cpu.get('cores'), 1, 1, 4096)} 核 · Load {load[0]}</small></div>
            <div><b>内存</b><strong>{mem_percent}%</strong>{pct_bar(mem_percent)}<small>{human_bytes(clean_int(mem.get('used')))} / {human_bytes(clean_int(mem.get('total')))}</small></div>
            <div><b>磁盘</b><strong>{disk_percent}%</strong>{pct_bar(disk_percent)}<small>{human_bytes(clean_int(disk.get('used')))} / {human_bytes(clean_int(disk.get('total')))}</small></div>
            <div><b>网络</b><strong>↓ {human_bytes(clean_float(net.get('rx_speed')))}/s</strong><small>↑ {human_bytes(clean_float(net.get('tx_speed')))}/s</small><small>总↓ {human_bytes(clean_int(net.get('rx_total')))} · 总↑ {human_bytes(clean_int(net.get('tx_total')))}</small></div>
          </div>
          <div class="foot">系统运行 {html.escape(str(sysinfo.get('uptime','')))} · 最后上报 {n.get('last_seen_age','?')} 秒前 · {html.escape(str(sysinfo.get('os','')))}</div>
        </section>
        """)
    stats_html = f"""
  <div class="stat"><b>总节点</b><strong>{total_nodes}</strong><small>当前已登记节点</small></div>
  <div class="stat online"><b>在线</b><strong>{online_nodes}</strong><small>最近上报正常</small></div>
  <div class="stat offline"><b>离线</b><strong>{offline_nodes}</strong><small>超过离线阈值</small></div>
  <div class="stat"><b>分组</b><strong>{group_count}</strong><small>节点分组数量</small></div>"""
    group_html = "".join(
        f'<section class="group"><h2>{html.escape(group)} <span>{len(cards)} 台</span></h2>{"".join(cards)}</section>'
        for group, cards in sorted(grouped_cards.items())
    )
    empty = "" if grouped_cards else '<section class="empty">还没有 Agent 上报。先在上面生成安装命令，再去被控机执行。</section>'
    return {
        "total_nodes": total_nodes,
        "online_nodes": online_nodes,
        "offline_nodes": offline_nodes,
        "group_count": group_count,
        "summary": f"主控面板 · 节点 {total_nodes} 个 · 在线 {online_nodes} · 离线 {offline_nodes}",
        "stats_html": stats_html,
        "nodes_html": f"{group_html}{empty}",
    }


def render_html(data: Dict[str, object], refresh: int, public_url: str = "", raw_base: str = "", token_hint: str = "") -> bytes:
    sections = render_agent_sections(data)
    total_nodes = sections["total_nodes"]
    online_nodes = sections["online_nodes"]
    offline_nodes = sections["offline_nodes"]
    stats_html = str(sections["stats_html"])
    nodes_html = str(sections["nodes_html"])
    public_url = public_url.rstrip("/")
    raw_base = raw_base.rstrip("/")
    token_hint = ""
    install_url = f"{raw_base}/install.sh" if raw_base else "https://raw.githubusercontent.com/你的用户名/你的仓库/main/install.sh"
    master_url = public_url or "http://主控IP:6060"
    agent_cmd = "请填写节点名、分组和 Token，或点击“生成”创建强 Token。"
    body = f"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/svg+xml" href="{asset_data_uri(PROBE_ICON_SVG_B64)}">
<link rel="apple-touch-icon" href="{asset_data_uri(PROBE_ICON_SVG_B64)}">
<title>Mini Komari Master</title>
<style>
:root {{ color-scheme: light; --bg:#f5f7fb; --card:#ffffff; --card-soft:#f9fafc; --muted:#6b7280; --text:#111827; --line:#e5e7eb; --line-strong:#d1d5db; --good:#16a34a; --bad:#dc2626; --accent:#4f6f9f; --accent-soft:#eef3fb; --shadow:0 12px 32px rgba(15,23,42,.08); }}
*{{box-sizing:border-box}} body{{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:linear-gradient(180deg,#ffffff,#f3f6fb 42%,#eef2f7);color:var(--text)}}
.wrap{{max-width:1180px;margin:0 auto;padding:30px 16px}} .hero{{display:flex;justify-content:space-between;gap:14px;align-items:flex-end;margin-bottom:18px;padding:18px 20px;background:rgba(255,255,255,.78);border:1px solid var(--line);border-radius:24px;box-shadow:var(--shadow);backdrop-filter:blur(10px)}} h1{{margin:0;font-size:31px;letter-spacing:-.03em;display:flex;align-items:center;gap:10px}} .logo{{width:36px;height:36px;display:inline-block;flex:0 0 auto}} .sub,p,small{{color:var(--muted)}} a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
.node,.generator{{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:18px;margin:14px 0;box-shadow:var(--shadow)}} .stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0}} .stat{{background:linear-gradient(180deg,#fff,#f8fafc);border:1px solid var(--line);border-radius:20px;padding:16px;box-shadow:0 8px 22px rgba(15,23,42,.06)}} .stat b{{font-size:13px;color:var(--muted);font-weight:650}} .stat strong{{font-size:31px;margin:4px 0 0;letter-spacing:-.03em}} .stat.online strong{{color:var(--good)}} .stat.offline strong{{color:var(--bad)}} .group>h2{{margin:24px 0 9px;font-size:18px;color:#1f2937}} .group>h2 span{{color:var(--muted);font-size:13px;font-weight:500}} .node-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}} h2{{margin:0;font-size:22px;letter-spacing:-.02em}} p{{margin:4px 0 0}} .actions{{display:flex;gap:8px;align-items:center}} .badge{{padding:6px 10px;border-radius:999px;font-weight:800;font-size:12px;letter-spacing:.02em}} .online{{background:#dcfce7;color:#166534;border:1px solid #bbf7d0}} .offline{{background:#fee2e2;color:#991b1b;border:1px solid #fecaca}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}} .metrics>div{{border:1px solid var(--line);background:var(--card-soft);border-radius:16px;padding:13px}} b{{display:block;color:var(--muted);font-size:13px}} strong{{display:block;font-size:24px;margin:6px 0;letter-spacing:-.02em}} small{{display:block;font-size:12px;line-height:1.5;overflow-wrap:anywhere}} .bar{{height:8px;border-radius:999px;background:#e5e7eb;overflow:hidden;margin:9px 0}} .bar span{{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#94a3b8,var(--accent))}} .foot{{margin-top:12px;color:var(--muted);font-size:13px}} .empty{{background:#fff;border:1px dashed var(--line-strong);border-radius:18px;padding:22px;color:var(--muted)}} .refresh-note{{margin-top:4px;color:var(--muted);font-size:12px;text-align:right}}
.form{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:12px}} label{{display:block;color:var(--muted);font-size:13px;margin-bottom:5px}} input{{width:100%;border:1px solid var(--line-strong);background:#fff;color:var(--text);border-radius:12px;padding:10px 11px;outline:none;transition:border-color .15s,box-shadow .15s}} input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}} .inline-field{{display:flex;gap:7px}} .inline-field input{{min-width:0}} .inline-field button{{white-space:nowrap;padding:10px 11px}} pre{{white-space:pre-wrap;word-break:break-all;background:#f8fafc;border:1px solid var(--line);border-radius:14px;padding:13px;color:#334155}} button{{border:1px solid #cbd5e1;background:linear-gradient(180deg,#fff,#eef2f7);color:#1f2937;font-weight:800;border-radius:12px;padding:10px 13px;cursor:pointer;box-shadow:0 3px 10px rgba(15,23,42,.06)}} button:hover{{background:linear-gradient(180deg,#fff,#e5eaf2)}} button.danger{{background:#fff;color:var(--bad);border:1px solid #fecaca;padding:6px 9px;box-shadow:none}}
@media(max-width:860px){{.metrics,.stats{{grid-template-columns:1fr 1fr}}.hero{{flex-direction:column;align-items:flex-start}}.refresh-note{{text-align:left}}}} @media(max-width:520px){{.metrics,.stats,.form{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<div class="hero"><div><h1><img class="logo" src="{asset_data_uri(PROBE_MARK_SVG_B64)}" alt="" aria-hidden="true">Mini Komari Master</h1><div class="sub"><span id="summaryText">主控面板 · 节点 {total_nodes} 个 · 在线 {online_nodes} · 离线 {offline_nodes}</span> · <a href="/api/nodes">JSON API</a> · <a href="/logout">退出登录</a></div></div><div><div class="refresh-note" id="refreshNote"></div></div></div>
<section class="stats" id="statsPanel" aria-label="节点统计">
{stats_html}
</section>
<section class="generator">
  <h2>生成被控 Agent 安装命令</h2>
  <p>先确认主控地址能被被控 VPS 访问，然后填写节点名，复制命令到被控 VPS 执行。</p>
  <div class="form">
    <div><label>主控地址</label><input id="masterUrl" value="{html.escape(master_url)}"></div>
    <div><label>节点名</label><input id="nodeName" placeholder="例如：美国-FREE" value=""></div>
    <div><label>分组</label><input id="nodeGroup" placeholder="例如：US" value=""></div>
    <div><label>Token</label><div class="inline-field"><input id="token" type="password" autocomplete="off" placeholder="点击生成或手动填写" value=""><button type="button" onclick="toggleToken()" id="toggleTokenBtn">显示</button><button type="button" onclick="generateToken()">生成</button></div></div>
  </div>
  <pre id="agentCmd">{html.escape(agent_cmd)}</pre>
  <button onclick="copyCmd()">复制安装命令</button>
  <small>安装脚本地址：{html.escape(install_url)}</small>
</section>
<div id="nodesPanel">{nodes_html}</div>
<script>
const installUrl = {json.dumps(install_url)};
const refreshSeconds = {int(refresh)};
function shellQuote(value) {{
  const text = String(value || '');
  if (/^[A-Za-z0-9_./:@%+=,-]+$/.test(text)) return text;
  return "'" + text.replace(/'/g, "'\\\\''") + "'";
}}
function buildCmd() {{
  const master = document.getElementById('masterUrl').value.trim().replace(/\/$/, '');
  const node = document.getElementById('nodeName').value.trim();
  const group = document.getElementById('nodeGroup').value.trim();
  const token = document.getElementById('token').value.trim();
  if (!master || !node || !group || !token) {{
    document.getElementById('agentCmd').textContent = '请填写节点名、分组和 Token，或点击“生成”创建强 Token。';
    return '';
  }}
  const args = ['agent', master, token, node, group].map(shellQuote).join(' ');
  const cmd = `curl -fsSL ${{shellQuote(installUrl)}} | MINI_KOMARI_INTERVAL=3 bash -s -- ${{args}}`;
  document.getElementById('agentCmd').textContent = cmd;
  return cmd;
}}
function generateToken() {{
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-';
  const bytes = new Uint8Array(32);
  if (window.crypto && window.crypto.getRandomValues) {{
    window.crypto.getRandomValues(bytes);
  }} else {{
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  }}
  let token = '';
  for (const b of bytes) token += chars[b % chars.length];
  document.getElementById('token').value = token;
  buildCmd();
}}
function resetGenerator() {{
  document.getElementById('nodeName').value = '';
  document.getElementById('nodeGroup').value = '';
  document.getElementById('token').value = '';
  const tokenInput = document.getElementById('token');
  const tokenBtn = document.getElementById('toggleTokenBtn');
  tokenInput.type = 'password';
  tokenBtn.textContent = '显示';
  buildCmd();
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
  const text = buildCmd();
  if (!text) {{
    alert('请先填写节点名、分组和 Token，或点击“生成”创建强 Token。');
    return;
  }}
  try {{
    if (navigator.clipboard && window.isSecureContext) {{
      await navigator.clipboard.writeText(text);
      alert('已复制。该命令仅显示一次，请尽快安装。确认后节点名、分组和 Token 将清空。');
      resetGenerator();
      return;
    }}
  }} catch (e) {{}}
  if (fallbackCopy(text)) {{
    alert('已复制。该命令仅显示一次，请尽快安装。确认后节点名、分组和 Token 将清空。');
    resetGenerator();
  }} else {{
    prompt('自动复制失败，请手动复制下面这条命令。该命令仅显示一次，请尽快安装；关闭后表单将清空。', text);
    resetGenerator();
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
['masterUrl','nodeName','nodeGroup','token'].forEach(id => {{
  const el = document.getElementById(id);
  el.addEventListener('input', buildCmd);
}});
async function refreshAgentData() {{
  const note = document.getElementById('refreshNote');
  try {{
    const r = await fetch('/api/agent-fragment', {{cache:'no-store'}});
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    document.getElementById('summaryText').textContent = data.summary;
    document.getElementById('statsPanel').innerHTML = data.stats_html;
    document.getElementById('nodesPanel').innerHTML = data.nodes_html;
    if (note) note.textContent = 'Agent 数据已更新：' + new Date().toLocaleTimeString();
  }} catch (e) {{
    if (note) note.textContent = 'Agent 数据刷新失败，稍后重试';
  }}
}}
window.setInterval(refreshAgentData, Math.max(1, refreshSeconds) * 1000);
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

    def send_redirect(self, location: str, cookie: str = "") -> None:
        body = redirect_body(location)
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def session_id(self) -> str:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return ""
        try:
            cookie = http.cookies.SimpleCookie(raw)
            return cookie.get("mini_komari_session").value if cookie.get("mini_komari_session") else ""
        except Exception:
            return ""

    def is_authenticated(self) -> bool:
        return valid_session(self.session_id())

    def require_dashboard_auth(self) -> bool:
        if not load_user():
            self.send_redirect("/register")
            return False
        if self.is_authenticated():
            return True
        self.send_redirect("/login")
        return False

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self.send_body(200, b"OK\n", "text/plain; charset=utf-8")
            return
        if path == "/register":
            if load_user():
                self.send_redirect("/login")
            else:
                self.send_body(200, render_auth_page("register"), "text/html; charset=utf-8")
            return
        if path == "/login":
            if not load_user():
                self.send_redirect("/register")
            elif self.is_authenticated():
                self.send_redirect("/")
            else:
                self.send_body(200, render_auth_page("login"), "text/html; charset=utf-8")
            return
        if path == "/logout":
            clear_session(self.session_id())
            self.send_redirect("/login", "mini_komari_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            return
        if not self.require_dashboard_auth():
            return
        data = list_nodes()
        if path in ("/api/nodes", "/api/status"):
            self.send_body(200, json.dumps(data, ensure_ascii=False, indent=2).encode(), "application/json; charset=utf-8")
        elif path == "/api/agent-fragment":
            fragment = render_agent_sections(data)
            self.send_body(200, json.dumps(fragment, ensure_ascii=False).encode(), "application/json; charset=utf-8")
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
        if path in ("/register", "/login"):
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(min(length, 100_000)) if length > 0 else b""
            form = parse_form(body)
            username = form.get("username", "").strip()
            password = form.get("password", "")
            if path == "/register":
                if load_user():
                    self.send_redirect("/login")
                    return
                if not username or not password:
                    self.send_body(400, render_auth_page("register", "用户名和密码不能为空"), "text/html; charset=utf-8")
                    return
                save_user(username, password)
                sid = create_session()
                self.send_redirect("/", f"mini_komari_session={sid}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax")
                return
            if not verify_user(username, password):
                self.send_body(401, render_auth_page("login", "用户名或密码错误"), "text/html; charset=utf-8")
                return
            sid = create_session()
            self.send_redirect("/", f"mini_komari_session={sid}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax")
            return
        if path == "/api/delete":
            if not self.require_dashboard_auth():
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
            payload = sanitize_report_payload(json.loads(body.decode("utf-8")))
            node_id = str(payload["id"])
            payload["last_seen"] = now_iso()
            payload["last_seen_ts"] = time.time()
            with NODES_LOCK:
                NODES[node_id] = payload
            save_nodes()
            self.send_body(200, b"OK\n", "text/plain; charset=utf-8")
        except Exception as exc:
            self.send_body(400, f"bad json: {exc}\n".encode(), "text/plain; charset=utf-8")


def start_local_collector(node_id: str, name: str, group: str, interval: int, label: str) -> None:
    def update_once() -> None:
        status = collect_status(node_id, name, group)
        status["last_seen"] = now_iso()
        status["last_seen_ts"] = time.time()
        with NODES_LOCK:
            NODES[str(status["id"])] = status
        save_nodes()

    update_once()

    def updater() -> None:
        while True:
            try:
                update_once()
            except Exception as exc:
                print(f"{label} update failed: {exc}", file=sys.stderr, flush=True)
            time.sleep(max(1, interval))

    threading.Thread(target=updater, daemon=True).start()


def run_master(args: argparse.Namespace, standalone: bool = False) -> None:
    set_data_file(getattr(args, "data_file", "") or os.environ.get("MINI_KOMARI_DATA_FILE", DATA_FILE))
    set_user_file(getattr(args, "user_file", "") or os.environ.get("MINI_KOMARI_USER_FILE", USER_FILE))
    ensure_legacy_user(getattr(args, "auth_user", ""), getattr(args, "auth_pass", ""))
    load_nodes()
    if standalone:
        start_local_collector(args.node_id, args.name, getattr(args, "group", "默认"), args.interval, "standalone")
    elif getattr(args, "self_node", True):
        start_local_collector(args.self_node_id, args.self_name, args.self_group, args.self_interval, "master self-node")

    httpd = ThreadingHTTPServer((args.host, args.port), MasterHandler)
    httpd.refresh = max(1, args.refresh)
    httpd.quiet = args.quiet
    httpd.token = args.token or os.environ.get("MINI_KOMARI_TOKEN", "")
    httpd.public_url = getattr(args, "public_url", "") or os.environ.get("MINI_KOMARI_PUBLIC_URL", "")
    httpd.raw_base = getattr(args, "raw_base", "") or os.environ.get("MINI_KOMARI_RAW_BASE", "")
    httpd.token_hint = httpd.token
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
        ok = False
        try:
            payload = collect_status(args.node_id, args.name, args.group)
            code, text = post_json(report_url, payload, token)
            ok = 200 <= code < 300
            if not args.quiet:
                print(f"reported {payload['id']} -> HTTP {code} {text.strip()}", flush=True)
        except Exception as exc:
            print(f"report failed: {exc}", file=sys.stderr, flush=True)
        if args.once:
            if not ok:
                raise SystemExit(1)
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
    p_master.add_argument("--user-file", default=os.environ.get("MINI_KOMARI_USER_FILE", str(USER_FILE)), help="dashboard user JSON file")
    p_master.add_argument("--auth-user", default=os.environ.get("MINI_KOMARI_AUTH_USER", ""), help="legacy Basic Auth username migrated to web login")
    p_master.add_argument("--auth-pass", default=os.environ.get("MINI_KOMARI_AUTH_PASS", ""), help="legacy Basic Auth password migrated to web login")
    p_master.add_argument("--self-node", action=argparse.BooleanOptionalAction, default=os.environ.get("MINI_KOMARI_SELF_NODE", "1") != "0", help="collect and show the master host as a local node")
    p_master.add_argument("--self-node-id", default=os.environ.get("MINI_KOMARI_SELF_NODE_ID") or f"master-{socket.gethostname()}")
    p_master.add_argument("--self-name", default=os.environ.get("MINI_KOMARI_SELF_NAME") or f"主控-{socket.gethostname()}")
    p_master.add_argument("--self-group", default=os.environ.get("MINI_KOMARI_SELF_GROUP", "主控"))
    p_master.add_argument("--self-interval", type=int, default=int(os.environ.get("MINI_KOMARI_SELF_INTERVAL", os.environ.get("MINI_KOMARI_INTERVAL", "3"))))
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
    p_single.add_argument("--user-file", default=os.environ.get("MINI_KOMARI_USER_FILE", str(USER_FILE)))
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
