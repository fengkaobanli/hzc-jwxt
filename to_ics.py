"""按周次+星期+节次生成 iCalendar (.ics) 文件

节次对应的钟点由 `settings.Settings` 提供，参见 settings.py / time_settings.json。
"""
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


def generate_ics(courses, week_monday, holidays=None, cal_name='课程表',
                 skip_weekends=True, skip_pairs=None, skip_before=None,
                 settings=None):
    """
    生成 ics 文本。

    courses      : parse_kb.parse_courses() 的结果
    week_monday  : {周次: 该周周一的 date}
    holidays     : 需要跳过的日期集合
    skip_pairs   : [(课程名, 周次)] 需要跳过的组合
    skip_before  : 早于此日期的课程不输出（如正式上课日之前）
    settings     : settings.Settings，控制节次时间与课前提醒
    """
    st = settings or Settings()
    remind = st.remind_minutes
    holidays = set(holidays or [])
    skip_pairs = set(skip_pairs or [])
    lines = [
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

            ev = [
                'BEGIN:VEVENT',
                f'UID:hzc-{c["name"]}-{c["day"]}-{w}-{s_hm.replace(":", "")}@{d:%Y%m%d}',
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
            lines += ev
            count += 1

    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(l) for l in lines) + '\r\n', count
