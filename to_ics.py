"""按周次+星期+节次生成 iCalendar (.ics) 文件

节次对应的钟点由 `settings.Settings` 提供，参见 settings.py / time_settings.json。

两种输出方式（`generate_ics(..., merge=)`）：
  merge=True  —— 同一门课合并成一个 RRULE 系列（默认，139 条 → 约 24 条）
  merge=False —— 每次课一条 VEVENT（旧行为，用于对照与回退）
"""
import hashlib
from datetime import date, datetime, timedelta

from settings import Settings

WEEKDAY_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

# 默认放假日期（来自教学周历备注，可用 --holiday 追加）
DEFAULT_HOLIDAYS = [
    date(2026, 9, 25),    # 中秋节
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3),
    date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),  # 国庆节
    date(2027, 1, 1),     # 元旦
]


def _esc(s):
    """ICS 文本转义"""
    return (str(s).replace('\\', '\\\\').replace(';', r'\;')
            .replace(',', r'\,')
            .replace('\n', r'\n'))


def _fold(line):
    """按 RFC5545 折行（每行不超过 75 字节）"""
    data = line.encode('utf-8')
    if len(data) <= 75:
        return line
    out, cur = [], b''
    for ch in line:
        b = ch.encode('utf-8')
        if len(cur) + len(b) > 73:
            out.append(cur)
            cur = b''
        cur += b
    if cur:
        out.append(cur)
    return '\r\n '.join(x.decode('utf-8', 'ignore') for x in out)


def sections_to_time(sections, st=None):
    """节次列表 -> (开始时间, 结束时间)

    st 为 settings.Settings；省略时使用内置默认作息。
    """
    if not sections:
        return None
    st = st or Settings()
    return st.block_time(sections)


def sections_label(sections):
    """节次列表 -> 好看的文案：[5,6,7,8] -> '5-8'；[1,3,5] -> '1,3,5'"""
    ss = sorted(sections)
    if not ss:
        return ''
    if len(ss) == 1:
        return str(ss[0])
    if ss == list(range(ss[0], ss[-1] + 1)):      # 连续一串
        return f'{ss[0]}-{ss[-1]}'
    return ','.join(map(str, ss))


_sections_label = sections_label        # 兼容旧名


def weeks_label(weeks):
    """周次列表 -> 紧凑文案：[1,2,4,5,6] -> '1-2,4-6'"""
    ws = sorted(set(weeks))
    if not ws:
        return ''
    runs, s, p = [], ws[0], ws[0]
    for x in ws[1:]:
        if x == p + 1:
            p = x
            continue
        runs.append((s, p))
        s = p = x
    runs.append((s, p))
    return ','.join(f'{a}' if a == b else f'{a}-{b}' for a, b in runs)


def _group_key(c):
    """同一门课（同名/同星期/同节次/同教师/同地点）才能合并成系列。"""
    return (c['name'], c['day'], tuple(c['sections']),
            c.get('teacher', ''), c.get('building', ''), c.get('classroom', ''))


def _segments(weeks):
    """把周次拆成等差数列段 [(start, step, count)]，作为 RRULE 的 INTERVAL。

    步长 1 = 每周；2 = 隔周；4 = 每四周。找不到规律的单周独立成段。
    """
    ws = sorted(set(weeks))
    out, i = [], 0
    while i < len(ws):
        best = None
        for step in range(1, 9):
            j = i
            while j + 1 < len(ws) and ws[j + 1] - ws[j] == step:
                j += 1
            n = j - i + 1
            if n >= 2 and (best is None or n > best[2]):
                best = (ws[i], step, n)
        if best:
            out.append(best)
            i += best[2]
        else:
            out.append((ws[i], 0, 1))
            i += 1
    return out


def _week_usable(w, week_monday, day, holidays, skip_before):
    """该周次是否真的要上课（有校历、非假日、不早于正式上课日）。"""
    if w not in week_monday:
        return False
    d = week_monday[w] + timedelta(days=day - 1)
    if d in holidays:
        return False
    if skip_before and d < skip_before:
        return False
    return True


def _uid_hash(key, seg_index):
    raw = '|'.join(str(x) for x in key) + f'|{seg_index}'
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def build_nexio(courses, week_monday, holidays=None, semester="",
                total_weeks=20, skip_weekends=True, skip_pairs=None,
                skip_before=None):
    """生成 NexioSchedule 的 JSON 导入格式。

    与 ics 的区别：直接给 startSection/endSection，完全绕过该 App 的
    "按节次时间反推节次"逻辑——那一步匹配失败会静默跳过整门课，
    再触发 `require(courses.size == coursesData.size)` 导致整个导入失败。
    """
    holidays = set(holidays or [])
    skip_pairs = set(skip_pairs or [])
    groups = {}
    for c in courses:
        if skip_weekends and c['day'] >= 6:
            continue
        groups.setdefault(_group_key(c), []).append(c)

    out = []
    for key, items in groups.items():
        name, day, sections = key[0], key[1], sorted(key[2])
        weeks = sorted({w for c in items for w in c['weeks']})
        weeks = [w for w in weeks if (name, w) not in skip_pairs]
        weeks = [w for w in weeks
                 if _week_usable(w, week_monday, day, holidays, skip_before)]
        if not weeks:
            continue
        c0 = items[0]
        # 节次不连续时拆成多条（App 的 startSection/endSection 只能表达连续区间）
        for run in _contiguous(sections):
            out.append({
                "name": name,
                "classroom": (c0.get("building", "") + c0.get("classroom", "")).strip(),
                "teacher": c0.get("teacher", ""),
                "dayOfWeek": day,
                "startSection": run[0],
                "endSection": run[-1],
                "selectedWeeks": weeks,
            })
    return {
        "schedule_name": f"惠州城市职业学院 {semester}".strip(),
        "settings": {"total_weeks": int(total_weeks or 20)},
        "courses": out,
    }


def _contiguous(ss):
    """[1,2,4,5,6] -> [[1,2],[4,5,6]]"""
    if not ss:
        return []
    runs, cur = [], [ss[0]]
    for x in ss[1:]:
        if x == cur[-1] + 1:
            cur.append(x)
        else:
            runs.append(cur)
            cur = [x]
    runs.append(cur)
    return runs


def _ics_head(cal_name):
    return [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//hzc-jwxt//Course Table//CN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:{_esc(cal_name)}',
        'X-WR-TIMEZONE:Asia/Shanghai',
        'BEGIN:VTIMEZONE',
        'TZID:Asia/Shanghai',
        'BEGIN:STANDARD',
        'DTSTART:19700101T000000',
        'TZOFFSETFROM:+0800',
        'TZOFFSETTO:+0800',
        'TZNAME:CST',
        'END:STANDARD',
        'END:VTIMEZONE',
    ]


def _vevent(uid, dt_s, dt_e, c, desc_parts, remind):
    ev = [
        'BEGIN:VEVENT',
        f'UID:{uid}',
        f'DTSTAMP:{datetime.now():%Y%m%dT%H%M%S}',
        f'DTSTART;TZID=Asia/Shanghai:{dt_s:%Y%m%dT%H%M%S}',
        f'DTEND;TZID=Asia/Shanghai:{dt_e:%Y%m%dT%H%M%S}',
        f'SUMMARY:{_esc(c["name"])}',
        f'LOCATION:{_esc((c.get("building", "") + c.get("classroom", "")))}',
        f'DESCRIPTION:{_esc(chr(10).join(desc_parts))}',
    ]
    if remind > 0:
        ev += [
            'BEGIN:VALARM',
            f'TRIGGER:-PT{remind}M',
            'ACTION:DISPLAY',
            f'DESCRIPTION:{_esc(c["name"])}',
            'END:VALARM',
        ]
    ev.append('END:VEVENT')
    return ev


def generate_ics(courses, week_monday, holidays=None, cal_name='课程表',
                 skip_weekends=True, skip_pairs=None, skip_before=None,
                 settings=None, merge=True):
    """
    生成 ics 文本，返回 (text, VEVENT 条数)。

    courses      : parse_kb.parse_courses() 的结果
    week_monday  : {周次: 该周周一的 date}
    holidays     : 需要跳过的日期集合
    skip_pairs   : [(课程名, 周次)] 需要跳过的组合
    skip_before  : 早于此日期的课程不输出（如正式上课日之前）
    settings     : settings.Settings，控制节次时间与课前提醒
    merge        : True 同一门课合并为 RRULE 系列；False 每天一条
    """
    st = settings or Settings()
    remind = st.remind_minutes
    holidays = set(holidays or [])
    skip_pairs = set(skip_pairs or [])
    lines = _ics_head(cal_name)

    if merge:
        count = _emit_merged(lines, courses, week_monday, holidays,
                             skip_weekends, skip_pairs, skip_before, st, remind)
    else:
        count = _emit_flat(lines, courses, week_monday, holidays,
                           skip_weekends, skip_pairs, skip_before, st, remind)

    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(l) for l in lines) + '\r\n', count


def _emit_flat(lines, courses, week_monday, holidays, skip_weekends,
               skip_pairs, skip_before, st, remind):
    """每次课一条 VEVENT（旧行为，保留用于对照/回退）。"""
    count = 0
    for c in courses:
        if skip_weekends and c['day'] >= 6:
            continue
        tm = sections_to_time(c['sections'], st)
        if not tm:
            continue
        s_hm, e_hm = tm

        for w in c['weeks']:
            if w not in week_monday:
                continue
            if (c['name'], w) in skip_pairs:
                continue
            d = week_monday[w] + timedelta(days=c['day'] - 1)
            if d in holidays:
                continue
            if skip_before and d < skip_before:
                continue

            dt_s = datetime(d.year, d.month, d.day,
                            int(s_hm[:2]), int(s_hm[3:]))
            dt_e = datetime(d.year, d.month, d.day,
                            int(e_hm[:2]), int(e_hm[3:]))

            desc_parts = []
            if c.get('teacher'):
                desc_parts.append(f"教师: {c['teacher']}")
            desc_parts.append(f"节次: 第{sections_label(c['sections'])}节")
            desc_parts.append(f"时间: {s_hm}-{e_hm}")
            desc_parts.append(f"周次: {c.get('raw_weeks', '')}")

            uid = (f'hzc-{c["name"]}-{c["day"]}-{w}-'
                   f'{s_hm.replace(":", "")}@{d:%Y%m%d}')
            lines += _vevent(uid, dt_s, dt_e, c, desc_parts, remind)
            count += 1
    return count


def _emit_merged(lines, courses, week_monday, holidays, skip_weekends,
                 skip_pairs, skip_before, st, remind):
    """同一门课合并成 RRULE 系列（139 条 → 约 24 条）。"""
    groups = {}
    for c in courses:
        if skip_weekends and c['day'] >= 6:
            continue
        if not sections_to_time(c['sections'], st):
            continue
        groups.setdefault(_group_key(c), []).append(c)

    count = 0
    for key, items in groups.items():
        name, day, sections = key[0], key[1], list(key[2])
        weeks = sorted({w for c in items for w in c['weeks']})
        weeks = [w for w in weeks if (name, w) not in skip_pairs]
        # 校历缺周 / 放假 / 正式上课日前的周次直接剔除。
        # 不依赖 EXDATE：部分 CalDAV 服务端（实测 QQ 邮箱）会丢弃 EXDATE，
        # 剔除后重新分段，用多段 RRULE 表达，兼容性最好。
        weeks = [w for w in weeks if _week_usable(w, week_monday, day,
                                                  holidays, skip_before)]
        if not weeks:
            continue

        c0 = items[0]
        s_hm, e_hm = sections_to_time(c0['sections'], st)

        for si, (start, step, n) in enumerate(_segments(weeks)):
            seq = [start + i * step for i in range(n)]
            # 段内所有周次均已确认要上课（无效的已在上面剔除后重新分段）
            dtstart_date = week_monday[seq[0]] + timedelta(days=day - 1)
            total = n

            dt_s = datetime(dtstart_date.year, dtstart_date.month,
                            dtstart_date.day, int(s_hm[:2]), int(s_hm[3:]))
            dt_e = datetime(dtstart_date.year, dtstart_date.month,
                            dtstart_date.day, int(e_hm[:2]), int(e_hm[3:]))

            rule = None
            if step >= 1 and total > 1:
                rule = f'RRULE:FREQ=WEEKLY;INTERVAL={step};COUNT={total}'

            desc_parts = []
            if c0.get('teacher'):
                desc_parts.append(f"教师: {c0['teacher']}")
            desc_parts.append(f"节次: 第{sections_label(sections)}节")
            desc_parts.append(f"时间: {s_hm}-{e_hm}")
            desc_parts.append(f"周次: {weeks_label(weeks)}")

            uid = f'hzc-{_uid_hash(key, si)}@hzc-jwxt'
            ev = _vevent(uid, dt_s, dt_e, c0, desc_parts, remind)
            if rule:
                pos = next(i for i, l in enumerate(ev) if l.startswith('DESCRIPTION:'))
                ev.insert(pos, rule)
            lines += ev
            count += 1
    return count
