"""用户八维可视化状态 API（用户主页蛛网图：GET 读取 / PUT 用户手动滑动调整）"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.models.user import UserState
from app.schemas.user_state import UserStateResponse, UserStateUpdate
from app.utils.clamp import clamp_int

router = APIRouter(prefix="/api/v1/users", tags=["User States"])

_DIMS = ["mood", "body_temp", "desire", "possessiveness", "fatigue", "sensitivity", "comfort", "anger"]


def _clamp(v) -> int:
    return clamp_int(v, default=50)


@router.get("/states", response_model=UserStateResponse)
async def get_user_states(user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        result = await db.execute(select(UserState).where(UserState.user_id == user_id))
        st = result.scalar_one_or_none()
        if st is None:
            st = UserState(user_id=user_id)
            db.add(st)
            await db.commit()
            await db.refresh(st)
        data = {k: getattr(st, k) for k in _DIMS}
        updated_at = st.updated_at
    return {"user_id": user_id, **data, "updated_at": updated_at}


@router.put("/states", response_model=UserStateResponse)
async def update_user_states(data: UserStateUpdate, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        result = await db.execute(select(UserState).where(UserState.user_id == user_id))
        st = result.scalar_one_or_none()
        if st is None:
            st = UserState(user_id=user_id)
            db.add(st)
        payload = data.model_dump(exclude_unset=True)
        for k in _DIMS:
            if k in payload and payload[k] is not None:
                setattr(st, k, _clamp(payload[k]))
        await db.commit()
        await db.refresh(st)
        data_out = {k: getattr(st, k) for k in _DIMS}
        updated_at = st.updated_at
    return {"user_id": user_id, **data_out, "updated_at": updated_at}
