# 惠州城市职业学院 课表 → ics 日历

把教务系统课表导出成 `.ics`，可导入 iOS/Android 日历、Google Calendar、Outlook 等，按周自动生成每节课的日程（含教师、教室、15 分钟课前提醒）。

## 为什么不用现成的工具

原计划部署 [`0xJacky/Qi`](https://github.com/0xJacky/Qi)，但该项目：

- **域名硬编码**在源码里，只支持深圳技术大学（`sztu.edu.cn`），没有任何配置项
- 用的是强智**老版**接口与页面结构，不兼容本校的**新版（V）**系统

因此本目录是**针对惠州城市职业学院从零编写**的适配实现。

## 认证链路（逆向分析结果）

```
①  CAS 统一身份认证
    https://cas.hzc.edu.cn/lyuapServer/login?service=<门户>
    ├─ 表单字段: username / password / captcha / lt / execution / _eventId
    ├─ 验证码:   /lyuapServer/captcha.jsp  (4位数字, ddddocr 识别)
    └─ 成功后 302 回 service，写入 portal 会话

②  门户 https://portal.hzc.edu.cn/  （联奕/Liferay）

③  教务系统单点登录
    GET  https://t.hzc.edu.cn/sso.jsp
      └─ 返回 JS 跳转: location.href='.../login?service=.../sso.jsp'
    GET  该 CAS 地址（持 CASTGC/TGT 自动签发 ticket）
      └─ 302 → sso.jsp?ticket=ST-xxx
      └─ 302 → LoginToXk?method=jwxt&ticket1=xxx
      └─ 302 → /jsxsd/framework/xsMainV.htmlx   ← 已登录

④  数据页面
    课表:     /jsxsd/xskb/xskb_list.do?xnxq01id=<学期>
    教学周历: /jsxsd/jxzl/jxzl_query?xnxq01id=<学期>
```

### 关键坑点

1. **教务系统直连登录已被学校关闭**
   `POST /jsxsd/Logon.do?method=logon&flag=sess` 返回 `{"flag1":2,"msgContent":"请先登录系统"}`，
   且请求必须带 `X-Requested-With: XMLHttpRequest` 才会被当作 AJAX 处理。必须走 CAS。

2. **两跳 SSO**：CAS 登录后先进门户，再由 `sso.jsp` → CAS 签发 ticket → 教务系统。
   中间那层是 **JavaScript 跳转**（`window.location.href=...`），
   `requests` 不执行 JS，必须手动解析并跟随。

3. **验证码识别非 100%**
   服务端错误信息是区分的：
   - `验证码不正确` → 可安全重试（不会锁号）
   - `账号或密码错误` → 必须立即停止，避免触发账号锁定

4. **教学周历的备注列整体错位一行**
   第 N 行的备注描述的是第 N-1 周。核对过：
   「9月3日起正式上课」在第 2 行，而 9/3 属于第 1 周（8/30–9/5）。

## 文件说明

| 文件 | 作用 |
|---|---|
| `cas_login.py` | CAS 登录：验证码 OCR（ddddocr）+ 6 次安全重试，凭据错误立即停（防锁号） |
| `hzc.py` | CAS → 门户 → SSO ticket 三跳会话，解析 JS 跳转 `location.href`，暴露 `course_table()` |
| `parse_kb.py` | 课表格子解析：链式节次展开、一格多课切块、备注行单列，外加官方名单交叉校验 |
| `calendar_xl.py` | 教学周历解析：周次↔日期映射，处理跨年与备注列错位一行 |
| `settings.py` | 作息建模（逐节/连排双模式）+ 内置预设（`hzc` 默认）+ 宽松时间格式解析 |
| `to_ics.py` | iCalendar 生成：节次文案 `5-8` 压缩、VALARM 课前提醒、放假日期剔除 |
| `pipeline.py` | 公共流程：抓取 → 生成 → 缓存 → scp 归档，CLI 与网页共用，失败降级不抛异常 |
| `main.py` | 命令行入口（导出 / 作息向导 / 预设切换 / 原始 HTML 排错） |
| `qq_sync.py` | CalDAV 推送：QQ 日历建表、 dated 命名、旧表清理（`--limit` 可单条测试） |
| `webapp.py` | 网页服务（纯标准库）：点选作息、一键抓取、访客/管理双视图、容器内定时同步 |
| `web/index.html` | 单文件前端：无 CDN 离线可用，含与后端同规则的作息实时预览 |
| `selftest.py` | 离线自检 104 项断言（解析坑与预设回归全覆盖，不联网） |
| `examples/` | `config.example.json`（账号模板）、`qq.example.json`（QQ 授权码模板） |
| `docs/ARCHITECTURE.md` | 逆向笔记：SSO 链路、解析五坑、校验设计（操作看本 README） |
| `Dockerfile` / `docker-compose.yml` / `requirements.txt` | 容器部署：见下文 Docker 一节 |
| `输出/` | 生成的 ics 归档目录（文件名含学校 + 学期 + 生成时间） |

## 网页界面（推荐）

```bash
python webapp.py                # 打开 http://127.0.0.1:8770
python webapp.py --port 9000
python webapp.py --open         # 顺手拉起浏览器
python webapp.py --host 0.0.0.0 --token <口令>    # 局域网/公网访问（建议加口令）
```

页面能做的事：

- **逐节点选每节课的起止时间**（原生时间选择器），可增删节次、切换「逐节 / 两节连排」、套用预设
- **实时预览整周课表**：改一个时间，右侧每条课的钟点立刻跟着变，不用先存再看
- 设置课前提醒分钟数、学期第 1 周周一
- 保存到 `time_settings.json`（`_` 开头的注释字段会保留）
- 一键「从教务系统获取课表」（后台线程 + 进度日志，不用盯着终端）
- 「生成 ics」并直接下载；生成后自动归档到服务器
- 「服务器归档」卡片：开关归档、改目标主机/目录、列出服务器上已有存档

接口一览（都以 `/api/` 开头，`GET`/`POST`）：

| 路径 | 作用 |
|---|---|
| `/api/state` | 一次性返回设置、预设、课表缓存、账号概况 |
| `/api/settings` | 保存时间设置 |
| `/api/reset-times` | 恢复内置默认作息 |
| `/api/config` | 保存账号（密码不会回传到前端） |
| `/api/fetch` + `/api/fetch/status` | 触发抓取 / 轮询进度 |
| `/api/generate`、`/download/ics` | 生成并下载 ics（`?file=xxx.ics` 可下载历史导出） |
| `/api/archive` | `GET` 列服务器存档；`POST` 把本地未上传的补传上去 |

`/api/state` 里还带 `exports`（本地已生成文件）与 `archiveConfig`（归档配置）。

**安全提示**：默认只监听 `127.0.0.1`。用 `--host 0.0.0.0` 暴露到局域网时**必须**加 `--token`，
否则同网段任何人都能读取你的课表并触发登录（不设口令时程序会提示并等待 3 秒）。
口令会注入页面，也可用 `X-Token` 请求头传递。

## 时间设置（命令行方式）

节次对应的钟点由 `time_settings.json` 控制，可配置三项：

| 配置 | 说明 |
|---|---|
| 节次时间表 | 每一节的起止时间。支持 `sections`（逐节）与 `blocks`（两节连排）两种建模 |
| 课前提醒 | 日程提前几分钟提醒，`0` = 不生成提醒 |
| 学期起始日 | 第 1 周周一；填了就不用每次去拉教学周历 |

### 三种改法

```bash
# ① 交互式向导（推荐，不用记格式）
python main.py --setup-times

# ② 直接改 time_settings.json，然后看是否生效
python main.py --show-times

# ③ 命令行一次性指定
python main.py --set-sections "08:00-08:45,08:50-09:35,10:00-10:45,10:50-11:35" --save-times
python main.py --preset block --remind 10 --save-times
python main.py --remind 0 --save-times          # 关掉课前提醒
```

内置预设 (`--preset`)：

| 名称 | 含义 |
|---|---|
| `default` | 每节 45 分钟（当前默认） |
| `compact40` | 每节 40 分钟 |
| `block` | 两节连排：1-2节 08:00-09:40、3-4节 10:00-11:40 …（本工具最初的默认值） |

### time_settings.json 格式

```json
{
  "mode": "sections",
  "remind_minutes": 15,
  "semester_start": "2026-08-31",
  "sections": [
    ["08:00", "08:45"],
    ["08:50", "09:35"]
  ]
}
```

- `mode` 省略时自动推断：有 `sections` 就用 `sections`，否则用 `blocks`
- `blocks` 模式则是 `"blocks": [["08:00","09:40"], ["10:00","11:40"]]`，每项代表 1-2 节、3-4 节……
- 时间格式宽松：`8:00`、`08：00`（全角冒号）都认；命令行里的分隔符 `, ; ， ； 、` 与区间连接符 `- ~ 至 ～ — －` 均可
- 若课表里出现超出表长的节次，会按最后一节的时长自动向后补齐
- 配置里以 `_` 开头的字段是给人看的注释，读写时会原样保留

---

## 使用

```bash
# 首次准备（用 uv 建虚拟环境，Python 3.13）
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe requests beautifulsoup4 lxml ddddocr

# 离线自检（不需要账号，先确认环境没问题）
.venv/Scripts/python.exe selftest.py

# 图形界面（推荐）：在网页里点选时间
.venv/Scripts/python.exe webapp.py

# 命令行直接导出
.venv/Scripts/python.exe main.py --user <账号> --password <密码>
.venv/Scripts/python.exe main.py --config config.json

# 常用参数
--semester 2026-2027-1     指定学期
--out-dir 导出             输出目录（默认 输出/）
--out D:\某处\我的.ics      指定完整路径（给了就忽略上面两项）
--skip-before 2026-09-03   跳过正式上课日之前的课
--keep-weekend             保留周末课程
--start-date 2026-08-31    本次覆盖第1周周一（加 --save-times 持久化）
```

每次跑 `main.py` 会把抓到的课表写进 `cache_courses.json`，
网页界面直接读它做预览，**换时间不用反复登录**。

时间设置相关的参数见上一节。

## Docker 部署（群晖 / NAS / Linux 服务器）

```bash
git clone https://github.com/fengkaobanli/hzc-jwxt.git
cd hzc-jwxt

# 1) 填账号（照模板抄）
cp examples/config.example.json config.json
#   编辑 config.json：user/password（学校 CAS 账号密码）

# 2) 建空状态文件（bind 挂载要求宿主文件先存在）
mkdir -p 输出
echo '{}' > cache_courses.json
echo '{}' > sync_config.json

# 3) QQ 日历同步（可选，不用就跳过）
# cp examples/qq.example.json qq.json
#   编辑 qq.json：QQ 邮箱账号 + 设置里生成的授权码

# 4) 启动
docker compose up -d --build
```

启动后：

| 地址 | 说明 |
|---|---|
| `http://服务器IP:8770/class` | 完整管理页（抓取 / 作息 / 导出 / QQ 同步 / 访客同步设置） |
| `http://服务器IP:8770/` | 访客页（纯课表预览 + 10 分钟一次的同步按钮） |
| `/fengkaobanli` | 与 `/class` 相同的管理页（可改 `webapp.py` 的 `ADMIN_PREFIX`） |

- **自动同步**：容器内自带定时器，网页「同步到邮箱日历」卡片里开关，可选每周一 / 每天 06:30（抓取 → 生成 → 推送 QQ 日历），不需要宿主机 cron
- **作息模板**：内置 `hzc`（惠州城市职业学院官方作息，默认），网页预设下拉框可切换
- **更新**：`git pull && docker compose up -d --build`（依赖没变时构建很快，只重跑 COPY 层）
- 对外暴露（域名/frp/nginx）时，**务必给容器加上 `--token`**，访客页保持开放、管理页收口令

## 输出归档

生成的文件统一放进 `输出/` 目录，文件名自动带上**学校、学期、生成时间（精确到秒）**：

```
输出/
└── 惠州城市职业学院_2026-2027-1_课表_20260914-174822.ics
```

- 目录可用 `--out-dir` 改；想指定完整路径用 `--out`（此时不做自动命名）
- **每生成一次就多一个文件**（按时间区分，方便回溯）。旧的不想留就自己删
- 网页界面的「导出记录」会把 `输出/` 里所有 ics 按时间倒序列出来，点一下即可下载
- 下载接口做了目录穿越防护，只能取 `输出/` 里的文件

### 同步到服务器留存档

生成后会**自动 scp 一份到服务器**（失败只告警，不影响本地已生成的 ics）：

```
fengkaobanli@192.168.1.1:/vol1/1000/class/
```

```bash
--no-archive                 本次不归档
--archive-host user@host     换主机
--archive-dest /path         换目录
```

默认值在 `pipeline.py` 顶部，也可以在 `config.json` 里覆盖：

```json
{
  "user": "你的学号",
  "password": "你的CAS密码",
  "semester": "2026-2027-1",
  "archive_enabled": true,
  "archive_host": "user@你的服务器",
  "archive_dest": "/vol1/1000/class"
}
```

用的是系统自带的 `scp`/`ssh`（Windows 需启用「OpenSSH 客户端」），
走 `BatchMode=yes`，**不会弹密码提示**，连不上直接报错返回。

网页界面的「服务器归档」卡片可以开关归档、改目标，并列出服务器上已有的存档；
「补齐未上传的」会把本地有、服务器没有的一并补传。

想改学校名或本地目录名，在 `pipeline.py` 顶部：

```python
SCHOOL_NAME = "惠州城市职业学院"
DEFAULT_OUT_DIR = "输出"
DEFAULT_ARCHIVE_HOST = "fengkaobanli@192.168.1.1"
DEFAULT_ARCHIVE_DEST = "/vol1/1000/class"
```

## 已验证的数据

- 学期：`2026-2027-1`
- 第 1 周周一：`2026-08-31`（教学周历第 1 周 = 8/30 周日 ~ 9/5 周六）
- 课程 15 条 / 日程 131 条（教务改排课后会变，以实际抓取为准）
- 作息：按学校《大专上课时间表》，1-2 节 `09:00-10:25`、3-4 节 `10:45-12:10`、5-6 节 `14:35-16:00`、5-8 节 `14:35-17:30`

| 星期 | 节次 | 时间 | 课程 | 教师 | 教室 |
|---|---|---|---|---|---|
| 周一 | 3-4 | 10:45-12:10 | 电气工程CAD | 吕哲 | 18栋0306虚拟仿真实训室 |
| 周一 | 5-6 | 14:35-16:00 | 中华优秀文化教育 | 杨柳 | 14栋0509阶梯课室 |
| 周二 | 1-2 | 09:00-10:25 | 计算机三维辅助设计 | 熊东海 | 18栋0306虚拟仿真实训室 |
| 周二 | 3-4 | 10:45-12:10 | 计算机三维辅助设计 | 熊东海 | 18栋0306虚拟仿真实训室 |
| 周二 | 5-8 | 14:35-17:30 | 工业机器人操作与编程 | 胡肖荣健 | 17栋0101工业机器人实训中心 |
| 周三 | 1-2 | 09:00-10:25 | 形势与政策（3）（第 4,8,12,16 周） | 朱小珊 | 17栋0206阶梯课室 |
| 周三 | 1-2 | 09:00-10:25 | 智能制造基础（仅第 7 周） | 林郁茹 | 18栋0402 |
| 周三 | 3-4 | 10:45-12:10 | 液压与气动系统及维护 | 凌彩勇 | 180304液压与气动实训室 |
| 周四 | 1-2 | 09:00-10:25 | 传感器与检测技术 | 陆作森 | 180305传感器实训室 |
| 周五 | 1-2 | 09:00-10:25 | 智能制造基础 | 林郁茹 | 18栋0412 |

### 备注栏（无固定节次，不生成日程）

课表页最后一行「备注:」里还有 3 门课，教务系统没有给它们排星期/节次，
`parse_kb.parse_remarks()` 会解析出来并在 CLI / 网页里提示：

| 课程 | 教师 | 周次 |
|---|---|---|
| 劳动教育（3） | — | 1-22 周 |
| 工业机器人焊接与拆装实训 | 胡肖荣健, 凌彩勇 | 1-18 周 |
| 工业机器人系统操作员综合实训 | — | 1-18 周 |

## 课表解析的五个坑（都踩过）

教务系统页面结构不算规范，以下几种情况会导致**课程被静默丢掉或字段串味**，
`parse_kb.py` 现在都能处理，改动时别把它们改回去：

1. **`div.kbcontent1` 是重复数据** —— 只取 `div.kbcontent`，否则课程翻倍。
2. **链式节次**：一次课连上 4 节时写作 `[05-06-07-08节]`（不是 `[05-08节]`）。
   只认 `^\d+-\d+$` 会返回空列表，整门课被丢掉。
   现按连字符个数分流：1 个当区间展开，≥2 个当枚举取全部数字。
3. **一格叠两门课**：例如周三 1-2 节同时有「形势与政策（3）」和「智能制造基础」。
   把整个 `div` 当一门课的话，后一门会把前一门的教师/周次覆盖成缝合怪。
   现按「无 title 的 font」切块，每个课程名开一个新块。
4. **块尾的学时说明也是无 title 的 font**（`(讲课:36,实践:36)`），切块时要排除。
5. **最后一行「备注:」里还有课**（劳动教育、实训周等），没有固定星期/节次。
   `parse_remarks()` 单独解析，在 CLI 与网页里提示 —— 不生成日程，但不当它不存在。

> 排错口决：用户说「少了一节课」时，先把 dump 的 HTML 按格子摊开数三遍 ——
> `div.kbcontent` 总数、每格里的 font 序列、备注行。**先怀疑解析，别先怀疑网络或登录。**

## 需要人工确认的地方

1. **节次时间**：教务系统页面不提供时刻表，`time_settings.json` 现按学校《大专上课时间表》原样填写
   （上午 09:00 起、下午 14:35 起、晚上 18:00 起，共 12 节）。
   注：第 2 节与第 3 节之间有 20 分钟**课间**（10:25–10:45），课间不是课，故不入表；
   第 10 节按学校原表为 18:45–19:20（比其余节少 5 分钟）。
   学校若调整作息，在 `time_settings.json` 改，或用 `python webapp.py` 在网页里点选。

2. **放假日期**：`to_ics.py` 的 `DEFAULT_HOLIDAYS` 取自教学周历备注
   （中秋 9/25、国庆 10/1–7、元旦 2027/1/1）。
   注：周历备注存在错位，建议对照学校通知复核。

3. **正式上课日**：第 1 周（8/31 起）理论上含 8/31–9/2 的开学准备期，
   实际 9 月 3 日起正式上课。如需剔除，加 `--skip-before 2026-09-03`。

## 安全提示

- 本工具需要**明文密码**（学校 CAS 不支持 OAuth/令牌）
- `config.json` 与 `cookies.json` 含敏感信息，已加入 `.gitignore`，请勿提交或分享
- 建议定期修改密码
- **服务器归档依赖 SSH 免密登录**：用当前用户的 `~/.ssh/` 密钥连接，
  走 `BatchMode=yes`（不会交互式索要密码）。换机器或换服务器时记得先配好密钥
- 网页界面若暴露到局域网，务必加 `--token`（见上一节）
