# 架构与逆向笔记

本文记录与学校系统对接时的关键发现。操作手册见 `README.md`。

## 1. 系统架构（逆向结果）

```
CAS 统一身份认证   https://cas.hzc.edu.cn/lyuapServer/   (Jasig CAS)
门户               https://portal.hzc.edu.cn/            (联奕 Liferay)
教务系统           https://t.hzc.edu.cn/jsxsd/           (湖南强智科技 新版 V)
```

## 2. 登录链路

教务直连登录已关闭（`Logon.do` 只回"请先登录系统"），只能走 CAS SSO：

```
① CAS 登录 → portal（表单 username/password/captcha/lt/execution）
② GET /sso.jsp               → 返回 JS：location.href='.../login?service=.../sso.jsp'
③ GET 该 CAS 地址            → 持 CASTGC/TGT 自动签 ticket
        → 302 sso.jsp?ticket=ST-...
        → 302 LoginToXk?method=jwxt&ticket1=...
        → 302 /jsxsd/framework/xsMainV.htmlx     ← 已登录
```

**最容易卡住的地方**：第 ② 步是 JavaScript 重定向，`requests` 不执行 JS，
必须正则解析 `location.href` 后手动再发一次请求（见 `hzc.py:connect`）。

验证码策略（`cas_login.py:login`）：

| 服务端返回 | 判断 | 动作 |
|---|---|---|
| `验证码不正确` | OCR 认错 | 安全重试，最多 6 次 |
| `账号或密码错误` | 凭据问题 | 立即停止，绝不重试（防锁号） |
| 其它未知错误 | 说不清 | 不盲目重试，直接报错 |

每次尝试都开全新 `requests.Session`（验证码与会话 cookie 绑定，复用必失败）。
所有会话 `trust_env = False`，不走环境代理（见 `cas_login.py`）。

## 3. 课表解析的五个坑（`parse_kb.py`）

1. **`div.kbcontent1` 是重复数据**——只取 `div.kbcontent`，否则课程翻倍。
2. **链式节次**：连上 4 节写作 `[05-06-07-08节]`（不是 `[05-08节]`）。
   1 个连字符按区间展开，≥2 个按枚举取全部数字。
3. **一格叠两门课**：后一门会覆盖前一门的教师/周次。
   按"无 title 的 font"切块（每个课程名开新块）。
4. **块尾学时说明也是无 title 的 font**（如 `(讲课:36,实践:36)`），切块时要排除。
5. **最后一行「备注:」里还有课**（劳育/实训周），没有固定节次 → 单独解析，不生成日程。

> 排错口诀：用户说"少了一节课"时，先把 HTML 按格子摊开数三遍——
> `div.kbcontent` 总数、每格里的 font 序列、备注行。别先怀疑网络或登录。

## 4. 名单交叉校验（防漏课复发）

页面底部两张默认隐藏的表是官方课程名单：

| 表格 id | 内容 |
|---|---|
| `dataTables2` | 有课表课程（应等于课表课程门数） |
| `dataTables` | 无课表课程 |

`parse_roster()` 按表头名定位列（不写死下标，未排课行教师可能为空），
`diff_roster()` 双向比对：名单有而课表无 = 漏课告警；反之 = 可疑识别。

## 5. 教学周历（`calendar_xl.py`）

- `table#kbtable`，数据行 = `[周次, 周日日期, 周一, …, 周六, 备注]`，**该周周一 = 周日日期 + 1 天**。
- 跨年：月份 ≥ 8 属学年起始年，≤ 7 属次年。
- **备注列整体错位一行**（第 N 行备注描述第 N-1 周），解析放假日期必须按此偏移修正。

## 6. 作息建模（`settings.py`）

- `sections` 逐节定义（当前使用）：`block_time([1,2])` = 第 1 节开始 → 第 2 节结束。
- `blocks` 两节连排：取整块起止。
- 预设 `hzc`（默认）= 学校《大专上课时间表》12 节实测值，含 20 分钟课间（不入表）与仅 35 分钟的第 10 节。
- `_` 开头字段视为注释，读写原样保留。
