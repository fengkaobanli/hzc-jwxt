"""QQ 日历(CalDAV)同步：把最新 ics 的 VEVENT 写入 dav.qq.com 的「课表」日历。

用法:
  python /app/qq_sync.py --config /app/qq.json --ics <最新ics路径> [--wipe] [--limit N]

策略: 默认先清空「课表」日历再全量写入（幂等，无重复）。
凭证文件 qq.json: {"email": "...", "username": "...", "password": "...", "server": "https://dav.qq.com/calendar/"}
"""
import argparse
import json
import os
import re
import sys

CAL_NAME = "课表"


def split_ics(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    head_m = re.search(r"(BEGIN:VCALENDAR.*?)(?=BEGIN:VEVENT)", text, re.S)
    head = head_m.group(1) if head_m else "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
    if "PRODID" not in head:
        head = head.replace("BEGIN:VCALENDAR",
                            "BEGIN:VCALENDAR\r\nPRODID:-//hzc-jwxt//qq-sync//CN", 1)
    events = re.findall(r"BEGIN:VEVENT.*?END:VEVENT\r?\n?", text, re.S)
    out = []
    for ev in events:
        uid = re.search(r"UID:(.+)", ev)
        body = ev if ev.endswith("\n") else ev + "\n"
        out.append(((uid.group(1).strip() if uid else str(abs(hash(ev))))[:64],
                    head + body + "END:VCALENDAR\r\n"))
    return out


def _calendars(principal):
    cals = {}
    for c in principal.calendars():
        try:
            nm = c.get_display_name()
        except Exception:  # noqa: BLE001
            nm = getattr(c, "name", None)
        cals[nm or str(c.url)] = c
    return cals


_OUR_CAL_RE = re.compile(r'^课表(\s+\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2})?$')


def delete_old_calendars(principal, keep_name, log=None):
    """删除旧课表日历（只认「课表」或「课表 10月23日 15:39」这种本工具生成的命名）。

    必须在**新日历写入成功之后**调用，避免失败时把用户唯一能看的日历删掉。
    """
    log = log or (lambda *a: None)
    n = 0
    for nm, c in _calendars(principal).items():
        if nm != keep_name and _OUR_CAL_RE.match(str(nm)):
            try:
                c.delete()
                n += 1
                log(f"已删除旧课表日历「{nm}」")
            except Exception as e:  # noqa: BLE001
                log(f"删除「{nm}」失败: {e}")
    return n


def _uid_of(event):
    m = re.search(r'^UID:(.+)$', event.data.replace('\r\n', '\n'), re.M)
    return m.group(1).strip() if m else None


def sync_ics(ics_path, server, username, password, cal_name,
             wipe=True, limit=0, log=None):
    """全量同步，**先写后删**：新日历写成功并校验通过后，才删除旧日历。

    任何一步失败都不会动旧日历，避免"同步失败 -> 日历全空"。
    返回 (写入数, 日历名)。
    """
    import caldav
    log = log or (lambda *a: None)
    client = caldav.DAVClient(url=server, username=username, password=password)
    principal = client.principal()
    cals = _calendars(principal)
    created = cal_name not in cals
    if created:
        cal = principal.make_calendar(name=cal_name)
        log(f"已新建日历「{cal_name}」")
    else:
        cal = cals[cal_name]
        log(f"日历「{cal_name}」已存在")

    # QQ 新建日历有短暂生效延迟：就绪前读写会 404，轮询等待
    import time as _time
    ready = False
    for i in range(6):
        try:
            cals = _calendars(principal)
            if cal_name in cals:
                cal = cals[cal_name]
            cal.events()
            ready = True
            break
        except Exception:  # noqa: BLE001
            log(f"日历生效中，等待 10 秒 ...({i+1}/6)")
            _time.sleep(10)
    if not ready:
        if created:
            try:
                cal.delete()
            except Exception:  # noqa: BLE001
                pass
        raise RuntimeError("QQ 日历长时间未就绪，旧日历已保留，稍后重试")

    # 先写入（不删任何东西）；失败则回滚本次新建的日历
    events = split_ics(ics_path)
    if limit:
        events = events[:limit]
    new_uids = {uid for uid, _ in events}
    ok = 0
    try:
        for uid, ical in events:
            cal.save_event(ical)
            ok += 1
    except Exception as e:  # noqa: BLE001
        log(f"写入中断（已写 {ok}/{len(events)}）")
        if created:
            try:
                cal.delete()
                log("已回滚本次新建的不完整日历，旧日历保持不动")
            except Exception:  # noqa: BLE001
                pass
        raise RuntimeError(f"写入失败，旧日历已保留：{e}") from e

    back = cal.events()
    log(f"回查日历中共 {len(back)} 条")

    # 同名复用时清掉上一版遗留的多余事件（UID 不在本次集合里）。
    # 新建日历一定为空，不需要清理；QQ 会把 UID 里的 @ 编码成 %40，
    # 比较前必须解码，否则会把刚写入的事件误判为"遗留"全删掉。
    if wipe and not limit and not created:
        from urllib.parse import unquote
        stale = 0
        for ev in back:
            uid = _uid_of(ev)
            if uid and unquote(uid) not in {unquote(u) for u in new_uids}:
                try:
                    ev.delete()
                    stale += 1
                except Exception:  # noqa: BLE001
                    pass
        if stale:
            log(f"清理上一版遗留事件 {stale} 条")

    # 写入成功后才删除旧日历
    if wipe:
        delete_old_calendars(principal, cal_name, log=log)

    return ok, cal_name


def main():
    ap = argparse.ArgumentParser(description="同步课表到 QQ 日历（CalDAV）")
    ap.add_argument("--config", default="qq.json", help="凭证 JSON 路径")
    ap.add_argument("--ics", required=False, default=None, help="源 ics 文件（与 --latest 二选一）")
    ap.add_argument("--latest", action="store_true", help="自动用 --out-dir 里最新的 ics")
    ap.add_argument("--out-dir", default="输出", help="--latest 的查找目录")
    ap.add_argument("--cal-name", default=CAL_NAME, help="目标日历名")
    ap.add_argument("--wipe", action="store_true", default=True,
                    help="写入前清空日历 + 删除旧课表日历（默认开）")
    ap.add_argument("--no-wipe", dest="wipe", action="store_false")
    ap.add_argument("--limit", type=int, default=0, help="只写前 N 条（测试用）")
    a = ap.parse_args()

    ics_path = a.ics
    if not ics_path and a.latest:
        cands = sorted(__import__("glob").glob(os.path.join(a.out_dir, "*.ics")),
                       key=os.path.getmtime)
        if not cands:
            sys.exit(f"{a.out_dir}/ 里没有 ics，先抓取再同步")
        ics_path = cands[-1]
    if not ics_path:
        sys.exit("请指定 --ics 或 --latest")
    print("源文件:", os.path.basename(ics_path))

    cfg = json.load(open(a.config, encoding="utf-8"))
    ok, nm = sync_ics(ics_path,
                      cfg.get("server", "https://dav.qq.com/calendar/"),
                      cfg["username"], cfg["password"],
                      a.cal_name, wipe=a.wipe, limit=a.limit, log=print)
    print(f"完成：日历「{nm}」共 {ok} 条")


if __name__ == "__main__":
    main()
