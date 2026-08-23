import 'dart:async';
import 'dart:typed_data';

import 'package:crop_your_image/crop_your_image.dart';
import 'package:flutter/material.dart';

/// 头像裁剪页：圆形裁剪区域，可拖动/双指缩放，确定后返回裁剪结果 bytes。
/// 用法：Navigator.push 携带 AvatarCropScreen(imageBytes: bytes)，返回 Uint8List；
/// 取消返回 null。
class AvatarCropScreen extends StatefulWidget {
  final Uint8List imageBytes;

  const AvatarCropScreen({super.key, required this.imageBytes});

  @override
  State<AvatarCropScreen> createState() => _AvatarCropScreenState();
}

class _AvatarCropScreenState extends State<AvatarCropScreen> {
  final _controller = CropController();
  bool _cropping = false;
  bool _ready = false;
  Timer? _timeout;

  void _confirm() {
    if (_cropping) return;
    if (!_ready) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('图片加载中，请稍候再试')),
      );
      return;
    }
    setState(() => _cropping = true);
    // 兜底：裁剪回调 15 秒内未返回则恢复按钮，避免永久卡在「处理中…」
    _timeout = Timer(const Duration(seconds: 15), () {
      if (!mounted) return;
      setState(() => _cropping = false);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('裁剪超时，请重试')),
      );
    });
    _controller.crop();
  }

  @override
  void dispose() {
    _timeout?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        elevation: 0,
        scrolledUnderElevation: 0,
        leading: TextButton(
          onPressed: _cropping ? null : () => Navigator.of(context).pop(),
          child: const Text('取消',
              style: TextStyle(color: Colors.white70, fontSize: 16)),
        ),
        centerTitle: true,
        title: const Text('调整头像',
            style: TextStyle(
                color: Colors.white, fontSize: 17, fontWeight: FontWeight.w600)),
        actions: [
          TextButton(
            onPressed: _cropping ? null : _confirm,
            child: Text(
              _cropping ? '处理中…' : '确定',
              style: const TextStyle(
                  color: Colors.white,
                  fontSize: 16,
                  fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
      body: Stack(
        children: [
          Positioned.fill(
            child: Crop(
              image: widget.imageBytes,
              controller: _controller,
              onStatusChanged: (status) {
                if (status == CropStatus.ready && mounted) {
                  setState(() => _ready = true);
                }
              },
              onCropped: (result) {
                _timeout?.cancel();
                if (result is CropSuccess) {
                  Navigator.of(context).pop(result.croppedImage);
                } else if (mounted) {
                  setState(() => _cropping = false);
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('裁剪失败，请重试')),
                  );
                }
              },
              aspectRatio: 1,
              withCircleUi: true,
              interactive: true,
              baseColor: Colors.black,
              maskColor: Colors.black54,
              radius: 6,
              progressIndicator:
                  const CircularProgressIndicator(color: Colors.white),
              cornerDotBuilder: (size, alignment) => Container(
                width: size,
                height: size,
                decoration: const BoxDecoration(
                    color: Colors.white, shape: BoxShape.circle),
              ),
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: MediaQuery.of(context).padding.bottom + 20,
            child: IgnorePointer(
              child: Text(
                '拖动调整位置 · 双指缩放',
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white70, fontSize: 12),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
