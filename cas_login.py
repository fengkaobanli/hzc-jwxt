#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
惠州城市职业学院 统一身份认证（联奕 lyuapServer / CAS）自动登录

逆向分析结果（2026-09-14）:
  认证平台: https://cas.hzc.edu.cn/lyuapServer/   (Apache-Coyote/Java, Jasig CAS 协议)
  门户入口: https://bgmh.hzc.edu.cn  ->  跳转 CAS, service 指向 portal.hzc.edu.cn
  教务系统: https://t.hzc.edu.cn/jsxsd/  (强智科技 教务一体化系统, 新版 V)

CAS 登录流程:
  1. GET  /lyuapServer/login?service=<SERVICE>   -> 取 lt / execution / JSESSIONID
  2. GET  /lyuapServer/captcha.jsp               -> 4位数字验证码 (ddddocr 识别)
  3. POST <form action>                          -> username/password/captcha/lt/execution/_eventId
     成功后 302 携带 ticket 回到 service, 建立门户会话

注意:
  - jwxt 自身的账号直连登录已被学校关闭
    (POST /jsxsd/Logon.do?method=logon&flag=sess 返回 {"flag1":2,"msgContent":"请先登录系统"})
    因此必须走 CAS 统一身份认证。
  - 登录失败时 /lyuapServer/login 页面会返回 <div id="msg" class="errors">账号或密码错误</div>

用法:
  python cas_login.py --user <账号> --password <密码> [--save-cookie cookies.json]
"""

import argparse
import json
import re
import sys
import warnings

import requests

warnings.filterwarnings("ignore")

CAS_HOST = "https://cas.hzc.edu.cn"
CAS_BASE = CAS_HOST + "/lyuapServer"

# 门户的 CAS service 参数（从 bgmh.hzc.edu.cn 跳转地址中提取）
SERVICE_PORTAL = "https://portal.hzc.edu.cn/c/portal/login?redirect=%2F&p_l_id=70131"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _ocr():
    """延迟加载 ddddocr，避免未安装时影响其他功能。"""
    try:
        import ddddocr
    except ImportError:
        sys.exit("缺少依赖 ddddocr，请先执行: pip install ddddocr requests")
    return ddddocr.DdddOcr(show_ad=False)


def _parse_form(html: str):
    """从登录页解析 form action / lt / execution。"""
    m = re.search(r'<form[^>]*id="fm1"[^>]*action="([^"]+)"', html)
    if not m:
        m = re.search(r'action="([^"]*login[^"]*)"', html)
    if not m:
        raise RuntimeError("未找到登录表单 action，页面结构可能已变更")

    action = m.group(1).replace("&amp;", "&")
    if action.startswith("/"):
        action = CAS_HOST + action

    def field(name):
        mm = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), html)
        return mm.group(1) if mm else None

    return action, field("lt"), field("execution")


def _attempt(username: str, password: str, service: str, debug: bool, tag: str = ""):
    """单次登录尝试（每次都用全新会话，因为验证码与会话绑定）。"""
    s = requests.Session()
    # 学校站点不过代理：经实测本机代理(127.0.0.1:7777)到 hzc.edu.cn 会 TLS 握手失败，
    # 且用户要求直连。设 False 后不再读取 HTTP(S)_PROXY/NO_PROXY 环境变量。
    s.trust_env = False
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})

    # 1) 取登录页
    r = s.get(CAS_BASE + "/login", params={"service": service}, verify=False, timeout=25)
    action, lt, execution = _parse_form(r.text)

    # 2) 取验证码并识别
    rc = s.get(CAS_BASE + "/captcha.jsp", verify=False, timeout=25,
               headers={"Referer": CAS_BASE + "/login"})
    if not rc.content:
        return s, False, "验证码获取失败（返回空）", None

    code = _ocr().classification(rc.content)
    if debug:
        print(f"  [debug{tag}] OCR = {code}")

    # 3) 提交
    data = {
        "username": username,
        "password": password,
        "captcha": code,
        "lt": lt,
        "execution": execution,
        "_eventId": "submit",
        "submit": "登录",
    }
    r2 = s.post(action, data=data, verify=False, timeout=25, allow_redirects=True,
                headers={"Referer": CAS_BASE + "/login", "Origin": CAS_HOST})

    # 4) 判定结果
    if r2.url.startswith(CAS_BASE + "/login"):
        m = re.search(r'<div[^>]*class="errors"[^>]*>([^<]+)', r2.text)
        msg = m.group(1).strip() if m else "登录被拒（未捕获到具体错误信息）"
        return s, False, msg, rc.content

    return s, True, "登录成功 -> " + r2.url, rc.content


def login(username: str, password: str, service: str = SERVICE_PORTAL,
          debug: bool = False, max_captcha_retry: int = 6, log=None):
    """
    执行 CAS 登录，带验证码重试。

    策略：验证码识别错误时重试（服务端明确返回"验证码不正确"，不会导致锁号）；
          账号或密码错误时立即停止，绝不重试（避免触发账号锁定）。

    返回 (session, ok, message)
    """
    _log = log or (lambda *a: None)
    last_msg = ""
    for i in range(max_captcha_retry):
        _log(f"CAS 登录尝试 ({i+1}/{max_captcha_retry}) ...")
        s, ok, msg, img = _attempt(username, password, service, debug,
                                   tag=f" #{i+1}")

        if ok:
            return s, True, msg

        last_msg = msg

        # 凭据错误 -> 立即停止，不再尝试
        if "账号或密码错误" in msg or "密码错误" in msg:
            if img:
                with open("captcha_failed.jpg", "wb") as f:
                    f.write(img)
            return s, False, msg

        # 验证码错误 -> 重试
        if "验证码" in msg:
            if debug:
                print(f"  验证码识别错误，重试 ({i+1}/{max_captcha_retry})")
            _log(f"验证码识别失败，自动重试 ({i+1}/{max_captcha_retry}) ...")
            continue

        # 其他未知错误 -> 不盲目重试
        return s, False, msg

    return s, False, f"验证码连续识别失败 {max_captcha_retry} 次，最后错误: {last_msg}"


def save_cookies(session: requests.Session, path: str):
    data = {c.name: c.value for c in session.cookies}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def main():
    ap = argparse.ArgumentParser(description="惠州城市职业学院 CAS 自动登录")
    ap.add_argument("--user", required=True, help="账号（你的学号）")
    ap.add_argument("--password", required=True, help="密码")
    ap.add_argument("--service", default=SERVICE_PORTAL, help="CAS service 参数")
    ap.add_argument("--save-cookie", help="把会话 cookie 保存到指定 json 文件")
    ap.add_argument("--debug", action="store_true", help="输出调试信息并保存验证码图片")
    a = ap.parse_args()

    session, ok, msg = login(a.user, a.password, a.service, a.debug)
    print(("✅ " if ok else "❌ ") + msg)

    if ok and a.save_cookie:
        save_cookies(session, a.save_cookie)
        print("cookies 已保存到", a.save_cookie)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
