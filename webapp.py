#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""课表时间设置 · 网页界面

    python webapp.py                        # http://127.0.0.1:8770
    python webapp.py --port 9000
    python webapp.py --open                 # 顺手打开浏览器
    python webapp.py --host 0.0.0.0 --token <口令>   # 局域网访问（必须给口令）

界面能做的事：
  * 网页里逐节点选每节课的起止时间（含预设、增删节次、两节连排模式）
  * 设置课前提醒分钟数与学期第 1 周周一
  * 实时预览整周课表（改时间立刻看到每条课的钟点）
  * 保存到 time_settings.json / 保存账号到 config.json
  * 从教务系统重新抓取课表、生成 .ics 并下载

只用标准库，不需要额外依赖。
"""
import argparse
import json
import os
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import settings as st_mod
from settings import Settings
import pipeline

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
CONFIG_PATH = os.path.join(ROOT, "config.json")
QQ_PATH = os.path.join(ROOT, "qq.json")
SYNC_CFG_PATH = os.path.join(ROOT, "sync_config.json")
TIMES_NAME = "time_settings.json"
MAX_BODY = 2 * 1024 * 1024


def _p(*parts):
    return os.path.join(ROOT, *parts)


def _week_info(week_monday):
    """按今天落在哪个周一区间算当前周，两头夹紧。
    周六日默认看下周（调课/预习），末周周末仍停在本周。"""
    import datetime as _dt
    try:
        items = sorted((int(k), _dt.date.fromisoformat(str(v)[:10]))
                       for k, v in (week_monday or {}).items())
    except Exception:  # noqa: BLE001
        return 1, 0
    if not items:
        return 1, 0
    today = _dt.date.today()
    cur = items[0][0]
    for w, mon in items:
        if today >= mon:
            cur = w
    if today.weekday() >= 5 and cur < items[-1][0]:
        cur += 1
    return cur, items[-1][0]


class App:
    """进程内共享状态"""

    def __init__(self, token=None, verbose=False, base=""):
        self.token = token
        self.verbose = verbose
        self.base = (base or "").rstrip("/")  # 如 /class；为空则只用根路径
        self.lock = threading.Lock()
        self.fetch_thread = None
        self.fetch_state = "idle"      # idle | running | ok | error
        self.fetch_log = []
        self.fetch_error = None
        self.last_generated = None
        self.sync_thread = None
        self.sync_state = "idle"      # idle | running | ok | error
        self.sync_log = []
        self.sync_error = None
        self.sync_result = {}
        self.cwd = os.getcwd()
        os.chdir(ROOT)                 # 让 settings/pipeline 的相对路径稳定
        th = threading.Thread(target=self._scheduler, daemon=True)
        th.start()

    # -- 时间设置 ----------------------------------------------------------
    def times_path(self):
        return _p(TIMES_NAME)

    def current_settings(self):
        return st_mod.load(self.times_path(), silent=True)

    # -- 账号 --------------------------------------------------------------
    def read_config(self):
        if not os.path.exists(CONFIG_PATH):
            return {}
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:  # noqa: BLE001
            return {}

    def public_config(self):
        c = self.read_config()
        return {
            "user": c.get("user", ""),
            "has_password": bool(c.get("password")),
            "semester": c.get("semester", ""),
            "archive_enabled": c.get("archive_enabled", ""),
            "archive_host": c.get("archive_host", ""),
            "archive_dest": c.get("archive_dest", ""),
        }

    def write_config(self, user=None, password=None, semester=None, archive=None):
        c = self.read_config()
        if user is not None:
            c["user"] = user.strip()
        if password:
            c["password"] = password
        if semester is not None:
            c["semester"] = semester.strip()
        if archive:
            if "enabled" in archive:
                c["archive_enabled"] = bool(archive["enabled"])
            if archive.get("host"):
                c["archive_host"] = archive["host"].strip()
            if archive.get("dest"):
                c["archive_dest"] = archive["dest"].strip()
        with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return os.path.abspath(CONFIG_PATH)

    # -- 归档 --------------------------------------------------------------
    def archive_cfg(self):
        return pipeline.archive_settings(self.read_config())

    # -- 课表缓存 ----------------------------------------------------------
    def cache(self):
        return pipeline.load_cache(_p(pipeline.CACHE_PATH))

    # -- QQ 同步 -----------------------------------------------------------
    def read_sync_config(self):
        dflt = {"enabled": True, "interval": "weekly", "wipe": True,
                "cal_name": "", "next_run": 0, "last_run": "",
                "last_status": "", "last_count": 0, "last_cal": ""}
        try:
            if os.path.exists(SYNC_CFG_PATH):
                with open(SYNC_CFG_PATH, encoding="utf-8") as f:
                    dflt.update(json.load(f) or {})
        except Exception:  # noqa: BLE001
            pass
        if dflt.get("interval") not in ("weekly", "daily"):
            dflt["interval"] = "weekly"
        return dflt

    def write_sync_config(self, cfg):
        with open(SYNC_CFG_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return SYNC_CFG_PATH

    def read_qq(self):
        try:
            if os.path.exists(QQ_PATH):
                with open(QQ_PATH, encoding="utf-8") as f:
                    return json.load(f) or {}
        except Exception:  # noqa: BLE001
            pass
        return {}

    def default_cal_name(self, fetched_at=None):
        """默认日历名固定为「课表」：客户端只勾选一次，之后原地更新。

        早期版本用「课表 10月23日 15:39」这种带时间的名字，每次同步都会
        产生新日历，客户端不会自动勾选，表现为"同步后什么都看不到"。
        """
        return "课表"

    def _slog(self, msg):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        with self.lock:
            self.sync_log.append(str(msg))
        # 同步日志落盘，便于事后排查（内存日志随重启丢失）
        try:
            with open(_p("sync.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass

    def _next_run_ts(self, interval):
        import datetime as _dt
        now = _dt.datetime.now()
        target = now.replace(hour=6, minute=30, second=0, microsecond=0)
        if interval == "daily":
            if target <= now:
                target += _dt.timedelta(days=1)
        else:  # weekly -> 下周一 06:30（今天已过则顺延）
            days = (7 - target.weekday()) % 7
            if days == 0 and target <= now:
                days = 7
            target += _dt.timedelta(days=days)
        return int(target.timestamp())

    def start_sync(self):
        with self.lock:
            if self.sync_state == "running":
                return False
            if self.fetch_state == "running":
                self.sync_state = "error"
                self.sync_error = "抓取进行中，稍后再试"
                return False
            self.sync_state = "running"
            self.sync_log = []
            self.sync_error = None
            self.sync_result = {}
            th = threading.Thread(target=self._do_sync, daemon=True)
            th.start()
            return True

    def _do_sync(self, auto=False):
        import qq_sync
        t0 = time.time()
        try:
            cfg = self.read_sync_config()
            ac = self.read_config()
            user, pwd = ac.get("user"), ac.get("password")
            if not (user and pwd):
                raise RuntimeError("缺少教务账号或密码，请先填写并保存")
            qq = self.read_qq()
            if not (qq.get("username") and qq.get("password")):
                raise RuntimeError("未配置 QQ 凭证（qq.json），无法同步")
            # 1) 抓取最新课表
            self._slog("抓取最新课表 ...")
            self._do_fetch(user, pwd, ac.get("semester") or None)
            if self.fetch_state != "ok":
                raise RuntimeError(f"抓取失败：{self.fetch_error or ''}")
            entry = self.cache() or {}
            # 2) 生成 ics 文件
            self._slog("生成 ics ...")
            gen = self.generate()
            # 3) 推送到 QQ 日历
            cal_name = (cfg.get("cal_name") or "").strip() or \
                self.default_cal_name(entry.get("fetched_at"))
            self._slog(f"同步到 QQ 日历「{cal_name}」 ...")
            n, used = qq_sync.sync_ics(
                gen["path"], qq.get("server", "https://dav.qq.com/calendar/"),
                qq["username"], qq["password"], cal_name,
                wipe=bool(cfg.get("wipe", True)), log=self._slog)
            with self.lock:
                self.sync_state = "ok"
                self.sync_result = {"count": n, "cal": used, "auto": auto,
                                    "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._slog(f"✅ 同步完成：{n} 条 →「{used}」（用时 {time.time()-t0:.0f} 秒）")
            cfg["last_run"] = self.sync_result["at"]
            cfg["last_status"] = "ok"
            cfg["last_count"] = n
            cfg["last_cal"] = used
            cfg["retry_count"] = 0
            self.write_sync_config(cfg)
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.sync_state = "error"
                self.sync_error = str(e)
            self._slog(f"❌ {e}")
            try:
                cfg = self.read_sync_config()
                cfg["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
                cfg["last_status"] = f"error: {e}"
                # 失败重试：30 分钟后重试，最多 3 次；用尽则回到正常周期
                rc = int(cfg.get("retry_count") or 0)
                if auto and rc < 3:
                    cfg["retry_count"] = rc + 1
                    cfg["next_run"] = int(time.time()) + 30 * 60
                    self._slog(f"↻ {30} 分钟后自动重试（第 {rc + 1}/3 次）")
                else:
                    cfg["retry_count"] = 0
                    cfg["next_run"] = self._next_run_ts(cfg.get("interval", "weekly"))
                self.write_sync_config(cfg)
            except Exception:  # noqa: BLE001
                pass

    def _scheduler(self):
        """容器内定时器：每天/每周 06:30 自动抓取+同步（替代宿主机 cron）。"""
        while True:
            try:
                time.sleep(60)
                cfg = self.read_sync_config()
                if not cfg.get("enabled"):
                    continue
                if self.fetch_state == "running" or self.sync_state == "running":
                    continue
                import datetime as _dt
                now = _dt.datetime.now().timestamp()
                nxt = cfg.get("next_run") or 0
                if not nxt:
                    nxt = self._next_run_ts(cfg.get("interval", "weekly"))
                    cfg["next_run"] = nxt
                    self.write_sync_config(cfg)
                if now >= nxt:
                    cfg["next_run"] = self._next_run_ts(cfg.get("interval", "weekly"))
                    self.write_sync_config(cfg)
                    self._slog("⏰ 自动同步触发")
                    with self.lock:
                        self.sync_state = "running"
                        self.sync_log = []
                        self.sync_error = None
                        self.sync_result = {}
                    th = threading.Thread(target=self._do_sync,
                                          kwargs={"auto": True}, daemon=True)
                    th.start()
            except Exception:  # noqa: BLE001
                continue

    # -- 抓取（后台线程） ---------------------------------------------------
    def start_fetch(self, semester=None):
        with self.lock:
            if self.fetch_state == "running":
                return False
            cfg = self.read_config()
            user, pwd = cfg.get("user"), cfg.get("password")
            if not (user and pwd):
                self.fetch_state = "error"
                self.fetch_error = "缺少账号或密码，请先在右侧填写并保存"
                self.fetch_log = [self.fetch_error]
                return False
            self.fetch_state = "running"
            self.fetch_log = []
            self.fetch_error = None
            sem = semester or cfg.get("semester") or None
            self.fetch_thread = threading.Thread(
                target=self._do_fetch, args=(user, pwd, sem), daemon=True)
            self.fetch_thread.start()
            return True

    def _log(self, msg):
        with self.lock:
            self.fetch_log.append(str(msg))

    def _do_fetch(self, user, pwd, semester):
        t0 = time.time()
        try:
            st = self.current_settings()
            self._log(time.strftime("开始抓取 %H:%M:%S"))
            # 起始日已知就不必再求周历
            entry = pipeline.fetch(user, pwd, semester=semester,
                                   log=self._log,
                                   want_calendar=not st.semester_start)
            pipeline.ensure_week_monday(entry, st.semester_start, entry.get("courses"))
            pipeline.save_cache(entry, _p(pipeline.CACHE_PATH))
            with self.lock:
                self.fetch_state = "ok"
            self._log(f"✅ 已缓存 {len(entry['courses'])} 条课程（用时 {time.time()-t0:.0f} 秒）")
            try:
                cnt = self.refresh_fixed()
                self._log(f"固定下载链接已更新（{cnt} 个日程）")
            except Exception as e:  # noqa: BLE001
                self._log(f"⚠ 固定链接更新失败：{e}")
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.fetch_state = "error"
                self.fetch_error = str(e)
            self._log(f"❌ {e}")
            if self.verbose:
                self._log(traceback.format_exc())

    # -- 生成 ics ----------------------------------------------------------
    def out_dir(self):
        return _p(pipeline.DEFAULT_OUT_DIR)

    def refresh_fixed(self):
        """只刷新两份固定下载文件（不写时间戳存档），抓取后自动调用。"""
        entry = self.cache()
        if not entry:
            return 0
        st = self.current_settings()
        courses = entry["courses"]
        wm = pipeline.week_monday_of(entry, courses, st.semester_start)
        if not wm:
            return 0
        semester = entry.get("semester", "unknown")
        kw = dict(holidays=entry.get("holidays"))
        count = 0
        for fname, mg in ((pipeline.LATEST_MERGED, True),
                          (pipeline.LATEST_FLAT, False)):
            text, count = pipeline.build_ics(courses, wm, st, semester,
                                             merge=mg, **kw)
            pipeline.write_ics(text, os.path.join(self.out_dir(), fname))
        self._write_nexio(entry, st, wm, semester, **kw)
        return count

    def _write_nexio(self, entry, st, wm, semester, **kw):
        """NexioSchedule 的 JSON 导入文件（固定名，每次覆盖）。"""
        data = pipeline.build_nexio_json(
            entry["courses"], wm, st, semester,
            holidays=kw.get("holidays"),
            skip_weekends=kw.get("skip_weekends", True),
            skip_before=kw.get("skip_before"))
        path = os.path.join(self.out_dir(), pipeline.LATEST_NEXIO)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return path

    def generate(self, skip_weekends=True, skip_before=None):
        entry = self.cache()
        if not entry:
            raise RuntimeError("还没有课表数据，请先点「从教务系统获取课表」")
        st = self.current_settings()
        courses = entry["courses"]
        wm = pipeline.week_monday_of(entry, courses, st.semester_start)
        if not wm:
            raise RuntimeError("无法确定学期起始日期，请在左侧填写「第 1 周周一」")
        semester = entry.get("semester", "unknown")
        kw = dict(holidays=entry.get("holidays"),
                  skip_weekends=skip_weekends, skip_before=skip_before)

        # 存档：合并模式（文件名带时间）
        text, n = pipeline.build_ics(courses, wm, st, semester, merge=True, **kw)
        out = pipeline.output_path(semester, self.out_dir())
        pipeline.write_ics(text, out)

        # 固定下载链接：两种模式各一份，每次覆盖
        for fname, mg in ((pipeline.LATEST_MERGED, True),
                          (pipeline.LATEST_FLAT, False)):
            t, _ = pipeline.build_ics(courses, wm, st, semester, merge=mg, **kw)
            pipeline.write_ics(t, os.path.join(self.out_dir(), fname))

        self._write_nexio(entry, st, wm, semester, **kw)
        with self.lock:
            self.last_generated = out

        arch = {"attempted": False, "ok": None, "message": "未开启服务器归档"}
        ac = self.archive_cfg()
        if ac["enabled"]:
            ok, msg = pipeline.archive_files([out], ac["host"], ac["dest"])
            arch = {"attempted": True, "ok": ok, "message": msg,
                    "host": ac["host"], "dest": ac["dest"]}

        return {"path": out, "name": os.path.basename(out), "count": n,
                "semester": semester, "dir": os.path.abspath(self.out_dir()),
                "archive": arch}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "hzc-jwxt-web"
    protocol_version = "HTTP/1.1"

    # -- 工具 --------------------------------------------------------------
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": str(msg)}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError("请求体过大")
        raw = self.rfile.read(n)
        return json.loads(raw.decode("utf-8") or "{}")

    def _authed(self, query):
        tok = self.server.app.token
        if not tok:
            return True
        got = (self.headers.get("X-Token")
               or (query.get("token") or [None])[0])
        return got == tok

    def log_message(self, fmt, *args):
        if self.server.app.verbose:
            sys.stderr.write("[web] " + (fmt % args) + "\n")

    ADMIN_PREFIX = "/fengkaobanli"

    def _strip_base(self, path):
        """去掉可选的 base 前缀（如 /class/api/state -> /api/state，
        /fengkaobanli/api/state -> /api/state）。
        根路径访问永远有效，老书签不断。"""
        for b in (self.server.app.base, self.ADMIN_PREFIX):
            if b and (path == b or path.startswith(b + "/")):
                return path[len(b):] or "/"
        return path

    def _is_guest_path(self, raw_path):
        """只有站点根路径是访客页（纯课表预览），其余走管理页。"""
        return raw_path == "/"

    # -- 路由 --------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if self._is_guest_path(u.path):
            return self._page(guest=True)
        path = self._strip_base(u.path)

        if path in ("/", "/index.html", self.ADMIN_PREFIX):
            return self._page()
        if path == "/favicon.ico":
            return self._send(204)
        if not path.startswith("/api/") and path != "/download/ics":
            return self._err("not found", 404)
        if not self._authed(q):
            return self._err("口令不正确", 401)

        try:
            if path == "/api/state":
                return self._state()
            if path == "/api/fetch/status":
                return self._fetch_status()
            if path == "/api/archive":
                return self._archive_list()
            if path == "/api/qq-config":
                return self._qq_config()
            if path == "/api/qq-sync/status":
                return self._qq_sync_status()
            if path == "/api/guest":
                return self._guest_state()
            if path == "/download/ics":
                return self._download_ics(q)
        except Exception as e:  # noqa: BLE001
            return self._err(e, 500)
        return self._err("not found", 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._err("口令不正确", 401)
        try:
            body = self._body()
        except Exception as e:  # noqa: BLE001
            return self._err(f"请求体解析失败: {e}")

        try:
            path = self._strip_base(u.path)
            if path == "/api/settings":
                return self._save_settings(body)
            if path == "/api/config":
                return self._save_config(body)
            if path == "/api/fetch":
                return self._start_fetch(body)
            if path == "/api/generate":
                return self._generate(body)
            if path == "/api/archive":
                return self._archive_now(body)
            if path == "/api/qq-config":
                return self._qq_config_save(body)
            if path == "/api/qq-sync":
                return self._qq_sync_start(body)
            if path == "/api/guest-fetch":
                return self._guest_fetch_start(body)
            if path == "/api/reset-times":
                return self._reset_times()
        except Exception as e:  # noqa: BLE001
            return self._err(e, 500)
        return self._err("not found", 404)

    do_HEAD = do_GET

    # -- 页面 --------------------------------------------------------------
    def _page(self, guest=False):
        fp = os.path.join(WEB_DIR, "index.html")
        if not os.path.exists(fp):
            return self._err("缺少 web/index.html", 500)
        with open(fp, encoding="utf-8") as f:
            html = f.read()
        html = html.replace("__TOKEN__", self.server.app.token or "")
        html = html.replace("__BASE__", self.server.app.base or "")
        html = html.replace("__GUEST__", "1" if guest else "")
        if guest:
            # 访客页剥掉管理区 markup，只留课表预览
            parts = html.split("<!--ADMIN-START-->")
            kept = [parts[0]]
            for p in parts[1:]:
                _, _, rest = p.partition("<!--ADMIN-END-->")
                kept.append(rest)
            html = "".join(kept)
        self._send(200, html, "text/html; charset=utf-8")

    def _guest_state(self):
        """访客只读接口：课表预览所需数据，不含账号/归档/导出记录。"""
        app = self.server.app
        st = app.current_settings()
        entry = app.cache() or {}
        cw, tw = _week_info(entry.get("week_monday"))
        cfg = app.read_sync_config()
        cd = max(0, int(600 - (time.time() - (cfg.get("guest_last_fetch") or 0))))
        return self._json({
            "ok": True,
            "settings": st.to_dict(),
            "courses": entry.get("courses") or [],
            "weekMonday": entry.get("week_monday") or {},
            "currentWeek": cw,
            "totalWeeks": tw,
            "semester": entry.get("semester"),
            "fetchedAt": entry.get("fetched_at"),
            "rosterCount": len(((entry.get("roster") or {}).get("scheduled")) or []),
            "rosterWarnings": entry.get("roster_warnings") or [],
            "guestCooldownSec": cd,
        })

    def _guest_fetch_start(self, body):
        """访客同步：复用抓取管线，全局 10 分钟冷却（服务端强制）。"""
        app = self.server.app
        cfg = app.read_sync_config()
        now = time.time()
        wait = int(600 - (now - (cfg.get("guest_last_fetch") or 0)))
        if wait > 0:
            return self._err("同步冷却中，还需等待约 "
                             + (f"{wait // 60} 分钟" if wait >= 60 else f"{wait} 秒"))
        if app.fetch_state == "running":
            return self._err("正在同步中，请稍候")
        if not app.start_fetch():
            return self._err(app.fetch_error or "启动同步失败")
        cfg["guest_last_fetch"] = now
        app.write_sync_config(cfg)
        return self._json({"ok": True, "started": True,
                           "state": app.fetch_state,
                           "log": list(app.fetch_log),
                           "error": app.fetch_error})

    # -- API ---------------------------------------------------------------
    def _state(self):
        app = self.server.app
        st = app.current_settings()
        entry = app.cache() or {}
        return self._json({
            "ok": True,
            "settings": st.to_dict(),
            "modes": ["sections", "blocks"],
            "presets": {k: {"label": v["label"], "mode": v["mode"],
                            "sections": v.get("sections"),
                            "blocks": v.get("blocks")}
                        for k, v in st_mod.PRESETS.items()},
            "presetOrder": st_mod.PRESET_ORDER,
            "warnings": st.validate(),
            "timesFile": os.path.abspath(app.times_path()),
            "config": app.public_config(),
            "archiveConfig": app.archive_cfg(),
            "semester": entry.get("semester"),
            "fetchedAt": entry.get("fetched_at"),
            "courses": entry.get("courses") or [],
            "remarks": entry.get("remarks") or [],
            "rosterCount": len(((entry.get("roster") or {}).get("scheduled")) or []),
            "rosterWarnings": entry.get("roster_warnings") or [],
            "weekMonday": entry.get("week_monday") or {},
            "currentWeek": _week_info(entry.get("week_monday"))[0],
            "totalWeeks": _week_info(entry.get("week_monday"))[1],
            "lastGenerated": app.last_generated,
            "exports": pipeline.list_exports(app.out_dir()),
            "outDir": os.path.abspath(app.out_dir()),
            "fetch": {"state": app.fetch_state, "log": list(app.fetch_log),
                      "error": app.fetch_error},
        })

    def _save_settings(self, body):
        app = self.server.app
        raw = body.get("settings") if "settings" in body else body
        try:
            st = Settings.from_dict(raw, path=app.times_path())
        except ValueError as e:
            return self._err(f"时间格式有误：{e}")
        st.extra = st_mod.load(app.times_path(), silent=True).extra  # 保留注释
        st.path = app.times_path()
        warns = st.validate()
        path = st.save()
        return self._json({"ok": True, "path": path, "warnings": warns,
                           "settings": st.to_dict()})

    def _reset_times(self):
        app = self.server.app
        st = Settings(path=app.times_path())
        st.extra = st_mod.load(app.times_path(), silent=True).extra  # 保留文件里的注释
        st.save()
        return self._json({"ok": True, "settings": st.to_dict(),
                           "warnings": st.validate()})

    def _save_config(self, body):
        app = self.server.app
        path = app.write_config(user=body.get("user"),
                               password=body.get("password"),
                               semester=body.get("semester"),
                               archive=body.get("archive"))
        return self._json({"ok": True, "path": path,
                           "config": app.public_config(),
                           "archiveConfig": app.archive_cfg()})

    def _start_fetch(self, body):
        app = self.server.app
        started = app.start_fetch(semester=(body.get("semester") or "").strip() or None)
        return self._json({"ok": True, "started": started,
                           "state": app.fetch_state,
                           "log": list(app.fetch_log),
                           "error": app.fetch_error})

    def _fetch_status(self):
        app = self.server.app
        entry = app.cache() or {}
        return self._json({"ok": True, "state": app.fetch_state,
                           "log": list(app.fetch_log), "error": app.fetch_error,
                           "semester": entry.get("semester"),
                           "fetchedAt": entry.get("fetched_at"),
                           "courses": entry.get("courses") or [],
                           "remarks": entry.get("remarks") or [],
                           "rosterCount": len(((entry.get("roster") or {}).get("scheduled")) or []),
                           "rosterWarnings": entry.get("roster_warnings") or [],
                           "weekMonday": entry.get("week_monday") or {},
                           "currentWeek": _week_info(entry.get("week_monday"))[0],
                           "totalWeeks": _week_info(entry.get("week_monday"))[1]})

    def _qq_config(self):
        app = self.server.app
        cfg = app.read_sync_config()
        entry = app.cache() or {}
        import datetime as _dt
        nxt = ""
        try:
            if cfg.get("next_run"):
                nxt = _dt.datetime.fromtimestamp(cfg["next_run"]).strftime("%m月%d日 %H:%M")
        except Exception:  # noqa: BLE001
            pass
        return self._json({"ok": True, "config": cfg,
                           "hasQQ": bool(app.read_qq().get("username")),
                           "nextRun": nxt,
                           "defaultCalName": app.default_cal_name(entry.get("fetched_at"))})

    def _qq_config_save(self, body):
        app = self.server.app
        cfg = app.read_sync_config()
        if "enabled" in body:
            cfg["enabled"] = bool(body["enabled"])
        if body.get("interval") in ("weekly", "daily"):
            cfg["interval"] = body["interval"]
        if "wipe" in body:
            cfg["wipe"] = bool(body["wipe"])
        if "cal_name" in body:
            cfg["cal_name"] = (body["cal_name"] or "").strip()
        if cfg.get("enabled") and not cfg.get("next_run"):
            cfg["next_run"] = app._next_run_ts(cfg.get("interval", "weekly"))
        app.write_sync_config(cfg)
        return self._json({"ok": True, "config": cfg})

    def _qq_sync_start(self, body):
        app = self.server.app
        started = app.start_sync()
        return self._json({"ok": True, "started": started,
                           "state": app.sync_state,
                           "log": list(app.sync_log),
                           "error": app.sync_error})

    def _qq_sync_status(self):
        app = self.server.app
        return self._json({"ok": True, "state": app.sync_state,
                           "log": list(app.sync_log), "error": app.sync_error,
                           "result": app.sync_result})

    def _generate(self, body):
        app = self.server.app
        try:
            r = app.generate(skip_weekends=body.get("skipWeekends", True),
                             skip_before=body.get("skipBefore") or None)
        except Exception as e:  # noqa: BLE001
            return self._err(e)
        return self._json({"ok": True, **r})

    def _archive_list(self):
        app = self.server.app
        ac = app.archive_cfg()
        items = pipeline.list_remote(ac["host"], ac["dest"]) if ac["enabled"] else []
        return self._json({"ok": True, "config": ac, "items": items,
                           "local": pipeline.list_exports(app.out_dir())})

    def _archive_now(self, body):
        app = self.server.app
        ac = app.archive_cfg()
        if not ac["enabled"]:
            return self._err("服务器归档未开启，请在下方打开开关并保存")
        want = (body.get("file") or "").strip()
        if want:
            fp = pipeline.export_path(app.out_dir(), want)
            if not fp:
                return self._err("本地找不到该文件")
            paths = [fp]
        else:
            # 默认把本地输出目录里、服务器上还没有的都补上去
            local = pipeline.list_exports(app.out_dir(), limit=10 ** 6)
            remote = {x["name"] for x in pipeline.list_remote(ac["host"], ac["dest"])}
            paths = [pipeline.export_path(app.out_dir(), f["name"]) for f in local
                     if f["name"] not in remote]
            paths = [p for p in paths if p]
        if not paths:
            return self._json({"ok": True, "message": "本地没有需要补传的文件",
                               "items": pipeline.list_remote(ac["host"], ac["dest"])})
        ok, msg = pipeline.archive_files(paths, ac["host"], ac["dest"])
        return self._json({"ok": ok, "message": msg, "uploaded": len(paths),
                           "items": pipeline.list_remote(ac["host"], ac["dest"])})

    def _download_ics(self, query=None):
        app = self.server.app
        mode = ((query or {}).get("mode") or [None])[0]
        if mode in ("merged", "flat"):
            # 固定链接：始终返回最新生成的那一份
            fname = (pipeline.LATEST_MERGED if mode == "merged"
                     else pipeline.LATEST_FLAT)
            fp = pipeline.export_path(app.out_dir(), fname)
            if not fp:
                return self._err("还没有生成 ics", 404)
            return self._send_ics(
                fp, "惠州城市职业学院_课表_合并.ics" if mode == "merged"
                else "惠州城市职业学院_课表_逐天.ics",
                "hzc-class-merged.ics" if mode == "merged"
                else "hzc-class-flat.ics")
        if mode == "nexio":
            fp = pipeline.export_path(app.out_dir(), pipeline.LATEST_NEXIO)
            if not fp:
                return self._err("还没有生成课表", 404)
            return self._send_download(
                fp, "application/json; charset=utf-8",
                "惠州城市职业学院_课表_Nexio.json", "hzc-class-nexio.json")
        want = (query or {}).get("file", [None])[0]
        if want:
            fp = pipeline.export_path(app.out_dir(), want)
            if not fp:
                return self._err("找不到该文件", 404)
        else:
            fp = app.last_generated
            if not fp or not os.path.exists(fp):
                # 内存记录丢失时（如重启后、CLI/cron 生成）回落到磁盘最新导出，
                # 保证订阅链接长期有效
                ex = pipeline.list_exports(app.out_dir())
                fp = pipeline.export_path(app.out_dir(), ex[0]["name"]) if ex else None
            if not fp or not os.path.exists(fp):
                return self._err("还没有生成 ics", 404)
        return self._send_ics(fp)

    def _send_ics(self, fp, download_name=None, ascii_name="hzc-class.ics"):
        return self._send_download(fp, "text/calendar; charset=utf-8",
                                   download_name, ascii_name)

    def _send_download(self, fp, mime, download_name=None,
                       ascii_name="download.bin"):
        with open(fp, "rb") as f:
            data = f.read()
        name = download_name or os.path.basename(fp)
        from urllib.parse import quote
        # filename= 给不认 RFC5987 的下载器兜底（Android/国产浏览器常见），
        # 否则可能存成没有后缀的文件，App 的文件选择器直接看不到
        disp = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'
        self._send(200, data, mime, {"Content-Disposition": disp})
        return None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, app):
        self.app = app
        super().__init__(addr, handler)


def main():
    ap = argparse.ArgumentParser(description="课表时间设置 · 网页界面")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认 127.0.0.1；局域网用 0.0.0.0，需配合 --token）")
    ap.add_argument("--port", type=int, default=8770, help="端口（默认 8770）")
    ap.add_argument("--token", help="访问口令（非本机访问时强烈建议设置）")
    ap.add_argument("--base-path", default="",
                    help="URL 前缀，如 /class（代理子路径时用；根路径始终有效）")
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--verbose", action="store_true", help="打印访问日志与异常堆栈")
    a = ap.parse_args()

    if a.host not in ("127.0.0.1", "localhost", "::1") and not a.token:
        print("⚠ 正在监听非本机地址，却没有设置 --token。")
        print("  同一网络下任何人都能读取你的课表并触发登录，建议加上 --token <口令>。")
        print("  3 秒后继续，按 Ctrl+C 取消 ...")
        import time
        time.sleep(3)

    app = App(token=a.token, verbose=a.verbose, base=a.base_path)
    srv = Server((a.host, a.port), Handler, app)
    url = f"http://127.0.0.1:{a.port}{a.base_path.rstrip('/')}/"
    if a.token:
        url += f"?token={a.token}"
    print(f"课表时间设置已启动: {url}")
    print(f"时间设置文件: {app.times_path()}")
    print("按 Ctrl+C 停止\n")
    if a.open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
