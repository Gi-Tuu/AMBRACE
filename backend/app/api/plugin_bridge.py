"""48a 插件桥 API：POST /api/v1/plugins/{name}/bridge（统一入口 + 白名单分发 + ai 限额）。

- 未登录 401（get_current_user_id）；插件不存在 404；未知 api 400；ai 超限 429 + Retry-After；
- 业务错误（AI 不存在 / store 超限 / http 被拒等）以 {"ok": false, "error"} 返回（JS Promise reject）；
- openChat/toast/copy/navigate 为前端能力（Flutter 端直接执行），不经过本端点。
"""
from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.plugins import registry
from app.application.plugin_bridge_service import (
    VALID_APIS,
    bridge_ai_rate_check,
    dispatch,
)
from app.utils.logger import get_logger

_logger = get_logger("api.plugin_bridge")

router = APIRouter(prefix="/api/v1/plugins", tags=["Plugins"])

# 桥 SDK JS（48a）：页面托管端点 GET /{name}/page/plugin-bridge.js 特殊返回同一份文本；
# Flutter WebView 侧注入的插件页也使用同一份模板（见 flutter_app/lib/services/plugin_bridge.dart）。
PLUGIN_BRIDGE_JS = """/* AMBRACE 插件桥 SDK（48a）：window.Ambrace.* → AmbraceBridge.postMessage → Flutter 分发 → __resolve 回传 Promise */
(function (global) {
  'use strict';
  if (global.Ambrace) { return; }
  var seq = 0;
  var pending = {};
  function call(api, params) {
    return new Promise(function (resolve, reject) {
      var id = ++seq;
      pending[id] = { resolve: resolve, reject: reject };
      try {
        global.AmbraceBridge.postMessage(JSON.stringify({ id: id, api: api, params: params || {} }));
      } catch (e) {
        delete pending[id];
        reject(e);
      }
    });
  }
  function resolve(id, ok, value) {
    var p = pending[id];
    if (!p) { return; }
    delete pending[id];
    if (ok) { p.resolve(value); } else { p.reject(new Error((value && value.error) || 'bridge error')); }
  }
  global.Ambrace = {
    call: call,
    ai: function (params) { return call('ai', params || {}); },
    getAiList: function () { return call('getAiList', {}); },
    getAiInfo: function (aiId) { return call('getAiInfo', { aiId: aiId }); },
    openChat: function (aiId) { return call('openChat', { aiId: aiId }); },
    getUserInfo: function () { return call('getUserInfo', {}); },
    store: {
      set: function (key, value) { return call('store.set', { key: key, value: value }); },
      get: function (key) { return call('store.get', key === undefined || key === null ? {} : { key: key }); }
    },
    toast: function (msg) { return call('toast', { msg: msg }); },
    copy: function (text) { return call('copy', { text: text }); },
    navigate: function (url) { return call('navigate', { url: url }); },
    http: function (url, method, data, headers) { return call('http', { url: url, method: method || 'GET', data: data, headers: headers }); }
  };
  global.AmbraceBridge.__resolve = resolve;
})(window);
"""


def _effective_api(api: str, params: dict) -> str:
    """解析实际生效的 api（call 统一入口递归一层）；用于 ai 限额判定"""
    if api == "call":
        inner = str((params or {}).get("api") or "").strip()
        return "" if inner == "call" else inner
    return api


@router.post("/{name}/bridge")
async def plugin_bridge(
    name: str,
    body: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """桥调用：body {api, params}；白名单 api；未登录 401 / 插件不存在 404 / 未知 api 400 / ai 超限 429"""
    body = body or {}
    api = str(body.get("api") or "").strip()
    params = body.get("params")
    if not isinstance(params, dict):
        params = {}
    if api not in VALID_APIS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "bridge_api_unknown", api=api or "(空)"))
    plugin = registry.get_plugin(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_not_found"))
    # ai 限额：每用户每插件 10 次/分、200 次/天（settings 可配；进程内滑动窗口；429 + Retry-After）
    if _effective_api(api, params) == "ai":
        ok, retry_after = bridge_ai_rate_check(user_id, name)
        if not ok:
            raise HTTPException(
                status_code=429, detail=tr_lang(lang, "bridge_ai_rate_limited"),
                headers={"Retry-After": str(retry_after)},
            )
    return await dispatch(name, api, params, user_id, lang)
