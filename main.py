#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
惠州城市职业学院 课表 -> .ics 日历 一键导出

用法:
  python main.py --config config.json
  python main.py --show-times                      # 查看当前作息
  python main.py --setup-times                     # 交互式设置作息

图形界面（推荐，网页里点选时间）:
  python webapp.py

config.json 示例:
  { "user": "你的学号", "password": "你的CAS密码", "semester": "2026-2027-1" }
"""
import argparse
import json
import os
import sys
from datetime import datetime

import settings as st_mod
from settings import Settings

import pipeline
from to_ics import DEFAULT_HOLIDAYS, sections_label


def build_settings(a):
    """根据命令行参数构造时间设置"""
    if a.setup_times:
        return st_mod.interactive_setup(path=a.times or st_mod.DEFAULT_PATH)

    st = st_mod.load(a.times or st_mod.DEFAULT_PATH, silent=True)

    if a.preset:
        st = st_mod.apply_preset(a.preset, base=st)
    if a.set_sections:
        try:
            secs = st_mod.parse_section_spec(a.set_sections)
        except ValueError as e:
            sys.exit(f"--set-sections 解析失败：{e}")
        st = Settings(mode='sections', sections=secs,
                      remind_minutes=st.remind_minutes,
                      semester_start=st.semester_start, path=st.path,
                      extra=st.extra)
    if a.remind is not None:
        st.remind_minutes = max(0, a.remind)
    if a.start_date:
        try:
            st.semester_start = datetime.strptime(a.start_date, "%Y-%m-%d").date().isoformat()
        except ValueError:
            sys.exit(f"--start-date 格式不对：{a.start_date}（应为 YYYY-MM-DD）")
    if a.save_times:
        st.path = a.times or st_mod.DEFAULT_PATH
        print(f"      时间设置已保存: {st.save(st.path)}")
    return st


def main():
    ap = argparse.ArgumentParser(
        description="惠州城市职业学院 课表导出 ics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""时间设置示例:
  python main.py --show-times
  python main.py --setup-times
  python main.py --set-sections "08:00-08:45,08:50-09:35,10:00-10:45" --save-times
  python main.py --preset block --remind 10 --save-times

图形界面:
  python webapp.py
""")
    ap.add_argument("--user", help="统一身份认证账号")
    ap.add_argument("--password", help="密码")
    ap.add_argument("--config", help="JSON 配置文件（含 user/password）")
    ap.add_argument("--semester", help="学期，如 2026-2027-1（默认取系统当前学期）")
    ap.add_argument("--out", help="输出 ics 的完整路径（给了就忽略 --out-dir 与自动命名）")
    ap.add_argument("--out-dir", default=pipeline.DEFAULT_OUT_DIR,
                    help=f"输出目录（默认 {pipeline.DEFAULT_OUT_DIR}/），文件名自动带上学校与生成时间")
    ap.add_argument("--start-date",
                    help="第1周周一，YYYY-MM-DD（覆盖时间设置中的值，加 --save-times 持久化）")
    ap.add_argument("--skip-before", help="跳过此日期之前的课程，格式 YYYY-MM-DD")
    ap.add_argument("--keep-weekend", action="store_true", help="保留周末课程")
    ap.add_argument("--dump-html", help="把原始 HTML 保存到该前缀，便于排错")
    ap.add_argument("--debug", action="store_true", help="输出调试信息")

    g = ap.add_argument_group("时间设置")
    g.add_argument("--times", nargs="?", const=st_mod.DEFAULT_PATH,
                   help=f"时间设置文件路径（默认 {st_mod.DEFAULT_PATH}）")
    g.add_argument("--show-times", action="store_true", help="打印当前作息时间表后退出")
    g.add_argument("--setup-times", action="store_true", help="交互式设置作息时间（向导）")
    g.add_argument("--preset", choices=st_mod.PRESET_ORDER, help="套用内置作息预设")
    g.add_argument("--set-sections", help='逐节指定时间，如 "08:00-08:45,08:50-09:35"')
    g.add_argument("--remind", type=int, help="课前提醒分钟数（0 = 不提醒）")
    g.add_argument("--save-times", action="store_true", help="把本次的时间设置写回文件")

    g2 = ap.add_argument_group("服务器归档")
    g2.add_argument("--archive", dest="archive", action="store_true", default=None,
                    help=f"生成后 scp 到服务器归档（默认开启，目标 {pipeline.DEFAULT_ARCHIVE_DEST}）")
    g2.add_argument("--no-archive", dest="archive", action="store_false",
                    help="本次不归档到服务器")
    g2.add_argument("--archive-host", help=f"归档主机（默认 {pipeline.DEFAULT_ARCHIVE_HOST}）")
    g2.add_argument("--archive-dest", help=f"归档目录（默认 {pipeline.DEFAULT_ARCHIVE_DEST}）")
    a = ap.parse_args()

    # ---- 0) 时间设置 ------------------------------------------------------
    print("[0/4] 时间设置 ...")
    st = build_settings(a)
    for line in st.table_lines():
        print("      " + line)
    for w in st.validate():
        print(f"      ⚠ {w}")

    if a.show_times:
        return

    # ---- 凭据 -------------------------------------------------------------
    user, password, cfg = a.user, a.password, {}
    if a.config:
        with open(a.config, encoding="utf-8") as f:
            cfg = json.load(f)
        user = user or cfg.get("user")
        password = password or cfg.get("password")
        if not a.semester:
            a.semester = cfg.get("semester")
    if not (user and password):
        if a.setup_times or a.save_times:
            print("\n时间设置已完成（未提供账号密码，跳过课表导出）。")
            return
        sys.exit("缺少账号或密码：请用 --user/--password 或 --config 指定")

    # ---- 1~3) 登录 / 课表 / 周历 ------------------------------------------
    print("[1/4] 登录并抓取课表 ...")
    try:
        entry = pipeline.fetch(user, password, semester=a.semester, debug=a.debug,
                               log=lambda m: print("      " + str(m)),
                               dump_prefix=a.dump_html,
                               want_calendar=not st.semester_start)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"抓取失败：{e}")

    courses = entry["courses"]
    if not courses:
        sys.exit("没有解析出任何课程，可能是页面结构变了，加 --dump-html dump 查看")

    # 存一份缓存，网页界面直接可用，不必再登一次
    pipeline.ensure_week_monday(entry, st.semester_start, courses)
    pipeline.save_cache(entry)
    print(f"      已缓存课表数据 -> {pipeline.CACHE_PATH}")

    print("[3/4] 确定学期起始日期 ...")
    week_monday = pipeline.week_monday_of(entry, courses, st.semester_start)
    if st.semester_start:
        print(f"      来自时间设置: 第1周周一 = {st.semester_start}")
    elif week_monday:
        print(f"      来自教学周历: 第1周周一 = {week_monday[min(week_monday)]}"
              f"（共 {len(week_monday)} 周）")
    if not week_monday:
        sys.exit("无法确定学期起始日期，请用 --start-date 或时间设置文件指定")

    # ---- 4) 生成 ics ------------------------------------------------------
    print("[4/4] 生成 ics ...")
    text, n = pipeline.build_ics(courses, week_monday, st, entry["semester"],
                                 holidays=DEFAULT_HOLIDAYS,
                                 skip_weekends=not a.keep_weekend,
                                 skip_before=a.skip_before)
    out = a.out or pipeline.output_path(entry["semester"], a.out_dir)
    path = pipeline.write_ics(text, out)

    print(f"\n✅ 完成！{n} 个日程")
    print(f"   输出文件: {path}")
    if not a.out:
        total = len(pipeline.list_exports(a.out_dir, limit=10 ** 6))
        if total > 1:
            print(f"   （{a.out_dir}/ 里累计 {total} 个导出文件）")

    # ---- 5) 归档到服务器 ---------------------------------------------------
    ac = pipeline.archive_settings(cfg, a.archive_host, a.archive_dest, a.archive)
    if ac["enabled"]:
        ok, msg = pipeline.archive_files([path], ac["host"], ac["dest"])
        print(f"   {'✅' if ok else '⚠'} {msg}")
        if not ok:
            print("       （归档失败不影响上面已生成的 ics，可加 --no-archive 跳过）")
    else:
        print("   （已跳过服务器归档）")

    used = {}
    for c in courses:
        tm = st.block_time(c["sections"])
        if tm:
            used[tuple(c["sections"])] = tm
    if used:
        print("\n本表使用的节次时间:")
        for secs in sorted(used):
            s, e = used[secs]
            print(f"  第{sections_label(secs)}节 -> {s} - {e}")

    print("\n课程概览:")
    for c in courses:
        print(f"  {['周一','周二','周三','周四','周五','周六','周日'][c['day']-1]} "
              f"第{sections_label(c['sections'])}节  {c['name']}  "
              f"({c['teacher']})  {c.get('classroom','')}")

    remarks = entry.get("remarks") or []
    if remarks:
        print(f"\n⚠ 备注栏还有 {len(remarks)} 门课没排进固定节次（不会生成日程）:")
        for r in remarks:
            t = f"  {r['teacher']}" if r.get("teacher") else ""
            print(f"  · {r['name']}{t}  {r.get('raw_weeks','')}")

    # 官方名单交叉验证
    roster = entry.get("roster") or {}
    sched = roster.get("scheduled") or []
    warns = entry.get("roster_warnings") or []
    if sched:
        print(f"\n交叉验证: 教务系统名单 {len(sched)} 门有课表课程"
              + ("—— 全部解析到 ✅" if not warns else "—— 有异常 ❌"))
    for w in warns:
        print(f"  ⚠ {w}")

    print("\n提示: 想用网页点选时间，运行 `python webapp.py`。")


if __name__ == "__main__":
    main()
