#!/usr/bin/env bash
# 拥爱（AMBRACE）APK 一键打包（Linux/macOS，需自装 Flutter SDK 3.27+ 与 Android SDK）
# 用法：bash scripts/build_apk.sh [release|debug] [输出目录，默认 ~/Downloads]
set -e
cd "$(dirname "$0")/../flutter_app"

BUILD_TYPE="release"
OUT_DIR="${2:-$(cd "$(dirname "$0")/.." && pwd)/output}"
case "${1:-}" in
  debug|--debug) BUILD_TYPE="debug" ;;
esac

echo "[1/3] pub get..."
FLUTTER_CMD="${FLUTTER:-flutter}"
"$FLUTTER_CMD" pub get || { echo "[提示] 联网解析失败，改用国内镜像重试..."; PUB_HOSTED_URL=https://pub.flutter-io.cn FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn "$FLUTTER_CMD" pub get; }

echo "[2/3] build apk --$BUILD_TYPE ..."
"$FLUTTER_CMD" build apk "--$BUILD_TYPE"

echo "[3/3] copy to output..."
mkdir -p "$OUT_DIR"
SRC="build/app/outputs/flutter-apk/app-$BUILD_TYPE.apk"
DST="$OUT_DIR/ai_companion_app-$BUILD_TYPE.apk"
cp "$SRC" "$DST"
SIZE_MB=$(du -m "$DST" | cut -f1)
echo ""
echo "完成！APK 已生成："
echo "  $DST"
echo "  大小：${SIZE_MB} MB"
echo "  安装提示：把 APK 传到安卓手机直接安装"
