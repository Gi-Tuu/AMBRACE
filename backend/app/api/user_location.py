"""用户位置信息 API：位置总开关 / 获取地理位置(GPS) / 用户位置 / AI 位置 / 位置跟随 / 时区 / 天气"""
from app.utils.async_tasks import spawn_background
import time
from fastapi import APIRouter, Depends
from sqlalchemy import select
from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.models.user import User
from app.schemas.user_location import UserLocationResponse, UserLocationUpdate

router = APIRouter(prefix="/api/v1/users", tags=["User Location"])

_reverse_throttle: dict[int, float] = {}  # user_id -> 上次城市反查时间（2026-08-16 审计：防高频 Nominatim 外呼）

_FIELDS = ("location_enabled", "location_gps_enabled", "user_location", "ai_location", "location_follow", "timezone_offset_minutes")


def _clean_location(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s[:100] if s else None


def _to_response(user) -> UserLocationResponse:
    ai_loc = user.ai_location
    if user.location_follow:
        ai_loc = user.user_location  # 位置跟随：AI 位置与用户相同
    return UserLocationResponse(
        user_id=user.id,
        location_enabled=bool(user.location_enabled),
        location_gps_enabled=bool(user.location_gps_enabled),
        user_location=user.user_location,
        ai_location=ai_loc,
        location_follow=bool(user.location_follow),
        timezone_offset_minutes=user.timezone_offset_minutes,
        location_lat=user.location_lat,
        location_lng=user.location_lng,
        location_city=user.location_city,
    )


@router.get("/location", response_model=UserLocationResponse)
async def get_user_location(user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if user is None:
        return UserLocationResponse(user_id=user_id)
    return _to_response(user)


@router.put("/location", response_model=UserLocationResponse)
async def update_user_location(data: UserLocationUpdate, user_id: int = Depends(get_current_user_id)):
    from fastapi import HTTPException
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        payload = data.model_dump(exclude_unset=True)
        if "user_location" in payload:
            user.user_location = _clean_location(payload["user_location"])
        if "ai_location" in payload:
            user.ai_location = _clean_location(payload["ai_location"])
        for k in ("location_enabled", "location_gps_enabled", "location_follow"):
            if k in payload and payload[k] is not None:
                setattr(user, k, bool(payload[k]))
        if "timezone_offset_minutes" in payload:
            user.timezone_offset_minutes = payload["timezone_offset_minutes"]
        # GPS 定位上报（获取地理位置开启时由手机端调用）：存坐标 + 反查城市
        _gps_reported = "location_lat" in payload or "location_lng" in payload
        if _gps_reported:
            _lat = payload.get("location_lat")
            _lng = payload.get("location_lng")
            if _lat is not None and _lng is not None:
                try:
                    user.location_lat = float(_lat)
                    user.location_lng = float(_lng)
                except (TypeError, ValueError):
                    pass
            else:
                user.location_lat = None
                user.location_lng = None
        if user.location_lat is not None and user.location_lng is not None:
            # 城市反查改后台异步（2026-08-16：Nominatim 外呼慢/超时曾阻塞定位响应导致 App 10s Dio 超时）；响应即时返回，城市随后补写
            _lat0, _lng0 = user.location_lat, user.location_lng

            async def _reverse_city(lat: float, lng: float) -> None:
                try:
                    from app.application.weather_service import coords_to_city
                    city = await coords_to_city(lat, lng)
                    if not city:
                        return
                    async with async_session_factory() as _db:
                        _u = (await _db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
                        if _u and _u.location_lat == lat and _u.location_lng == lng:
                            _u.location_city = city
                            await _db.commit()
                except Exception:
                    pass

            _now = time.time()
            if _now - _reverse_throttle.get(user_id, 0.0) >= 300:  # 5 分钟节流
                _reverse_throttle[user_id] = _now
                spawn_background(_reverse_city(_lat0, _lng0))
        # 位置跟随：AI 位置强制与用户位置同步（AI 位置不可自定义）
        if user.location_follow:
            user.ai_location = user.user_location
        # 关闭总开关时清理子项，避免残留授权
        if not user.location_enabled:
            user.location_gps_enabled = False
            user.location_follow = False
            user.user_location = None
            user.ai_location = None
            user.location_lat = None
            user.location_lng = None
            user.location_city = None
        # 关闭获取地理位置时清坐标与反查城市（自定义城市不受影响）
        if not user.location_gps_enabled and not _gps_reported:
            user.location_lat = None
            user.location_lng = None
            user.location_city = None
        await db.commit()
        await db.refresh(user)
    return _to_response(user)


@router.get("/location/weather")
async def get_user_weather(user_id: int = Depends(get_current_user_id)):
    """按用户位置返回当前天气：坐标优先，其次自定义城市名；失败返回 {ok:false}"""
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if user is None or not user.location_enabled:
        return {"ok": False, "reason": "disabled"}
    from app.application.weather_service import get_weather_text
    text = await get_weather_text(
        user.location_lat, user.location_lng,
        user.location_city or user.user_location,
    )
    if text is None:
        return {"ok": False, "reason": "no_data"}
    city = user.location_city or user.user_location or ""
    return {"ok": True, "city": city, "weather": text}
