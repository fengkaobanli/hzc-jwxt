#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作息时间设置模块

可配置三项时间相关内容：

  1. 节次时间表 —— 每节课的起止时间
     支持两种建模方式：
       sections：逐节定义（第1节 08:00-08:45、第2节 08:50-09:35 …）
       blocks  ：两节连排（1-2节 08:00-09:40、3-4节 10:00-11:40 …）
  2. 课前提醒分钟数（0 = 不提醒）
  3. 学期第 1 周周一日期（留空则自动从教学周历获取）

配置文件默认 `time_settings.json`：

{
  "mode": "sections",
  "sections": [["08:00","08:45"], ["08:50","09:35"], ...],
  "blocks":   [["08:00","09:40"], ["10:00","11:40"], ...],
  "remind_minutes": 15,
  "semester_start": "2026-08-31"
}

`mode` 省略时自动推断：有 sections 就用 sections，否则用 blocks。
"""
import json
import os
import re
import sys

DEFAULT_PATH = 'time_settings.json'

# --------------------------------------------------------------------------
# 内置预设
# --------------------------------------------------------------------------

# 每节 45 分钟
PRESET_45 = [
    ('08:00', '08:45'),   # 第 1 节
    ('08:50', '09:35'),   # 第 2 节
    ('10:00', '10:45'),   # 第 3 节
    ('10:50', '11:35'),   # 第 4 节
    ('14:00', '14:45'),   # 第 5 节
    ('14:50', '15:35'),   # 第 6 节
    ('16:00', '16:45'),   # 第 7 节
    ('16:50', '17:35'),   # 第 8 节
    ('19:00', '19:45'),   # 第 9 节
    ('19:50', '20:35'),   # 第 10 节
    ('20:45', '21:30'),   # 第 11 节
    ('21:35', '22:20'),   # 第 12 节
]

# 每节 40 分钟
PRESET_40 = [
    ('08:00', '08:40'),
    ('08:45', '09:25'),
    ('09:40', '10:20'),
    ('10:25', '11:05'),
    ('14:00', '14:40'),
    ('14:45', '15:25'),
    ('15:40', '16:20'),
    ('16:25', '17:05'),
    ('19:00', '19:40'),
    ('19:45', '20:25'),
    ('20:35', '21:15'),
    ('21:20', '22:00'),
]

# 惠州城市职业学院「大专上课时间表」（学校官方作息，本工具默认值）
PRESET_HZC = [
    ('09:00', '09:40'),   # 第 1 节
    ('09:45', '10:25'),   # 第 2 节
    ('10:45', '11:25'),   # 第 3 节（10:25-10:45 为课间，不入表）
    ('11:30', '12:10'),   # 第 4 节
    ('14:35', '15:15'),   # 第 5 节
    ('15:20', '16:00'),   # 第 6 节
    ('16:05', '16:45'),   # 第 7 节
    ('16:50', '17:30'),   # 第 8 节
    ('18:00', '18:40'),   # 第 9 节
    ('18:45', '19:20'),   # 第 10 节（仅 35 分钟，按原表照抄）
    ('19:30', '20:10'),   # 第 11 节
    ('20:15', '20:50'),   # 第 12 节
]

# 两节连排（本工具最初的默认值）
PRESET_BLOCKS = [
    ('08:00', '09:40'),   # 第 1-2 节
    ('10:00', '11:40'),   # 第 3-4 节
    ('14:00', '15:40'),   # 第 5-6 节
    ('16:00', '17:40'),   # 第 7-8 节
    ('19:00', '20:40'),   # 第 9-10 节
    ('20:50', '22:30'),   # 第 11-12 节
]

PRESETS = {
    'hzc':       {'label': '惠州城市职业学院（默认时间模板）', 'mode': 'sections', 'sections': PRESET_HZC},
    'default':   {'label': '每节 45 分钟',          'mode': 'sections', 'sections': PRESET_45},
    'compact40': {'label': '每节 40 分钟',          'mode': 'sections', 'sections': PRESET_40},
    'block':     {'label': '两节连排（原默认值）',   'mode': 'blocks',   'blocks': PRESET_BLOCKS},
}

PRESET_ORDER = ['hzc', 'default', 'compact40', 'block']

DEFAULT_PRESET = 'hzc'    # 缺省套用的预设：惠州城市职业学院官方作息

DEFAULT_REMIND_MINUTES = 15
DEFAULT_BREAK = 5          # 自动补齐后一节的间隔（分钟）
DEFAULT_DURATION = 45      # 自动补齐一节的时长（分钟）

_TIME_RE = re.compile(r'^([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)$')
_ITEM_SEP = re.compile(r'[,;，；、]')
_RANGE_SEP = re.compile(r'\s*[-~至～—－]\s*')
# 把 ["08:00",\n "08:45"] 压回一行，方便手工编辑配置文件
_PAIR_RE = re.compile(
    r'\[\s*\n\s*("(?:[^"\\]|\\.)*")\s*,\s*\n\s*("(?:[^"\\]|\\.)*")\s*\n\s*\]')


def dump_readable(d):
    """json.dumps + 把时间对压成一行"""
    txt = json.dumps(d, ensure_ascii=False, indent=2)
    return _PAIR_RE.sub(lambda m: f'[{m.group(1)}, {m.group(2)}]', txt) + '\n'


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def norm_hm(s):
    """把 8:00 / 08：00 归一化为 08:00，非法则抛 ValueError"""
    m = _TIME_RE.match(str(s).strip())
    if not m:
        raise ValueError(f'时间格式不对：{s!r}（应为 HH:MM，例如 08:00）')
    return f'{int(m.group(1)):02d}:{m.group(2)}'


def _to_min(hm):
    h, m = hm.split(':')
    return int(h) * 60 + int(m)


def _to_hm(mins):
    mins = max(0, min(23 * 60 + 59, int(mins)))
    return f'{mins // 60:02d}:{mins % 60:02d}'


def add_minutes(hm, delta):
    return _to_hm(_to_min(hm) + delta)


def _pair(item):
    """[开始, 结束] -> (开始, 结束)，并做归一化"""
    if not isinstance(item, (list, tuple)) or len(item) != 2:
        raise ValueError(f'每一项应为 ["开始","结束"]，收到：{item!r}')
    return norm_hm(item[0]), norm_hm(item[1])


def parse_section_spec(spec):
    """解析命令行规格 "08:00-08:45,08:50-09:35" -> [(start, end), ...]

    分隔符支持半角/全角：, ， ; ； 、
    区间连接符支持：- ~ 至 ～ — －
    """
    out = []
    for chunk in _ITEM_SEP.split(spec or ''):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = _RANGE_SEP.split(chunk)
        if len(parts) != 2:
            raise ValueError(f'无法解析：{chunk!r}（应为 08:00-08:45）')
        out.append((norm_hm(parts[0]), norm_hm(parts[1])))
    if not out:
        raise ValueError('没有解析出任何节次时间')
    return out


def format_pair(pair):
    return f'{pair[0]} - {pair[1]}'


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

class Settings:
    """时间设置"""

    _KNOWN = ('mode', 'sections', 'blocks', 'remind_minutes', 'semester_start')

    def __init__(self, mode='sections', sections=None, blocks=None,
                 remind_minutes=DEFAULT_REMIND_MINUTES, semester_start=None,
                 path=None, extra=None):
        self.mode = mode
        self.sections = [_pair(x) for x in (sections or [])]
        self.blocks = [_pair(x) for x in (blocks or [])]
        self.remind_minutes = int(remind_minutes)
        self.semester_start = (semester_start or '').strip() or None
        self.path = path
        self.extra = dict(extra or {})   # 保留文件里的说明性字段
        self._apply_defaults()

    # -- 内部 --------------------------------------------------------------
    def _apply_defaults(self):
        """保证生效的那一套时间表非空"""
        if self.mode == 'blocks':
            if not self.blocks:
                self.blocks = [_pair(x) for x in PRESET_BLOCKS]
        else:
            self.mode = 'sections'
            if not self.sections:
                self.sections = [_pair(x) for x in PRESET_HZC]

    @property
    def total_blocks(self):
        return len(self.blocks) if self.mode == 'blocks' else len(self.sections)

    @property
    def max_section(self):
        """可表达的最大节次"""
        n = self.total_blocks
        return n * 2 if self.mode == 'blocks' else n

    # -- 节次 -> 时间 ------------------------------------------------------
    def ensure_capacity(self, max_section):
        """节次超出当前表长时，按最后一节的时长自动顺延补齐"""
        need = (max_section + 1) // 2 if self.mode == 'blocks' else max_section
        while self.total_blocks < need:
            if self.mode == 'blocks':
                s, e = self.blocks[-1]
                dur = max(30, _to_min(e) - _to_min(s))
                ns = add_minutes(e, 10)
                self.blocks.append((ns, add_minutes(ns, dur)))
            else:
                s, e = self.sections[-1]
                dur = max(30, _to_min(e) - _to_min(s))
                ns = add_minutes(e, DEFAULT_BREAK)
                self.sections.append((ns, add_minutes(ns, dur)))

    def block_time(self, sections):
        """节次列表 -> (开始时间, 结束时间)"""
        if not sections:
            return None
        a, b = max(1, min(sections)), max(1, max(sections))
        self.ensure_capacity(b)
        if self.mode == 'blocks':
            i = min((a - 1) // 2, len(self.blocks) - 1)
            j = min((b - 1) // 2, len(self.blocks) - 1)
            return self.blocks[i][0], self.blocks[j][1]
        i = min(a - 1, len(self.sections) - 1)
        j = min(b - 1, len(self.sections) - 1)
        return self.sections[i][0], self.sections[j][1]

    # -- 序列化 ------------------------------------------------------------
    def to_dict(self):
        d = dict(self.extra)          # 先铺说明性字段，便于人读
        d['mode'] = self.mode
        d['remind_minutes'] = self.remind_minutes
        if self.mode == 'blocks':
            d['blocks'] = [list(x) for x in self.blocks]
            if self.sections:
                d['sections'] = [list(x) for x in self.sections]
        else:
            d['sections'] = [list(x) for x in self.sections]
            if self.blocks:
                d['blocks'] = [list(x) for x in self.blocks]
        if self.semester_start:
            d['semester_start'] = self.semester_start
        return d

    @classmethod
    def from_dict(cls, d, path=None):
        d = d or {}
        extras = {k: v for k, v in d.items() if k not in cls._KNOWN}
        mode = d.get('mode')
        sections = d.get('sections') or []
        blocks = d.get('blocks') or []
        if not mode:
            mode = 'blocks' if (blocks and not sections) else 'sections'
        return cls(mode=mode, sections=sections, blocks=blocks,
                   remind_minutes=d.get('remind_minutes', DEFAULT_REMIND_MINUTES),
                   semester_start=d.get('semester_start'), path=path, extra=extras)

    # -- 校验 / 展示 -------------------------------------------------------
    def validate(self):
        """返回警告列表（不阻断执行）"""
        warns = []
        table = self.blocks if self.mode == 'blocks' else self.sections
        unit = '块' if self.mode == 'blocks' else '节'
        for idx, (s, e) in enumerate(table, 1):
            if _to_min(e) <= _to_min(s):
                warns.append(f'第{idx}{unit}结束时间不晚于开始时间（{s}-{e}）')
        for idx in range(1, len(table)):
            prev_e = _to_min(table[idx - 1][1])
            cur_s = _to_min(table[idx][0])
            if cur_s < prev_e:
                warns.append(f'第{idx}{unit}与第{idx + 1}{unit}时间重叠')
        if self.remind_minutes < 0:
            warns.append('课前提醒分钟数为负数，已按 0 处理')
        if self.remind_minutes > 120:
            warns.append(f'课前提醒 {self.remind_minutes} 分钟，是不是太早了？')
        return warns

    def table_lines(self):
        lines = []
        if self.mode == 'blocks':
            lines.append('节次时间（两节连排）:')
            for i, (s, e) in enumerate(self.blocks, 1):
                a, b = (i - 1) * 2 + 1, i * 2
                lines.append(f'  第 {a:>2}-{b:<2} 节   {s} - {e}')
        else:
            lines.append('节次时间（逐节）:')
            for i, (s, e) in enumerate(self.sections, 1):
                lines.append(f'  第 {i:>2} 节      {s} - {e}')
        rem = '不提醒' if self.remind_minutes <= 0 else f'提前 {self.remind_minutes} 分钟'
        lines.append(f'课前提醒: {rem}')
        lines.append(f'第 1 周周一: {self.semester_start or "（自动从教学周历获取）"}')
        return lines

    def describe(self):
        return '\n'.join(self.table_lines())

    # -- 持久化 ------------------------------------------------------------
    def save(self, path=None):
        path = path or self.path or DEFAULT_PATH
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(dump_readable(self.to_dict()))
        return os.path.abspath(path)


def load(path=None, silent=False):
    """读取时间设置；文件不存在则返回内置默认值"""
    path = path or DEFAULT_PATH
    if path and os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                st = Settings.from_dict(json.load(f), path=path)
            st.path = path
            if not silent:
                print(f'      已加载时间设置: {os.path.abspath(path)}')
            return st
        except Exception as e:  # noqa: BLE001
            print(f'      ⚠ 时间设置读取失败（{e}），改用内置默认值')
            return Settings(path=path)
    return Settings(path=path if path else None)


def apply_preset(name, base=None):
    """套用内置预设，保留原有的提醒分钟数与学期起始日"""
    key = (name or DEFAULT_PRESET).strip().lower()
    if key not in PRESETS:
        raise ValueError(f'未知预设 {name!r}，可选：{", ".join(PRESET_ORDER)}')
    p = PRESETS[key]
    base = base or Settings()
    return Settings(mode=p['mode'], sections=p.get('sections'),
                    blocks=p.get('blocks'),
                    remind_minutes=base.remind_minutes,
                    semester_start=base.semester_start,
                    path=base.path, extra=base.extra)


# --------------------------------------------------------------------------
# 交互式向导
# --------------------------------------------------------------------------

def _ask(prompt, default=None):
    """安全读取一行输入；EOF/中断返回 None"""
    suffix = f' [{default}]' if default not in (None, '') else ''
    try:
        raw = input(f'{prompt}{suffix}: ').strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return raw or (default if default is not None else '')


def interactive_setup(base=None, path=None):
    """交互式设置作息时间，返回新的 Settings（未保存）"""
    cur = base or load(path, silent=True)
    path = path or cur.path or DEFAULT_PATH

    print()
    print('=' * 52)
    print(' 作息时间设置')
    print('=' * 52)
    print('当前生效的设置：')
    print(cur.describe())
    print('-' * 52)
    print('请选择作息模式：')
    for n, key in enumerate(PRESET_ORDER, 1):
        print(f'  {n}) {PRESETS[key]["label"]}')
    print(f'  {len(PRESET_ORDER) + 1}) 逐节自定义')
    print('  q) 放弃，退出')

    choice = _ask('选择', '1')
    if choice is None or choice.lower() in ('q', 'quit', ''):
        print('已取消，未做修改。')
        return cur

    if choice.isdigit() and 1 <= int(choice) <= len(PRESET_ORDER):
        st = apply_preset(PRESET_ORDER[int(choice) - 1], base=cur)
        print(f'\n已选择：{PRESETS[PRESET_ORDER[int(choice) - 1]]["label"]}')
    elif choice.isdigit() and int(choice) == len(PRESET_ORDER) + 1:
        st = _custom_sections(cur)
        if st is None:
            return cur
    else:
        print('看不懂，已取消。')
        return cur

    # 提醒
    rem = _ask('\n课前提醒（分钟，0 = 不提醒）', str(st.remind_minutes))
    if rem is not None and rem != '':
        try:
            st.remind_minutes = max(0, int(rem))
        except ValueError:
            print('不是数字，保留原值。')

    # 学期起始
    start = _ask('第 1 周周一日期（YYYY-MM-DD，留空 = 自动获取）',
                 st.semester_start or '')
    if start is not None:
        start = start.strip()
        if not start:
            st.semester_start = None
        elif re.match(r'^\d{4}-\d{2}-\d{2}$', start):
            st.semester_start = start
        else:
            print('日期格式不对，保留原值。')

    st.path = path
    print('\n新的设置：')
    print(st.describe())
    for w in st.validate():
        print(f'  ⚠ {w}')

    ok = _ask('\n保存到 ' + os.path.abspath(path) + ' ？(y/n)', 'y')
    if ok is not None and ok.lower().startswith('y'):
        st.save(path)
        print(f'已保存: {os.path.abspath(path)}')
    else:
        print('未保存。')
    return st


def _custom_sections(cur):
    """逐节自定义"""
    n = _ask('共几节？', str(len(cur.sections) if cur.sections else 12))
    if n is None:
        return None
    try:
        n = max(1, min(20, int(n)))
    except ValueError:
        print('不是数字，已取消。')
        return None

    seed = list(cur.sections) if cur.sections else [_pair(x) for x in PRESET_HZC]
    while len(seed) < n:
        s, e = seed[-1]
        dur = max(30, _to_min(e) - _to_min(s))
        ns = add_minutes(e, DEFAULT_BREAK)
        seed.append((ns, add_minutes(ns, dur)))

    print('\n逐节输入，格式 HH:MM-HH:MM；直接回车保留当前值；输入 q 结束。')
    out = []
    for i in range(n):
        default = format_pair(seed[i]).replace(' - ', '-')
        val = _ask(f'  第 {i + 1:>2} 节', default)
        if val is None or val.lower() == 'q':
            out.extend(seed[i:])
            break
        try:
            parts = re.split(r'\s*[-~至]\s*', val)
            if len(parts) != 2:
                raise ValueError('格式不对')
            out.append((norm_hm(parts[0]), norm_hm(parts[1])))
        except ValueError as e:  # noqa: BLE001
            print(f'      ⚠ {e}，保留原值 {default}')
            out.append(seed[i])
    return Settings(mode='sections', sections=out,
                    remind_minutes=cur.remind_minutes,
                    semester_start=cur.semester_start, path=cur.path,
                    extra=cur.extra)


# --------------------------------------------------------------------------
# 独立运行
# --------------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser(description='作息时间设置')
    ap.add_argument('--file', default=DEFAULT_PATH, help='设置文件路径')
    ap.add_argument('--show', action='store_true', help='打印当前设置')
    ap.add_argument('--preset', help='套用预设：' + '/'.join(PRESET_ORDER))
    ap.add_argument('--sections', help='逐节指定，如 "08:00-08:45,08:50-09:35"')
    ap.add_argument('--remind', type=int, help='课前提醒分钟数')
    ap.add_argument('--semester-start', help='第 1 周周一，YYYY-MM-DD')
    ap.add_argument('--setup', action='store_true', help='交互式设置向导')
    a = ap.parse_args()

    if a.setup:
        interactive_setup(path=a.file)
        return

    st = load(a.file, silent=True)
    changed = False

    if a.preset:
        st = apply_preset(a.preset, base=st)
        changed = True
    if a.sections:
        st = Settings(mode='sections', sections=parse_section_spec(a.sections),
                      remind_minutes=st.remind_minutes,
                      semester_start=st.semester_start, path=st.path,
                      extra=st.extra)
        changed = True
    if a.remind is not None:
        st.remind_minutes = max(0, a.remind)
        changed = True
    if a.semester_start is not None:
        st.semester_start = a.semester_start.strip() or None
        changed = True

    print(st.describe())
    for w in st.validate():
        print(f'  ⚠ {w}')

    if changed:
        print(f'\n已保存: {st.save(a.file)}')
    elif not a.show:
        print('\n（未做修改；用 --setup 进入向导，或 --preset/--sections/--remind 修改）')


if __name__ == '__main__':
    main()
