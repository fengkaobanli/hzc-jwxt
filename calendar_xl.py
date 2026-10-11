"""解析教学周历 -> 周次与日期的映射"""
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


def parse_calendar(html, start_year=None):
    """
    解析教学周历表 (table#kbtable)。

    表格结构:
      表头: ['', '星期日','星期一',...,'星期六','备注']
      数据行: [周次, 周日日期(含月日), 周一, 周二, ..., 周六, 备注]

    返回 {周次: 该周周一的 date}
    """
    soup = BeautifulSoup(html, 'lxml')
    tb = soup.find('table', id='kbtable')
    if not tb:
        raise RuntimeError('未找到教学周历表 (kbtable)')

    rows = tb.find_all('tr')
    if len(rows) < 2:
        raise RuntimeError('教学周历表为空')

    # 从 "2026-2027 学年 第 1 学期" 推断起始年
    if start_year is None:
        m = re.search(r'(20\d\d)\s*[-–]\s*20\d\d\s*学年', html)
        start_year = int(m.group(1)) if m else date.today().year

    result = {}
    for tr in rows[1:]:
        cells = [c.get_text(' ', strip=True) for c in tr.find_all(['td', 'th'])]
        if len(cells) < 3:
            continue
        try:
            week = int(cells[0])
        except (ValueError, IndexError):
            continue

        # cells[1] = 星期日的完整日期, 形如 "08月30日"
        m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', cells[1])
        if not m:
            continue
        month, day = int(m.group(1)), int(m.group(2))
        # 8-12月属于起始年, 1-7月属于次年上半年
        year = start_year if month >= 8 else start_year + 1

        try:
            sunday = date(year, month, day)
        except ValueError:
            continue
        # 该周周一 = 周日 + 1 天
        result[week] = sunday + timedelta(days=1)

    return result


def find_holidays(html):
    """从校历备注中提取放假日期（尽力而为，备注列存在错位情况）。"""
    holidays = set()
    for m in re.finditer(r'(\d{1,2})月(\d{1,2})(?:-(\d{1,2}))?日[^。；\n]{0,20}?放假', html):
        pass  # 备注错位时不精确，默认不自动排除；用户可用 --exclude 手动指定
    return holidays


if __name__ == '__main__':
    h = open('xl.html', encoding='utf-8').read()
    cal = parse_calendar(h)
    for w in sorted(cal):
        print(f'第{w:>2}周 周一 = {cal[w]}')
