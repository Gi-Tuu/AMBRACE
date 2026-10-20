import 'dart:async';
import 'dart:convert';
import 'dart:js_interop';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:web/web.dart' as web;

import '../attachment/upload_progress.dart';
import 'upload_phase.dart';

/// Web 端**增量** HTTP 适配器（M1a-4：补齐真实上行超时相位与 wire 进度）。
///
/// 三件必须成立的事：
/// ① **增量交付**：等 `readyState>=2` 拿到真实 status/headers 才完成 `ResponseBody`，body 自 `LOADING(3)` 起边收边交付。
/// ② **四相位超时模型**（本单核心，替代旧的「sendTimeout 只约束 collect」写法）：
///    * **P0 收集请求体**：`sendTimeout`（`collect().timeout(st)`）；`null`/`Duration.zero` ⇒ 禁用。
///    * **P1 `xhr.send` → `xhr.upload` 完成**：`sendTimeout`（自 `xhr.send` 起算），真实慢上行在此触发
///      `DioExceptionType.sendTimeout` 并 **abort**；bodyless 请求 (无请求体) P1 视为瞬时。
///    * **P2 上传完成 → `HEADERS_RECEIVED`**：`connectTimeout`（**自 P1 结束起算**，故慢上行不会被等待头计时器抢走）；
///      `null`/`zero` ⇒ 禁用。
///    * **P3 headers → `DONE`**：`receiveTimeout` 作**字节空闲**计时（每收一段字节重置）；`null`/`zero` ⇒ 禁用
///      ⇒ SSE（`receiveTimeout: 0`）保持流式。
///    计时决策集中在纯 Dart 的 [XhrUploadPhases]（VM 可单测）。`sendTimeout` 是**分阶段预算**
///    （P0 与 P1 各计一份），不是 P0+P1 的单一总预算。
/// ③ **生命周期**：超时/取消/网络失败/正常完成**只结算一次**；超时**真正 abort** 自己的 XHR；
///    所有定时器与（含新增 `xhr.upload.*` 的）事件监听在结算时清理；`close(force: true)` 中止所有在飞请求。
///
/// **准备期（P0 收集体）**（2026-10-17 返工 R1）：请求在**收集第一段字节之前**就登记进 [_live]，
/// 故 `CancelToken` 与 `adapter.close(force: true)` 在收集期同样**立即**结算——不等 body EOF、
/// 立刻取消请求体订阅（不再攒字节）、不再 `send`、不产生未处理错误。P0 的有限 `sendTimeout`
/// 触发时同样会取消订阅；`Duration.zero`/`null` 仍是**禁用**（不会为过测变相给准备期加时限）。
///
/// **真实上行进度**：只经 [kAmbWireUploadProgressExtraKey] 命名的回调上报（数据源
/// `xhr.upload.onprogress`），**不**消费 dio 的 `onSendProgress`（那是准备期计数，可能在真正
/// 上行前就到 100%）。进度 `100%` / `done:true` 只表示**上传阶段结束**，不代表服务器已处理完毕。
class _XhrIncrementalAdapter implements HttpClientAdapter {
  /// 在飞请求 → 其「立即结算」入口（`close(force:true)` 用；与取消共用同一结算函数）。
  ///
  /// 关键：**登记发生在收集请求体之前**——准备期的请求同样可被 force-close 立即终止，
  /// 不必等 body EOF（返工 R1）。结算时（[detach]）移除，避免泄漏。
  final Map<web.XMLHttpRequest, void Function()> _live =
      <web.XMLHttpRequest, void Function()>{};

  @override
  void close({bool force = false}) {
    if (!force) return;
    // 迭代副本：结算函数内部会从 _live 移除自身。
    for (final entry in _live.entries.toList()) {
      try {
        entry.value(); // 立即结算：abort + 取消请求体订阅 + 清监听/定时器
      } catch (_) {/* 已结束/不可中止：忽略 */}
    }
    _live.clear();
  }

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final xhr = web.XMLHttpRequest();
    final controller = StreamController<Uint8List>();
    /// 只表示「响应头已到达**或**请求已结算」，**不带错误**（错误统一走 [fatal]，
    /// 避免 completer 的错误 future 无人等待而变成未处理错误）。
    final headersSignal = Completer<void>();
    var delivered = 0;
    var closed = false;
    var settled = false;
    DioException? fatal; // 结算错误（正常完成时为 null）
    StreamSubscription<Uint8List>? bodySub; // P0 请求体订阅（结算时取消，不等 EOF）
    Completer<Uint8List?>? bodyDone; // P0 收集结果（结算时以错误完成，防 await 悬挂）

    // 上传进度尽力记录（供 done 事件；`xhr.upload.onprogress` 未必触发）。
    var lastLoaded = 0;
    var lastTotal = 0;

    // 四相位计时器（晚绑定：onTimeout 需要 fail，fail 需要 phases）。
    // `late final`：单一赋值且发生在任何回调可能触发之前（所有 fail 入口都在赋值之后）。
    late final XhrUploadPhases phases;

    // 真实 wire 上行进度回调（命名键，经 options.extra 传入；非该回调即忽略）。
    final rawProgress = options.extra[kAmbWireUploadProgressExtraKey];
    final AmbWireUploadProgress? onWire =
        rawProgress is AmbWireUploadProgress ? rawProgress : null;

    void emitWire(int loaded, int total, bool done) {
      if (onWire == null || settled) return;
      try {
        onWire(AmbUploadProgress(loaded: loaded, total: total, done: done));
      } catch (_) {/* 回调异常不影响传输 */}
    }

    void detach() {
      try {
        xhr.onreadystatechange = null;
        xhr.onprogress = null;
        xhr.onerror = null;
        xhr.ontimeout = null;
        xhr.onabort = null;
        // 新增 upload 监听一并清理（不能只清原 readystatechange/onprogress）。
        final up = xhr.upload;
        up.onloadstart = null;
        up.onprogress = null;
        up.onload = null;
        up.onloadend = null;
        up.onerror = null;
        up.onabort = null;
        up.ontimeout = null;
      } catch (_) {/* 忽略 */}
      _live.remove(xhr);
    }

    DioException mk(DioExceptionType t, String m) =>
        DioException(requestOptions: options, type: t, message: m);

    /// 失败结算：**只做一次**。返工 R1：立即取消请求体订阅（准备期不再攒字节）、
    /// 让收集 future 以同一错误完成（否则 `await` 悬挂）、真正 abort XHR、清理计时器与监听。
    void fail(DioException e) {
      if (settled) return;
      final wasClosed = closed;
      settled = true;
      fatal = e;
      phases.cancelAll();
      // ① 立即取消请求体订阅（准备期取消/force-close 不得继续攒字节）
      final s = bodySub;
      bodySub = null;
      if (s != null) {
        unawaited(s.cancel().catchError((Object _) {}));
      }
      // ② 收集 future 以同一错误完成（否则收集期的 `await` 永远挂着）
      final bd = bodyDone;
      if (bd != null && !bd.isCompleted) bd.completeError(e);
      detach();
      try {
        xhr.abort();
      } catch (_) {/* 忽略 */}
      // ③ 唤醒等待者：头未到 ⇒ 完成 signal（fetch 之后查 [fatal] 抛出）；
      //    头已到且流式中 ⇒ 给流补错误并关闭。
      if (!headersSignal.isCompleted) {
        headersSignal.complete();
      } else if (!wasClosed) {
        closed = true;
        controller.addError(e);
        controller.close();
      }
    }

    phases = XhrUploadPhases(
      sendTimeout: options.sendTimeout,
      connectTimeout: options.connectTimeout,
      receiveTimeout: options.receiveTimeout,
      isSettled: () => settled,
      onTimeout: (type) {
        final msg = switch (type) {
          DioExceptionType.sendTimeout => 'XHR upload send timeout',
          DioExceptionType.connectionTimeout => 'XHR connect timeout',
          DioExceptionType.receiveTimeout => 'XHR receive timeout',
          _ => 'XHR timeout',
        };
        fail(mk(type, msg));
      },
    );

    // 返工 R1：**收集期就登记**「可被 force-close 立即终止」入口（不是 send 之后才登记）。
    _live[xhr] = () => fail(mk(DioExceptionType.cancel, 'XHR force-closed'));

    /// P2→P3 交接：收到响应头。清 connect 计时，进入接收空闲计时。
    void onHeadersReceived() {
      phases.headersReceived();
      if (!headersSignal.isCompleted) headersSignal.complete();
    }

    void emitNewText() {
      if (closed || settled) return;
      if (xhr.readyState < web.XMLHttpRequest.LOADING) return;
      final text = xhr.responseText;
      var fresh = text.substring(delivered);
      delivered = text.length;
      if (fresh.isEmpty) return;
      // 扣留尾部未配对高位代理（跨块 UTF-16/UTF-8 边界保护）
      final last = fresh.codeUnitAt(fresh.length - 1);
      if (last >= 0xD800 && last <= 0xDBFF) {
        delivered -= 1;
        fresh = fresh.substring(0, fresh.length - 1);
      }
      if (fresh.isNotEmpty) {
        controller.add(Uint8List.fromList(utf8.encode(fresh)));
        phases.resetReceive(); // 收到字节 ⇒ 空闲计时重置
      }
    }

    /// P1→P2 交接：上传阶段结束。清 send 计时 + 起 connect 计时 + 上报 done。
    void endUploadPhase() {
      if (phases.uploadDone) return;
      phases.uploadCompleted();
      emitWire(lastLoaded, lastTotal, true);
    }

    /// 正常完成：**只做一次**；先把尾部剩余字节交付完，再结算、清理计时器与监听。
    void finish() {
      if (settled) return;
      if (!closed) {
        emitNewText(); // settled 仍为 false ⇒ 不丢最后一段
        closed = true;
        controller.close();
      }
      settled = true;
      phases.cancelAll();
      final s = bodySub;
      bodySub = null;
      if (s != null) {
        unawaited(s.cancel().catchError((Object _) {}));
      }
      detach();
      if (!headersSignal.isCompleted) {
        fatal = mk(DioExceptionType.connectionError, 'XHR status 0（网络/CORS）');
        headersSignal.complete();
      }
    }

    xhr.open(options.method, options.uri.toString(), true);
    xhr.responseType = 'text';
    options.headers.forEach((k, v) {
      if (v != null) {
        try {
          xhr.setRequestHeader(k, '$v');
        } catch (_) {/* 受保护头由浏览器接管 */}
      }
    });

    xhr.onreadystatechange = (web.Event _) {
      final rs = xhr.readyState;
      if (rs >= web.XMLHttpRequest.HEADERS_RECEIVED && !headersSignal.isCompleted && !settled) {
        if (xhr.status == 0) {
          fail(mk(DioExceptionType.connectionError, 'XHR status 0（网络/CORS）'));
          return;
        }
        // 兜底：header 只能在上传完成后到达——若 upload 事件缺失，这里也把 P1 结算掉。
        endUploadPhase();
        onHeadersReceived();
      }
      if (rs == web.XMLHttpRequest.LOADING) emitNewText();
      if (rs == web.XMLHttpRequest.DONE) finish();
    }.toJS;
    xhr.onprogress = ((web.Event _) => emitNewText()).toJS;
    xhr.onerror =
        ((web.Event _) => fail(mk(DioExceptionType.connectionError, 'XHR error'))).toJS;
    xhr.ontimeout = ((web.Event _) =>
            fail(mk(DioExceptionType.connectionTimeout, 'XHR timeout event')))
        .toJS;
    xhr.onabort = ((web.Event _) => fail(mk(DioExceptionType.cancel, 'aborted'))).toJS;

    // 真实上行进度 + P1 结束信号（数据源 xhr.upload）。
    xhr.upload.onprogress = ((web.Event e) {
      if (settled) return;
      final pe = e as web.ProgressEvent;
      final total = pe.lengthComputable ? pe.total : 0;
      lastLoaded = pe.loaded;
      lastTotal = total;
      emitWire(pe.loaded, total, false);
    }).toJS;
    xhr.upload.onload = ((web.Event _) => endUploadPhase()).toJS;
    xhr.upload.onloadend = ((web.Event _) => endUploadPhase()).toJS;

    cancelFuture?.then((_) {
      // 未发出也会走到 fail：立即结算一次、不再 send、无孤立错误。
      fail(mk(DioExceptionType.cancel, 'cancelled'));
    });

    // P0：请求体先收集，受 sendTimeout 约束（分阶段预算）：再发送。
    // 收集期同样可被 cancel/force-close 立即终止（fail 会取消订阅并以错误完成 bodyDone）。
    final body = requestStream;
    Uint8List? payload;
    if (body != null) {
      final collected = Completer<Uint8List?>();
      final chunks = <int>[];
      bodySub = body.listen(
        chunks.addAll,
        onError: (Object e, StackTrace st) {
          if (!collected.isCompleted) collected.completeError(e, st);
        },
        onDone: () {
          if (!collected.isCompleted) {
            collected.complete(chunks.isEmpty ? null : Uint8List.fromList(chunks));
          }
        },
        cancelOnError: false,
      );
      bodyDone = collected;
      final st = phases.sendBudget;
      try {
        payload = st != null
            ? await collected.future.timeout(st)
            : await collected.future;
      } on TimeoutException {
        // 有限准备超时：结算（fail 内已取消订阅，不残留监听）
        fail(mk(DioExceptionType.sendTimeout, 'XHR send timeout'));
      } on DioException catch (e) {
        // R1b：DioException 有**两个来源**，必须按「是否已结算」区分，不能按异常类型判定：
        // ① 本适配器的 fail 结算（cancel/force-close/timeout）会以同一错误完成 bodyDone
        //    ⇒ settled == true，吞掉即可，由 :313 统一 throw fatal（**只结算一次**，
        //    且不得把已结算的 cancel 覆盖成 connectionError）；
        // ② **请求体流自身**抛的 DioException（cancel/sendTimeout/connectionError…）
        //    ⇒ settled 仍为 false、payload 仍为 null。若在此吞掉，会继续落到 xhr.send()
        //    发出**空请求**、并把正常响应当成功（R1b 漏洞）。故必须立即结算：
        //    取消订阅、abort、清理定时器/监听，并**保留原异常**（类型即真实失败原因）。
        if (!settled) {
          fail(e);
        }
      } catch (e) {
        // 请求体流自身异常
        if (!settled) {
          fail(mk(DioExceptionType.connectionError,
              'XHR request body stream error: $e'));
        }
      } finally {
        bodyDone = null;
      }
    }
    if (settled) throw fatal ?? mk(DioExceptionType.cancel, 'settled');

    if (payload == null) {
      xhr.send();
      // bodyless：无上传相位，P1 视为瞬时 ⇒ 直接进 P2。
      endUploadPhase();
    } else {
      xhr.send(payload.toJS);
      // P1：自 send 起算的 sendTimeout（真实上行 deadline）。
      phases.beginSend();
    }

    await headersSignal.future; // **等真实响应头**（或超时/取消/网络失败的结算）
    if (fatal != null) throw fatal!;

    final headers = <String, List<String>>{};
    for (final line in xhr.getAllResponseHeaders().split('\r\n')) {
      final idx = line.indexOf(':');
      if (idx > 0) {
        final k = line.substring(0, idx).trim().toLowerCase();
        headers.putIfAbsent(k, () => <String>[]).add(line.substring(idx + 1).trim());
      }
    }
    return ResponseBody(controller.stream, xhr.status, headers: headers);
  }
}

/// Web：返回真·增量适配器。
HttpClientAdapter? createIncrementalStreamAdapter() => _XhrIncrementalAdapter();

/// Web 平台支持增量流。
bool get supportsIncrementalStream => true;
