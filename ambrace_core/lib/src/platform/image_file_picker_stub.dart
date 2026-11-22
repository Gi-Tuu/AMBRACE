import 'image_file_picker_types.dart';

ImageFilePicker createPlatformImageFilePicker() => _StubImageFilePicker();

class _StubImageFilePicker implements ImageFilePicker {
  @override
  Future<PickedImageFile?> pick() async => null;
}
