"""天气服务：Open-Meteo 免费接口（无 key）
- 坐标查天气：forecast current=temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m
- 城市名查坐标：geocoding-api（语言 zh）
- 坐标反查城市名：Nominatim reverse（限流 1req/s，失败静默）
- 内存缓存 30 分钟，失败静默降级（不阻塞聊天）
"""
import asyncio
import time
import urllib.parse
import urllib.request
import json

from app.utils.logger import get_logger

_logger = get_logger("services.weather")

_UA = {"User-Agent": "AICompanion/1.0 (personal assistant)"}

# WMO weather code -> 中文描述（Open-Meteo）
_WMO_CN = {
    0: "晴", 1: "基本晴朗", 2: "局部多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "雷暴伴冰雹",
}


def _get_json(url: str, timeout: float = 8.0) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        _logger.warning("Weather http fail (%s): %s", url[:60], e)
        return None


def _wmo_desc(code) -> str:
    try:
        return _WMO_CN.get(int(code), "未知")
    except (TypeError, ValueError):
        return "未知"


# ---------- 城市名 -> 坐标 ----------
_geo_cache: dict = {}
_geo_ts: dict = {}


async def city_to_coords(city: str) -> dict | None:
    """城市名（中/英文）-> {lat, lng, name}；失败 None"""
    city = (city or "").strip()
    if not city:
        return None
    now = time.time()
    if city in _geo_cache and now - _geo_ts.get(city, 0) < 3600:
        return _geo_cache[city]
    url = (
        "https://geocoding-api.open-meteo.com/v1/search?name="
        + urllib.parse.quote(city)
        + "&count=1&language=zh&format=json"
    )
    data = await asyncio.to_thread(_get_json, url, 8.0)  # 2026-08-16 审计：避免同步 urllib 阻塞事件循环
    out = None
    if data and data.get("results"):
        r0 = data["results"][0]
        out = {
            "lat": float(r0["latitude"]),
            "lng": float(r0["longitude"]),
            "name": r0.get("name") or city,
        }
    _geo_cache[city] = out
    _geo_ts[city] = now
    return out


# ---------- 坐标 -> 城市名（Nominatim reverse） ----------
async def coords_to_city(lat: float, lng: float) -> str | None:
    url = (
        "https://nominatim.openstreetmap.org/reverse?lat="
        + f"{lat:.5f}&lon={lng:.5f}&format=json&accept-language=zh"
    )
    data = await asyncio.to_thread(_get_json, url, 3.0)  # 2026-08-16：后台反查也缩短超时（Nominatim 不可达时快速失败）；to_thread 避免同步 urllib 阻塞事件循环
    if not data:
        return None
    addr = data.get("address") or {}
    # 优先级：city > town > county > state 区县
    for k in ("city", "town", "county", "state"):
        if addr.get(k):
            return str(addr[k])[:100]
    return None


# ---------- 天气查询 ----------
_weather_cache: dict = {}
_weather_ts: dict = {}


async def get_weather(lat: float, lng: float) -> dict | None:
    """坐标查当前天气 -> {temperature, description, humidity, wind, code, timezone}；失败 None（30 分钟缓存）"""
    key = f"{lat:.4f},{lng:.4f}"
    now = time.time()
    if key in _weather_cache and now - _weather_ts.get(key, 0) < 1800:
        return _weather_cache[key]
    url = (
        "https://api.open-meteo.com/v1/forecast?latitude="
        + f"{lat:.4f}&longitude={lng:.4f}"
        + "&current=temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m&timezone=auto"
    )
    data = await asyncio.to_thread(_get_json, url, 8.0)  # 2026-08-16 审计：避免同步 urllib 阻塞事件循环
    out = None
    if data and data.get("current"):
        cur = data["current"]
        code = cur.get("weather_code")
        out = {
            "temperature": cur.get("temperature_2m"),
            "description": _wmo_desc(code),
            "code": code,
            "humidity": cur.get("relative_humidity_2m"),
            "wind": cur.get("wind_speed_10m"),
            "timezone": (data.get("timezone") or "").split("/")[-1],
        }
    if out:
        _weather_cache[key] = out
        _weather_ts[key] = now
    return out


async def get_user_weather_line(user_id: int) -> str:
    """按用户位置取一句话天气（供日记/朋友圈/主动消息等文本生成注入）；未开启位置或失败返回空串"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.user import User
        async with async_session_factory() as db:
            r = await db.execute(select(User).where(User.id == user_id))
            user = r.scalar_one_or_none()
        if user is None or not getattr(user, "location_enabled", False):
            return ""
        city = getattr(user, "location_city", None) or getattr(user, "user_location", None)
        wtext = await get_weather_text(
            getattr(user, "location_lat", None),
            getattr(user, "location_lng", None),
            city or "",
        )
        if not wtext:
            return ""
        loc = f"（{city}）" if city else ""
        return f"你所在城市{loc}当前天气：{wtext}。"
    except Exception:
        return ""


async def get_weather_text(lat: float | None, lng: float | None, city_name: str | None = None) -> str | None:
    """按坐标或城市名取天气，返回一句话（如「晴 25°C，湿度 60%」）；失败 None"""
    lat2, lng2 = lat, lng
    name = (city_name or "").strip()
    if lat2 is None or lng2 is None:
        coords = await city_to_coords(name)
        if coords is None:
            return None
        lat2, lng2 = coords["lat"], coords["lng"]
    w = await get_weather(lat2, lng2)
    if w is None:
        return None
    parts = [w["description"], f"{w['temperature']}°C"]
    if w.get("humidity") is not None:
        parts.append(f"湿度 {int(w['humidity'])}%")
    if w.get("wind") is not None:
        parts.append(f"风速 {int(w['wind'])}km/h")
    return "，".join(parts)
