"""惠州城市职业学院 教务系统 会话模块

封装: CAS 统一身份认证登录 -> sso.jsp 单点登录 -> 拿到教务系统会话
"""
import re
import warnings

import requests

from cas_login import login as cas_login

warnings.filterwarnings("ignore")

JWXT = "https://t.hzc.edu.cn"
SSO_ENTRY = JWXT + "/sso.jsp"
MAIN_PAGE = JWXT + "/jsxsd/framework/xsMainV.htmlx"
KB_PAGE = JWXT + "/jsxsd/xskb/xskb_list.do"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class JwxtSession:
    """已登录教务系统的会话。"""

    def __init__(self, session: requests.Session):
        self.s = session
        self.s.headers.setdefault("User-Agent", UA)

    def get(self, url, **kw):
        kw.setdefault("verify", False)
        kw.setdefault("timeout", 30)
        return self.s.get(url, **kw)

    def post(self, url, **kw):
        kw.setdefault("verify", False)
        kw.setdefault("timeout", 30)
        return self.s.post(url, **kw)

    def course_table(self, week=None, semester=None):
        """取课表 HTML。week: 周次(1-based)；semester: 如 2026-2027-1"""
        params = {}
        if week:
            params["zc"] = str(week)
        if semester:
            params["xnxq01id"] = semester
        r = self.get(KB_PAGE, params=params or None)
        return r.text


def _retry(fn, times=4, delay=2.0, what=""):
    """网络抖动重试（教务系统偶尔会 RemoteDisconnected）。"""
    import time
    last = None
    for i in range(times):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < times - 1:
                time.sleep(delay * (i + 1))
    raise RuntimeError(f"{what} 重试 {times} 次仍失败: {last}")


def connect(username: str, password: str, debug: bool = False,
            log=None) -> JwxtSession:
    """完成 CAS 登录 + SSO，返回已登录教务系统的会话。

    完整链路:
      CAS(lyuapServer) 登录 -> portal
        -> GET /sso.jsp  (返回 JS 跳转)
        -> GET 该 CAS URL (持 CASTGC 自动签 ticket)
        -> sso.jsp?ticket=ST-... -> LoginToXk?method=jwxt&ticket1=...
        -> xsMainV.htmlx  (已登录)
    """
    s, ok, msg = cas_login(username, password, debug=debug, log=log)
    if not ok:
        raise RuntimeError(f"CAS 登录失败: {msg}")

    if log:
        log("CAS 登录成功，SSO 跳转到教务系统 ...")

    if debug:
        print("[debug] CAS 登录成功，开始 SSO 到教务系统")

    # 1) sso.jsp 返回 JS 跳转
    r = _retry(lambda: s.get(SSO_ENTRY, verify=False, timeout=30),
               what="请求 sso.jsp")
    m = re.search(r"location\.href\s*=\s*'([^']+)'", r.text)
    if not m:
        raise RuntimeError("sso.jsp 未返回预期的 JS 跳转，页面结构可能已变更")

    # 2) 访问 CAS（持 CASTGC 会自动 302 签发 ticket 并跳回 sso.jsp）
    r2 = _retry(lambda: s.get(m.group(1), verify=False, timeout=30,
                              allow_redirects=True),
                what="SSO ticket 流程")

    if debug:
        for h in r2.history:
            print("[debug] ", h.status_code, "->", h.headers.get("Location", "")[:120])
        print("[debug] 最终:", r2.url[:120])

    # 3) 校验是否真的进了教务系统
    if "/jsxsd/" not in r2.url:
        raise RuntimeError(f"SSO 未跳转到教务系统，落点: {r2.url}")

    return JwxtSession(s)


if __name__ == "__main__":
    import sys
    jw = connect(sys.argv[1], sys.argv[2], debug=True)
    print("当前页面:", jw.get(MAIN_PAGE).url)
