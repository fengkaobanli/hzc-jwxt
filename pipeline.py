#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""课表抓取 + ics 生成的公共流程

命令行（main.py）与网页界面（webapp.py）共用这里，避免登录/解析逻辑两处维护。
"""
import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta

from hzc import connect, JWXT
from calendar_xl import parse_calendar
from parse_kb import (parse_courses, parse_remarks, parse_roster, diff_roster,
                      find_semester_info)
from to_ics import generate_ics, DEFAULT_HOLIDAYS

XL_PAGE = JWXT + "/jsxsd/jxzl/jxzl_query"

CACHE_PATH = "cache_courses.json"

# 输出归档
SCHOOL_NAME = "惠州城市职业学院"
DEFAULT_OUT_DIR = "输出"

# 远程留存档（飞牛 fnOS，数据盘 /vol1）
DEFAULT_ARCHIVE_HOST = "fengkaobanli@192.168.1.1"
DEFAULT_ARCHIVE_DEST = "/vol1/1000/class"
ARCHIVE_KEYS = ("archive_enabled", "archive_host", "archive_dest")


def _noop(*_a, **_kw):
    pass


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------

def fetch(user, password, semester=None, debug=False, log=None,
          dump_prefix=None, want_calendar=True):
    """登录教务系统并抓取课表（可选教学周历）。

    返回 JSON 可序列化的 dict:
      {semester, courses, week_monday, holidays, fetched_at}
    """
    log = log or _noop

    log("CAS 统一身份认证登录 ...")
    jw = connect(user, password, debug=debug, log=log)
    log("已进入教务系统")

    log("拉取课表 ...")
    kb_html = jw.course_table(semester=semester)
    info = find_semester_info(kb_html)
    sem = semester or info.get("selected_option")
    if not sem:
        m = re.search(r'<option[^>]*value="(20\d\d-20\d\d-[12])"[^>]*selected', kb_html)
        sem = m.group(1) if m else "unknown"
    courses = parse_courses(kb_html)
    remarks = parse_remarks(kb_html)
    roster = parse_roster(kb_html)
    roster_warnings = diff_roster(courses, roster)
    log(f"学期 {sem}，解析出 {len(courses)} 条课程"
        + (f"（另有 {len(remarks)} 门仅在备注栏、无固定节次）" if remarks else ""))
    for w in roster_warnings:
        log(f"⚠ {w}")
    if dump_prefix:
        with open(dump_prefix + "_kb.html", "w", encoding="utf-8") as f:
            f.write(kb_html)

    week_monday = {}
    if want_calendar:
        log("拉取教学周历 ...")
        try:
            xl_html = jw.get(XL_PAGE, params={"xnxq01id": sem}).text
            if dump_prefix:
                with open(dump_prefix + "_xl.html", "w", encoding="utf-8") as f:
                    f.write(xl_html)
            week_monday = parse_calendar(xl_html)
            if week_monday:
                log(f"第1周周一 = {week_monday[min(week_monday)]}"
                    f"（共 {len(week_monday)} 周）")
            else:
                log("⚠ 校历解析为空")
        except Exception as e:  # noqa: BLE001
            log(f"⚠ 校历获取失败: {e}")
    else:
        log("已指定学期起始日，跳过教学周历请求")

    return {
        "semester": sem,
        "courses": courses,
        "remarks": remarks,
        "roster": roster,
        "roster_warnings": roster_warnings,
        "week_monday": {str(w): d.isoformat() for w, d in sorted(week_monday.items())},
        "holidays": [d.isoformat() for d in DEFAULT_HOLIDAYS],
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------

def save_cache(entry, path=CACHE_PATH):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
    return os.path.abspath(path)


def load_cache(path=CACHE_PATH):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# 周次 -> 日期
# --------------------------------------------------------------------------

def weeks_from_start(start, courses=None, max_week=None):
    """按「第 1 周周一 + 7×n 天」推算每周周一"""
    if isinstance(start, str):
        start = datetime.strptime(start, "%Y-%m-%d").date()
    if max_week is None:
        max_week = max((max(c["weeks"]) for c in (courses or [])), default=20)
    return {w: start + timedelta(days=7 * (w - 1)) for w in range(1, max_week + 1)}


def week_monday_of(entry, courses=None, override_start=None):
    """给定缓存条目，得到 {int: date}；override_start 优先（YYYY-MM-DD）"""
    if override_start:
        return weeks_from_start(override_start, courses)
    raw = (entry or {}).get("week_monday") or {}
    out = {}
    for k, v in raw.items():
        try:
            out[int(k)] = datetime.strptime(str(v), "%Y-%m-%d").date()
        except ValueError:
            continue
    return out


def ensure_week_monday(entry, start=None, courses=None):
    """缓存里没有周次映射时，按起始日补上，返回是否发生了补写"""
    if entry.get("week_monday"):
        return False
    start = start or None
    if not start:
        return False
    wm = weeks_from_start(start, courses or entry.get("courses"))
    entry["week_monday"] = {str(k): v.isoformat() for k, v in wm.items()}
    return True


# --------------------------------------------------------------------------
# 生成 ics
# --------------------------------------------------------------------------

def parse_holidays(seq):
    out = []
    for x in seq or []:
        try:
            out.append(datetime.strptime(str(x), "%Y-%m-%d").date())
        except ValueError:
            continue
    return out


def build_ics(courses, week_monday, settings, semester, holidays=None,
              skip_weekends=True, skip_before=None, cal_name=None):
    """生成 ics 文本，返回 (text, count)"""
    if isinstance(skip_before, str):
        skip_before = datetime.strptime(skip_before, "%Y-%m-%d").date()
    hols = parse_holidays(holidays) if holidays is not None else list(DEFAULT_HOLIDAYS)
    return generate_ics(
        courses, week_monday,
        holidays=hols,
        cal_name=cal_name or f"{SCHOOL_NAME} {semester} 课表",
        skip_weekends=skip_weekends,
        skip_before=skip_before,
        settings=settings,
    )


def write_ics(text, path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return os.path.abspath(path)


# --------------------------------------------------------------------------
# 输出归档：<输出目录>/<学校>_<学期>_课表_<YYYYMMDD>-<HHMM>.ics
# --------------------------------------------------------------------------

def output_path(semester, out_dir=None, when=None, school=SCHOOL_NAME):
    """按「学校 + 学期 + 生成时间」拼出输出路径，并建好目录

    时间精确到秒 —— 精确到分的话，同一分钟内生成两次会静默覆盖前一份存档。
    """
    d = out_dir or DEFAULT_OUT_DIR
    os.makedirs(d, exist_ok=True)
    ts = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    name = f"{school}_{semester}_课表_{ts}.ics"
    return os.path.join(d, name)


def list_exports(out_dir=None, limit=20):
    """列出输出目录里已生成的 ics（新的在前）"""
    d = out_dir or DEFAULT_OUT_DIR
    if not os.path.isdir(d):
        return []
    items = []
    for n in sorted(os.listdir(d)):
        if not n.lower().endswith(".ics"):
            continue
        fp = os.path.join(d, n)
        try:
            st = os.stat(fp)
        except OSError:
            continue
        items.append({
            "name": n,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "semester": _semester_of(n),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def _semester_of(filename):
    """从文件名里抠出学期，形如 ..._2026-2027-1_课表_..."""
    m = re.search(r'(\d{4}-\d{4}-[12])', filename or "")
    return m.group(1) if m else ""


def export_path(out_dir, name):
    """把下载请求里的文件名安全地映射到输出目录内的真实路径"""
    base = os.path.basename(name or "")
    if not base.lower().endswith(".ics"):
        return None
    fp = os.path.abspath(os.path.join(out_dir or DEFAULT_OUT_DIR, base))
    root = os.path.abspath(out_dir or DEFAULT_OUT_DIR)
    if os.path.dirname(fp) != root:      # 防目录穿越
        return None
    return fp if os.path.exists(fp) else None


# --------------------------------------------------------------------------
# 远程留存档：scp 到服务器（默认 192.168.1.1:/vol1/1000/class）
# --------------------------------------------------------------------------

def _shquote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def _run(cmd, timeout=60):
    """执行外部命令，返回 (returncode, stdout, stderr)，不抛异常"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"找不到命令 {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"超时（{timeout}s）"
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def find_scp():
    """优先用 Windows 自带 OpenSSH 的 scp，绕开 MSYS 的路径改写"""
    win = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "OpenSSH", "scp.exe")
    if os.path.exists(win):
        return win
    return shutil.which("scp")


def find_ssh():
    win = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "OpenSSH", "ssh.exe")
    if os.path.exists(win):
        return win
    return shutil.which("ssh")


def archive_settings(cfg=None, host=None, dest=None, enabled=None):
    """合并「代码默认值 / config.json / 命令行」三处来源"""
    cfg = cfg or {}
    en = cfg.get("archive_enabled", True) if enabled is None else enabled
    return {
        "enabled": bool(en),
        "host": host or cfg.get("archive_host") or DEFAULT_ARCHIVE_HOST,
        "dest": dest or cfg.get("archive_dest") or DEFAULT_ARCHIVE_DEST,
    }


def archive_files(paths, host=None, dest=None, timeout=120):
    """把本地文件 scp 到服务器归档目录。返回 (ok, message)

    失败只报错，不抛异常 —— 归档不该把「已经生成好 ics」这件事搞砸。
    """
    host = host or DEFAULT_ARCHIVE_HOST
    dest = dest or DEFAULT_ARCHIVE_DEST
    paths = [p for p in (paths or []) if p and os.path.exists(p)]
    if not paths:
        return False, "没有可归档的文件（先点「生成 ics」）"

    scp, ssh = find_scp(), find_ssh()
    if not scp or not ssh:
        return False, "找不到 scp/ssh（Windows 需启用「OpenSSH 客户端」可选功能）"

    rc, out, err = _run([ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                         host, f"mkdir -p {_shquote(dest)}"], timeout=30)
    if rc != 0:
        return False, f"连不上 {host}：{(err or out).strip()[:200]}"

    cmd = [scp, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]
    cmd += paths
    cmd.append(f"{host}:{dest}/")
    rc, out, err = _run(cmd, timeout=timeout)
    if rc != 0:
        return False, f"上传失败：{(err or out).strip()[:200]}"

    names = "、".join(os.path.basename(p) for p in paths)
    return True, f"已归档到 {host}:{dest} —— {names}"


def list_remote(host=None, dest=None, timeout=20):
    """列出服务器归档目录里的 ics（新的在前），失败返回空列表"""
    host = host or DEFAULT_ARCHIVE_HOST
    dest = dest or DEFAULT_ARCHIVE_DEST
    ssh = find_ssh()
    if not ssh:
        return []
    remote = (f"LC_ALL=C.UTF-8 ls -l --time-style=+%Y-%m-%dT%H:%M {_shquote(dest)}/*.ics"
              f" 2>/dev/null")
    rc, out, _ = _run([ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                       host, remote], timeout=timeout)
    if rc != 0:
        return []
    items = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            size = int(parts[4])
        except ValueError:
            continue
        name = " ".join(parts[6:]).split("/")[-1]
        items.append({"name": name, "size": size, "mtime": parts[5],
                      "semester": _semester_of(name)})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items
