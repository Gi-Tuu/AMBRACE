import "package:flutter_test/flutter_test.dart";
import "package:ai_companion/utils/beijing_time.dart";

void main() {
  group("formatInTz", () {
    test("北京时间（默认 offset=8）", () {
      expect(formatInTz("2026-08-12T04:20:00.123"), "2026-08-12 12:20");
    });

    test("东京 UTC+9", () {
      expect(formatInTz("2026-08-12T04:20:00", offset: 9), "2026-08-12 13:20");
    });

    test("伦敦 UTC+0 / 纽约 UTC-5（跨日）", () {
      expect(formatInTz("2026-08-12T04:20:00", offset: 0), "2026-08-12 04:20");
      expect(formatInTz("2026-08-12T04:20:00", offset: -5), "2026-08-11 23:20");
    });

    test("跨日进位", () {
      expect(formatInTz("2026-08-12T23:00:00"), "2026-08-13 07:00");
    });

    test("跨月进位", () {
      expect(formatInTz("2026-08-31T23:00:00"), "2026-09-01 07:00");
    });

    test("跨年进位", () {
      expect(formatInTz("2026-12-31T20:00:00"), "2027-01-01 04:00");
    });

    test("非法/空输入原样返回", () {
      expect(formatInTz(""), "");
      expect(formatInTz("bad"), "bad");
    });

    test("formatBeijingTime 兼容委托", () {
      expect(formatBeijingTime("2026-08-04T12:47:29"), "2026-08-04 20:47");
    });
  });
}
