"""解析惠州城市职业学院 强智教务系统(新版V) 课表 HTML"""
import re
from bs4 import BeautifulSoup

WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


def parse_weeks(s):
    """解析周次字符串 -> 周次列表
    '1-18' -> [1..18]; '4,8,12,16' -> [4,8,12,16]; '1,3-18' -> [1,3..18]
    """
    s = s.strip().rstrip('周').strip()
    out = []
    for part in s.split(','):
        part = part.strip()
        if not part:
            continue
        m = re.match(r'^(\d+)\s*-\s*(\d+)$', part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.extend(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            out.append(int(part))
    return sorted(set(out))


_DASHES = '-–—－~～'


def _norm_dashes(s):
    out = s
    for d in _DASHES:
        out = out.replace(d, '-')
    return out


def _parse_section_seg(seg):
    """单段节次 -> 节次列表

    强智用两种写法：
      '01-02'            -> 第 1、2 节（连排区间）
      '05-06-07-08'      -> 第 5、6、7、8 节（一次课跨 4 节，链式枚举）
    区分方式：只有一个连字符按区间展开，两个以上说明是枚举，直接取全部数字。
    """
    seg = _norm_dashes(seg).strip()
    if not seg:
        return []
    if seg.isdigit():
        return [int(seg)]
    nums = re.findall(r'\d+', seg)
    if seg.count('-') >= 2:          # 链式枚举：05-06-07-08
        return [int(n) for n in nums]
    if len(nums) == 2 and seg.count('-') == 1:
        a, b = int(nums[0]), int(nums[1])
        if a > b:
            a, b = b, a
        if 0 <= b - a <= 20:         # 区间：01-04 -> 1..4
            return list(range(a, b + 1))
        return [a, b]
    return [int(n) for n in nums]


def parse_sections(s):
    """解析节次 -> 节次列表
    '[01-02节]' -> [1,2]; '[05节]' -> [5]; '[05-06-07-08节]' -> [5,6,7,8]
    """
    out = []
    for part in re.findall(r'\[([^\]]+)\]', s):
        for seg in re.split(r'[,，;；]', part.replace('节', '')):
            out.extend(_parse_section_seg(seg))
    return sorted(set(out))


def _is_hour_info(txt):
    """判断「无 title 的 font」是不是学时信息而不是课程名

    课表里每个课程块末尾会跟一段 `(讲课:36,实践:36)`，也是无 title 的 font。
    """
    t = txt.strip()
    return t.startswith('(') or t.startswith('（')


def courses_in_div(div):
    """一个 cell 里可能纵向叠着多门课 —— 按「课程名 font」切成多块

    形如：
      形势与政策（3） / 教师 / 周次(节次) / 教学楼 / 教室 / 通知单编号 / 班级 / 备注 / (讲课:8)
      智能制造基础   / 教师 / 周次(节次) / ...
    以前把整个 div 当一个课程，后一门课会把前一门的教师/周次覆盖掉。
    """
    blocks, cur = [], None
    for f in div.find_all('font'):
        txt = f.get_text(' ', strip=True)
        if not txt:
            continue
        title = f.get('title')
        if title is None:
            if _is_hour_info(txt):
                continue                      # 学时说明，丢弃
            cur = {'name': txt, 'teacher': '', 'weeks_raw': '',
                   'building': '', 'classroom': ''}
            blocks.append(cur)
            continue
        if cur is None:                       # 还没遇到课程名，忽略
            continue
        if title == '教师':
            cur['teacher'] = txt
        elif title == '周次(节次)':
            cur['weeks_raw'] = txt
        elif title == '教学楼':
            cur['building'] = txt
        elif title == '教室':
            cur['classroom'] = txt
    return blocks


def parse_courses(html, want_kind='详细'):
    """从课表 HTML 提取课程列表。

    want_kind='详细' 时只取 div.kbcontent(含教师/教学楼等完整字段)，
    避免与 div.kbcontent1(简明版, 同一门课) 重复。
    """
    soup = BeautifulSoup(html, 'lxml')
    tb = soup.find('table', id='timetable')
    if not tb:
        return []

    rows = tb.find_all('tr')
    courses = []

    # 第一行是表头（星期），后面是节次行
    for tr in rows[1:]:
        tds = tr.find_all('td')
        for d, td in enumerate(tds):
            day = d + 1  # 1=周一 ... 7=周日
            if day > 7:
                continue
            for div in td.find_all('div', class_='kbcontent'):
                for blk in courses_in_div(div):
                    name = blk['name']
                    weeks_raw = blk['weeks_raw']
                    if not name or not weeks_raw:
                        continue

                    weeks = parse_weeks(re.split(r'[(（]', weeks_raw)[0])
                    sections = parse_sections(weeks_raw)
                    if not weeks or not sections:
                        continue

                    courses.append({
                        'name': name,
                        'teacher': blk['teacher'],
                        'day': day,
                        'weeks': weeks,
                        'sections': sections,
                        'building': blk['building'],
                        'classroom': blk['classroom'],
                        'raw_weeks': weeks_raw,
                    })

    # 去重（同一门课可能在多个模式 div 里出现；跨节次的连排课会占多行）
    uniq, seen = [], set()
    for c in courses:
        k = (c['name'], c['day'], tuple(c['weeks']), tuple(c['sections']),
             c['teacher'], c['classroom'])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def parse_remarks(html):
    """解析课表底部「备注:」行里没排进格子的课程

    这些课（劳动教育、实训周等）没有固定星期/节次，无法生成日程，
    但也不能当作不存在 —— 单独列出来提示用户。
    返回 [{'name', 'teacher', 'raw_weeks'}]
    """
    soup = BeautifulSoup(html, 'lxml')
    tb = soup.find('table', id='timetable')
    if not tb:
        return []
    out, seen = [], set()
    for tr in tb.find_all('tr'):
        th = tr.find('th')
        if not th or '备注' not in th.get_text():
            continue
        for td in tr.find_all('td'):
            text = td.get_text(' ', strip=True)
            for seg in re.split(r'[;；]', text):
                seg = seg.strip()
                if not seg:
                    continue
                m = re.search(r'(\d[\d,，\-–—]*)\s*周', seg)
                if not m:
                    continue
                rest = seg[:m.start()].strip()
                toks = rest.split()
                if not toks:
                    continue
                name, teacher = toks[0], ' '.join(toks[1:])
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append({'name': name, 'teacher': teacher,
                            'raw_weeks': m.group(0).strip()})
    return out


def _roster_table(soup, tid):
    """读一张「课程名单」表（有课表课程 / 无课表课程）

    表头：序号 | 上课班级 | 课程编号 | 课程名称 | 授课教师 | 排课人数 | 选课人数 | 课程性质 | 课程属性
    """
    tb = soup.find('table', id=tid)
    if not tb:
        return []
    rows = tb.find_all('tr')
    head = None
    for tr in rows:
        cells = [c.get_text(' ', strip=True) for c in tr.find_all(['th', 'td'])]
        if '课程名称' in cells:
            head = cells
            break
    if not head:
        return []
    idx = {name: i for i, name in enumerate(head)}
    i_name = idx.get('课程名称')
    if i_name is None:
        return []

    def cell(cells, key):
        i = idx.get(key)
        return cells[i].strip() if i is not None and i < len(cells) else ''

    out = []
    for tr in rows:
        cells = [c.get_text(' ', strip=True) for c in tr.find_all(['th', 'td'])]
        if len(cells) != len(head) or cells[i_name] in ('课程名称', ''):
            continue
        out.append({
            'name': cells[i_name],
            'code': cell(cells, '课程编号'),
            'teacher': cell(cells, '授课教师'),
            'class_name': cell(cells, '上课班级'),
            'kind': cell(cells, '课程性质'),
            'attr': cell(cells, '课程属性'),
        })
    return out


def parse_roster(html):
    """解析教务系统自己给出的课程名单（页面上两张默认隐藏的表）

    这是**权威名单**，用来交叉验证课表解析有没有漏课 —— 比肉眼数格子可靠。
    返回 {'scheduled': [...], 'unscheduled': [...]}
    """
    soup = BeautifulSoup(html, 'lxml')
    return {
        'scheduled': _roster_table(soup, 'dataTables2'),     # 有课表课程
        'unscheduled': _roster_table(soup, 'dataTables'),    # 无课表课程
    }


def diff_roster(courses, roster):
    """课表解析结果 vs 官方名单，返回告警列表

    典型用途：课程改成链式节次写法后被解析器丢掉，靠这个能立刻发现。
    """
    roster = roster or {}
    sched = roster.get('scheduled') or []
    if not sched:
        return []
    want = [r['name'] for r in sched]
    got = {c['name'] for c in courses}
    warns = []
    for name in want:
        if not any(name == g or name in g or g in name for g in got):
            warns.append(f"名单里有《{name}》但课表里没解析出来 —— 大概率漏课了")
    for g in sorted(got):
        if not any(g == w or w in g or g in w for w in want):
            warns.append(f"课表里解析出名单外的《{g}》，请确认识别是否有误")
    return warns


def find_semester_info(html):
    """尝试提取学期/学年文本。"""
    info = {}
    for pat, key in [(r'(20\d\d[-–]\s?20\d\d学年第?[一二1-2]学期)', 'semester'),
                     (r'(20\d\d[-–]20\d\d)学年第([一二1-2])学期', 'raw')]:
        m = re.search(pat, html)
        if m:
            info[key] = m.group(0)
    sel = re.search(r'<option[^>]*selected[^>]*>([^<]*20\d\d[^<]*)</option>', html)
    if sel:
        info['selected_option'] = sel.group(1).strip()
    return info


if __name__ == '__main__':
    html = open('kb.html', encoding='utf-8').read()
    cs = parse_courses(html)
    print(f'共解析出 {len(cs)} 条课程记录\n')
    for c in cs:
        print(f"{WEEKDAYS[c['day']-1]} 第{'-'.join(map(str, c['sections']))}节  {c['name']}")
        print(f"    教师={c['teacher']}  教室={c['building']}{c['classroom']}")
        print(f"    周次={c['raw_weeks']}  ({len(c['weeks'])}周)")
        print()
    rm = parse_remarks(html)
    if rm:
        print(f'备注栏未排课课程 {len(rm)} 门：')
        for r in rm:
            print(f"    {r['name']}  {r['teacher']}  {r['raw_weeks']}")
        print()
    print('学期信息:', find_semester_info(html))
