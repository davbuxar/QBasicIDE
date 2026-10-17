[app]
title = QBasic IDE
package.name = qbasicide
package.domain = org.qbasic
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,bas
version = 1.0
requirements = python3,kivy==2.3.0
orientation = all
fullscreen = 0
android.permissions = WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE
android.api = 33
android.minapi = 21
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
