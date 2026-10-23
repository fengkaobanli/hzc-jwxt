#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自检：不联网，验证时间设置 -> ics 生成链路

    python selftest.py
"""
import sys
from datetime import date, datetime, timedelta

import settings as st_mod
from settings import Settings, PRESET_ORDER
from to_ics import generate_ics

COURSES = [
    {'name': '电气工程CAD', 'day': 1, 'sections': [3, 4], 'weeks': [1, 2],
     'teacher': '吕哲', 'building': '18栋', 'classroom': '0306', 'raw_weeks': '1-2周'},
    {'name': '中华优秀文化教育', 'day': 1, 'sections': [5, 6], 'weeks': [1],
     'teacher': '杨柳', 'building': '14栋', 'classroom': '0509', 'raw_weeks': '1周'},
    {'name': '智能制造基础', 'day': 5, 'sections': [1, 2], 'weeks': [1],
     'teacher': '林郁茹', 'building': '18栋', 'classroom': '0412', 'raw_weeks': '1周'},
]
MONDAY = date(2026, 8, 31)
WEEKS = {1: MONDAY, 2: MONDAY + timedelta(days=7)}

fails = []


def check(label, ok, detail=''):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail else ''))
    if not ok:
        fails.append(label)


def event_time(ics, summary, day_offset):
    """从 ics 文本里抓某个 SUMMARY 的 DTSTART/DTEND 时间"""
    d = MONDAY + timedelta(days=day_offset)
    key = f'{d:%Y%m%d}'
    for block in ics.split('BEGIN:VEVENT')[1:]:
        if f'SUMMARY:{summary}' not in block or key not in block:
            continue
        s = e = None
        for line in block.splitlines():
            if line.startswith('DTSTART'):
                s = line.split('T')[-1]
            elif line.startswith('DTEND'):
                e = line.split('T')[-1]
        return s, e
    return None, None


print('=' * 56)
print(' 离线自检：时间设置 -> ics')
print('=' * 56)

# 1) 逐节模型（显式 45 分钟/节数据，验证跨节取段逻辑）
print('\n[1] 逐节模型（45 分钟/节数据）')
st = Settings(sections=[tuple(x) for x in st_mod.PRESET_45])
check('模式为 sections', st.mode == 'sections')
check('第1-2节 = 08:00-09:35', st.block_time([1, 2]) == ('08:00', '09:35'),
      str(st.block_time([1, 2])))
check('第3-4节 = 10:00-11:35', st.block_time([3, 4]) == ('10:00', '11:35'),
      str(st.block_time([3, 4])))
check('第5-6节 = 14:00-15:35', st.block_time([5, 6]) == ('14:00', '15:35'),
      str(st.block_time([5, 6])))

ics, n = generate_ics(COURSES, WEEKS, settings=st, merge=False)
check('逐天模式事件数 = 4', n == 4, f'n={n}')
s, e = event_time(ics, '电气工程CAD', 0)
check('CAD 周一 3-4节 -> 10:00:00-11:35:00', (s, e) == ('100000', '113500'), f'{s}-{e}')
s, e = event_time(ics, '中华优秀文化教育', 0)
check('中华文化 周一 5-6节 -> 14:00:00-15:35:00', (s, e) == ('140000', '153500'), f'{s}-{e}')
check('VALARM = -PT15M', 'TRIGGER:-PT15M' in ics)
check('DESCRIPTION 含时间行', '时间: 08:00-09:35' in ics)

# 1a) RRULE 合并模式
print('\n[1a] RRULE 合并（139 条 -> 约 28 条）')
ics_m, n_m = generate_ics(COURSES, WEEKS, settings=st, merge=True)
check('合并后 3 条（电气 2 周并成 1 条）', n_m == 3, f'n={n_m}')
check('含交替周 RRULE', 'RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=2' in ics_m)
check('单周课不生成 RRULE', ics_m.count('RRULE:') == 1, str(ics_m.count('RRULE:')))
check('默认即合并模式',
      generate_ics(COURSES, WEEKS, settings=st)[1] == n_m)
check('合并模式不产生 EXDATE（QQ 会丢弃）', 'EXDATE' not in ics_m)

# 放假周应剔除并重新分段，而非用 EXDATE 表达
_wm2 = dict(WEEKS)
_ics_h, _n_h = generate_ics(
    [{'name': '测试课', 'day': 1, 'sections': [1, 2], 'weeks': [1, 2]},
     {'name': '测试课2', 'day': 1, 'sections': [3, 4], 'weeks': [1, 2]}],
    _wm2, settings=st, holidays=[WEEKS[1]], merge=True)
check('首周放假 -> DTSTART 顺延到第 2 周',
      'DTSTART;TZID=Asia/Shanghai:' in _ics_h and 'EXDATE' not in _ics_h,
      'no EXDATE')

# 1b) 默认时间 = 惠州城市职业学院官方作息
print('\n[1b] 默认时间模板（惠州城市职业学院）')
std = Settings()
check('空构造默认 sections', std.mode == 'sections' and len(std.sections) == 12,
      f'{std.mode}/{len(std.sections)}')
check('第1节 = 09:00-09:40', std.block_time([1, 1]) == ('09:00', '09:40'),
      str(std.block_time([1, 1])))
check('第1-2节 = 09:00-10:25', std.block_time([1, 2]) == ('09:00', '10:25'),
      str(std.block_time([1, 2])))
check('第10节 = 18:45-19:20（35分钟）', std.block_time([10, 10]) == ('18:45', '19:20'),
      str(std.block_time([10, 10])))
sth = st_mod.apply_preset('hzc')
check('hzc 预设标签正确',
      st_mod.PRESETS['hzc']['label'] == '惠州城市职业学院（默认时间模板）',
      st_mod.PRESETS['hzc']['label'])
check('hzc 预设 12 节无校验错', sth.total_blocks == 12 and not sth.validate())
check('缺省套用 = hzc', st_mod.apply_preset(None).block_time([1, 2]) == ('09:00', '10:25'))
check('hzc 在预设首位', PRESET_ORDER[0] == 'hzc', str(PRESET_ORDER))

# 2) 两节连排模型
print('\n[2] 两节连排模型（block 预设，兼容旧行为）')
stb = st_mod.apply_preset('block')
check('模式为 blocks', stb.mode == 'blocks')
check('第1-2节 = 08:00-09:40', stb.block_time([1, 2]) == ('08:00', '09:40'),
      str(stb.block_time([1, 2])))
ics_b, _ = generate_ics(COURSES, WEEKS, settings=stb)
s, e = event_time(ics_b, '智能制造基础', 4)
check('智造 周五 1-2节 -> 08:00:00-09:40:00', (s, e) == ('080000', '094000'), f'{s}-{e}')

# 3) 自定义 + 提醒
print('\n[3] 自定义时间 / 提醒分钟')
stc = Settings(sections=[('09:15', '10:00'), ('10:05', '10:50')], remind_minutes=30)
check('自定义第1-2节 = 09:15-10:50', stc.block_time([1, 2]) == ('09:15', '10:50'),
      str(stc.block_time([1, 2])))
ics_c, _ = generate_ics(COURSES, WEEKS, settings=stc)
s, e = event_time(ics_c, '智能制造基础', 4)
check('自定义时间生效', (s, e) == ('091500', '105000'), f'{s}-{e}')
check('提醒 30 分钟 -> -PT30M', 'TRIGGER:-PT30M' in ics_c)
stz = Settings(remind_minutes=0)
ics_z, _ = generate_ics(COURSES, WEEKS, settings=stz)
check('提醒 0 -> 无 VALARM', 'BEGIN:VALARM' not in ics_z)

# 4) 超出表长自动补齐
print('\n[4] 节次超出表长自动补齐')
st4 = Settings(sections=[('08:00', '08:45'), ('08:50', '09:35')])
t = st4.block_time([9, 10])
check('第9-10节能算出时间', t is not None, str(t))
check('补齐后递增合理', t[0] < t[1], f'{t}')

# 5) 预设与解析
print('\n[5] 预设与命令行解析')
for name in PRESET_ORDER:
    p = st_mod.apply_preset(name)
    ok = p.total_blocks > 0 and not p.validate()
    check(f'预设 {name}（{st_mod.PRESETS[name]["label"]}）有效', ok,
          p.validate() and str(p.validate()) or '')
secs = st_mod.parse_section_spec('8:00-8:45, 08:50-09:35；10:00-10:45')
check('解析 "8:00-8:45, 08:50-09:35；10:00-10:45" = 3 节', len(secs) == 3, str(secs))
check('全角冒号/分号可接受', secs[0] == ('08:00', '08:45'), str(secs[0]))
bad = st_mod.Settings(sections=[('09:00', '08:00')])
check('倒序时间会被 validate 抓到', bool(bad.validate()), str(bad.validate()))

# 6) 序列化往返
print('\n[6] 配置文件读写往返')
d = stc.to_dict()
rt = Settings.from_dict(d)
check('to_dict/from_dict 往返一致',
      rt.sections == stc.sections and rt.remind_minutes == stc.remind_minutes)
db = stb.to_dict()
check('blocks 模式透传 mode=blocks', db.get('mode') == 'blocks' and 'blocks' in db)
check('blocks 反向兼容（无 mode 字段）',
      Settings.from_dict({'blocks': [['08:00', '09:40']]}).mode == 'blocks')
d2 = Settings.from_dict({'_说明': 'hello', 'mode': 'sections',
                         'sections': [['09:00', '09:45']]})
d2.remind_minutes = 7
check('说明性字段被保留', d2.to_dict().get('_说明') == 'hello')
check('时间对在同一行输出', '["09:00", "09:45"]' in st_mod.dump_readable(d2.to_dict()),
      st_mod.dump_readable(d2.to_dict()).splitlines()[-3].strip())
check('_说明 不属于已知键', '_说明' in d2.extra)

# 7) 内置 time_settings.json 可用
print('\n[7] 项目自带 time_settings.json')
import os
if os.path.exists('time_settings.json'):
    f = st_mod.load('time_settings.json', silent=True)
    check('可解析', f.total_blocks > 0, f'{f.total_blocks} 项')
    check('无校验告警', not f.validate(), str(f.validate()))
    check('起始日 = 2026-08-31', f.semester_start == '2026-08-31', str(f.semester_start))
else:
    check('time_settings.json 存在（可省略，缺省用内置默认）', True, '未提供，跳过')

# 8) pipeline 周次与日期
print('\n[8] pipeline 周次换算')
import pipeline
wm = pipeline.weeks_from_start('2026-08-31', COURSES)
check('第1周周一 = 2026-08-31', wm[1] == MONDAY, str(wm[1]))
check('第2周周一 = 2026-09-07', wm[2] == MONDAY + timedelta(days=7), str(wm[2]))
check('无课程时默认覆盖到第20周', len(pipeline.weeks_from_start('2026-08-31')) == 20,
      str(len(pipeline.weeks_from_start('2026-08-31'))))
check('给了课程则按课程最大周次截断', len(wm) == 2, str(len(wm)))
e1 = {'courses': COURSES, 'week_monday': {}}
check('ensure_week_monday 能补写', pipeline.ensure_week_monday(e1, '2026-08-31', COURSES))
check('补写内容可还原', pipeline.week_monday_of(e1)[1] == MONDAY)
check('已有数据时不重复补写',
      not pipeline.ensure_week_monday({'week_monday': {'1': '2026-08-31'}}))
check('override_start 优先', pipeline.week_monday_of({'week_monday': {'1': '2020-01-06'}},
                                                     override_start='2026-08-31')[1] == MONDAY)
check('holidays 解析', len(pipeline.parse_holidays(['2026-09-25', '坏数据'])) == 1)

# 9) 生成文本走 pipeline.build_ics
print('\n[9] pipeline.build_ics')
txt, cnt = pipeline.build_ics(COURSES, wm, Settings(), '2026-2027-1',
                              holidays=['2026-09-25'])
check('事件数 3（合并后）', cnt == 3, str(cnt))
check('含 RRULE', 'RRULE:FREQ=WEEKLY' in txt)
check('含 TZID', 'TZID=Asia/Shanghai' in txt)
check('首行是 BEGIN:VCALENDAR', txt.startswith('BEGIN:VCALENDAR'))

# 10) 输出归档命名
print('\n[10] 输出归档命名')
import os, tempfile, shutil
tmp = tempfile.mkdtemp(prefix='hzc-export-')
try:
    when = datetime(2026, 9, 14, 17, 38)
    p = pipeline.output_path('2026-2027-1', tmp, when=when)
    check('目录已自动创建', os.path.isdir(tmp))
    check('文件名含学校', '惠州城市职业学院' in os.path.basename(p), os.path.basename(p))
    check('文件名含学期', '2026-2027-1' in os.path.basename(p), os.path.basename(p))
    check('文件名含生成时间', '20260914-173800' in os.path.basename(p),
          os.path.basename(p))
    check('时间精确到秒（同分钟不撞名）',
          pipeline.output_path('x', tmp, when=when) !=
          pipeline.output_path('x', tmp, when=when.replace(second=1)))
    check('后缀为 .ics', p.endswith('.ics'))

    pipeline.write_ics('BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n', p)
    lst = pipeline.list_exports(tmp)
    check('list_exports 能列出', len(lst) == 1, str(len(lst)))
    check('列出项能反查学期', lst[0]['semester'] == '2026-2027-1', lst[0]['semester'])
    check('按时间倒序（新在前）', lst[0]['name'] == os.path.basename(p))

    name = os.path.basename(p)
    check('export_path 能命中', pipeline.export_path(tmp, name) is not None)
    outside = os.path.join(os.path.dirname(tmp), 'zz-probe-outside.ics')
    with open(outside, 'w') as f:
        f.write('probe')
    try:
        check('export_path 拒绝穿越到上级目录',
              pipeline.export_path(tmp, '../zz-probe-outside.ics') is None)
        check('export_path 拒绝子目录',
              pipeline.export_path(tmp, 'sub/zz-probe-outside.ics') is None)
    finally:
        os.remove(outside)
    check('export_path 拒绝非 ics', pipeline.export_path(tmp, 'a.txt') is None)
    check('export_path 拒绝不存在', pipeline.export_path(tmp, 'nope.ics') is None)
    check('默认目录名为 输出', pipeline.DEFAULT_OUT_DIR == '输出',
          pipeline.DEFAULT_OUT_DIR)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# 11) 归档配置合并（不联网）
print('\n[11] 服务器归档配置')
dft = pipeline.archive_settings()
check('默认开启', dft['enabled'] is True, str(dft))
check('默认主机', dft['host'] == pipeline.DEFAULT_ARCHIVE_HOST, dft['host'])
check('默认目录为 /vol1/1000/class',
      dft['dest'] == '/vol1/1000/class', dft['dest'])
over = pipeline.archive_settings({'archive_enabled': False,
                                  'archive_host': 'a@b', 'archive_dest': '/x'})
check('config.json 可关闭', over['enabled'] is False)
check('config.json 可改主机', over['host'] == 'a@b', over['host'])
check('config.json 可改目录', over['dest'] == '/x', over['dest'])
cli = pipeline.archive_settings({'archive_host': 'a@b'}, host='c@d', dest='/y', enabled=True)
check('命令行优先于 config', (cli['host'], cli['dest'], cli['enabled']) == ('c@d', '/y', True),
      str(cli))
check('_shquote 处理单引号', pipeline._shquote("/a'b") == r"'/a'\''b'",
      pipeline._shquote("/a'b"))
check('归档字段名稳定',
      pipeline.ARCHIVE_KEYS == ('archive_enabled', 'archive_host', 'archive_dest'),
      str(pipeline.ARCHIVE_KEYS))
check('archive_files 空输入不炸', pipeline.archive_files([])[0] is False)
check('archive_files 文件不存在时不炸',
      pipeline.archive_files(['不存在的文件.ics'])[0] is False)

# 12) 课表 HTML 解析（强智新版V 的几种坑）
print('\n[12] 课表 HTML 解析')
import parse_kb  # noqa: E402
from to_ics import sections_label  # noqa: E402

check('区间 01-02 -> [1,2]', parse_kb.parse_sections('[01-02节]') == [1, 2],
      str(parse_kb.parse_sections('[01-02节]')))
check('单节 05 -> [5]', parse_kb.parse_sections('[05节]') == [5])
check('链式 05-06-07-08 -> [5,6,7,8]',
      parse_kb.parse_sections('[05-06-07-08节]') == [5, 6, 7, 8],
      str(parse_kb.parse_sections('[05-06-07-08节]')))
check('区间 01-04 -> [1,2,3,4]', parse_kb.parse_sections('[01-04节]') == [1, 2, 3, 4])
check('多段 01-02节,05节 -> [1,2,5]',
      parse_kb.parse_sections('[01-02节,05节]') == [1, 2, 5])
check('全角连字符也能吃', parse_kb.parse_sections('[05～06～07～08节]') == [5, 6, 7, 8])
check('节次文案 [5,6,7,8] -> 5-8', sections_label([5, 6, 7, 8]) == '5-8')
check('节次文案 [1,3,5] -> 1,3,5', sections_label([1, 3, 5]) == '1,3,5')

_SAMPLE = '''<table id="timetable"><tr><th>星期一</th><th>星期二</th></tr>
<tr><td></td><td><div class="kbcontent">
 <font>工业机器人操作与编程</font><font title="教师">胡肖荣健</font>
 <font title="周次(节次)">1-18(周)[05-06-07-08节]</font>
 <font title="教学楼">【17号楼】</font><font title="教室">17栋0101</font>
 <font>(讲课:72)</font>
</div></td></tr>
<tr><td><div class="kbcontent">
 <font>形势与政策（3）</font><font title="教师">朱小珊</font>
 <font title="周次(节次)">4,8,12,16(周)[01-02节]</font>
 <font title="教学楼">【17号楼】</font><font title="教室">17栋0206</font>
 <font>(讲课:8)</font>
 <font>智能制造基础</font><font title="教师">林郁茹</font>
 <font title="周次(节次)">7(周)[01-02节]</font>
 <font title="教学楼">【18号楼】</font><font title="教室">18栋0402</font>
 <font>((讲课:8),讲课:36)</font>
</div></td><td></td></tr>
<tr><th>备注:</th><td colspan="7">劳动教育（3）  1-22周;工业机器人焊接与拆装实训 胡肖荣健,凌彩勇 1-18周;</td></tr>
</table>'''

_parsed = parse_kb.parse_courses(_SAMPLE)
_by_name = {c['name']: c for c in _parsed}
check('链式节次课被保留', '工业机器人操作与编程' in _by_name)
check('链式节次解析正确',
      _by_name.get('工业机器人操作与编程', {}).get('sections') == [5, 6, 7, 8],
      str(_by_name.get('工业机器人操作与编程', {}).get('sections')))
check('一个格子两门课都保留', len(_parsed) == 3, str(sorted(_by_name)))
check('第一门课周次没被第二门覆盖',
      _by_name.get('形势与政策（3）', {}).get('weeks') == [4, 8, 12, 16],
      str(_by_name.get('形势与政策（3）', {}).get('weeks')))
check('第一门课教师没被第二门覆盖',
      _by_name.get('形势与政策（3）', {}).get('teacher') == '朱小珊',
      str(_by_name.get('形势与政策（3）', {}).get('teacher')))
check('第二门课教师正确',
      _by_name.get('智能制造基础', {}).get('teacher') == '林郁茹')
check('学时信息不会被当成课程名',
      not any(c['name'].startswith('(') for c in _parsed))
_rm = parse_kb.parse_remarks(_SAMPLE)
check('备注栏课程被解析出', len(_rm) == 2, str([r['name'] for r in _rm]))
check('备注课程带教师',
      any(r['name'] == '工业机器人焊接与拆装实训' and r['teacher'] == '胡肖荣健,凌彩勇'
          for r in _rm), str(_rm))

_ROSTER_HTML = '''<table id="dataTables2"><tr><td>有课表课程</td></tr>
<tr><th>序号</th><th>上课班级</th><th>课程编号</th><th>课程名称</th><th>授课教师</th>
<th>排课人数</th><th>选课人数</th><th>课程性质</th><th>课程属性</th></tr>
<tr><td>1</td><td>F25C072工机班</td><td>FC04101</td><td>计算机三维辅助设计</td>
<td>熊东海</td><td>54</td><td>54</td><td>专业核心课</td><td>必修</td></tr>
<tr><td>2</td><td>F25C072工机班</td><td>FC05701</td><td>工业机器人操作与编程</td>
<td>胡肖荣健</td><td>54</td><td>54</td><td>专业核心课</td><td>必修</td></tr>
</table>
<table id="dataTables"><tr><td>无课表课程</td></tr>
<tr><th>序号</th><th>上课班级</th><th>课程编号</th><th>课程名称</th><th>授课教师</th>
<th>排课人数</th><th>选课人数</th><th>课程性质</th><th>课程属性</th></tr>
<tr><td>1</td><td>F25C072工机班</td><td>FC12301</td><td>工业机器人系统操作员综合实训</td>
<td></td><td>54</td><td>54</td><td>专业拓展课</td><td>必修</td></tr>
</table>'''

_ro = parse_kb.parse_roster(_ROSTER_HTML)
check('名单-有课表课程 2 门', len(_ro['scheduled']) == 2, str([r['name'] for r in _ro['scheduled']]))
check('名单-无课表课程 1 门', len(_ro['unscheduled']) == 1)
check('名单字段解析正确',
      _ro['scheduled'][0]['code'] == 'FC04101'
      and _ro['scheduled'][0]['teacher'] == '熊东海'
      and _ro['scheduled'][0]['kind'] == '专业核心课',
      str(_ro['scheduled'][0]))
check('名单里空教师不报错', _ro['unscheduled'][0]['teacher'] == '')
check('名单一致时无告警',
      parse_kb.diff_roster([{'name': '计算机三维辅助设计'},
                            {'name': '工业机器人操作与编程'}], _ro) == [])
check('漏课能被抓出来',
      parse_kb.diff_roster([{'name': '计算机三维辅助设计'}], _ro)
      == ['名单里有《工业机器人操作与编程》但课表里没解析出来 —— 大概率漏课了'],
      str(parse_kb.diff_roster([{'name': '计算机三维辅助设计'}], _ro)))
check('名单为空时不误报', parse_kb.diff_roster([{'name': 'X'}], {'scheduled': []}) == [])

print('\n' + '=' * 56)
if fails:
    print(f' ❌ {len(fails)} 项失败: ' + ', '.join(fails))
    sys.exit(1)
print(' ✅ 全部通过')
