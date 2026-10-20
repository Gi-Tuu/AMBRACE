// 条件导入入口：web 上返回真·增量适配器，其它平台返回 null（保持 dio 默认适配器）。
export 'xhr_stream_adapter_stub.dart'
    if (dart.library.js_interop) 'xhr_stream_adapter_web.dart';
