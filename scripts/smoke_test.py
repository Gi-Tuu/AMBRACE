r"""接口冒烟：登录 + 角色 + 朋友圈 + 归档（test/test123 账号）。

用法：backend\.venv\Scripts\python.exe scripts\smoke_test.py
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def req(method, path, body=None, token=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(r, data=data, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def main() -> None:
    st, root = req("GET", "/")
    assert st == 200, f"根路径 {st}: {root}"
    print(f"[OK] GET / -> {st}")

    st, login = req("POST", "/api/v1/auth/login", {"username": "test", "password": "test123"})
    assert st == 200 and login.get("access_token"), f"登录 {st}"
    token = login["access_token"]
    print(f"[OK] 登录 -> token len {len(token)}")

    st, chars = req("GET", "/api/v1/characters", token=token)
    assert st == 200, f"角色列表 {st}"
    print(f"[OK] 角色列表 -> total {chars.get('total')}")

    st, moments = req("GET", "/api/v1/moments?limit=5", token=token)
    assert st == 200, f"朋友圈 {st}"
    print(f"[OK] 朋友圈 -> total {moments.get('total')}")

    st, arch = req("GET", "/api/v1/moments/archive", token=token)
    assert st == 200, f"归档 {st}"
    print(f"[OK] 归档 -> days {arch.get('total_days')}")

    print("\n===== 冒烟通过 =====")


if __name__ == "__main__":
    main()
