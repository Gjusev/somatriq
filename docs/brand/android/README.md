# Android launcher icon source pack

These files are the versioned adaptive-icon source pack for the separate `Gjusev/noop-somatriq` mobile fork. The fork is intentionally not vendored into the Somatriq server repository (ADR 0004). The matching launcher resources were applied to the working fork on 2026-09-09 and validated with `:app:assembleFullDebug`.

The source pack maps to `android/app/src/main/res/` in the mobile repository:

```text
drawable/somatriq_launcher_background.xml
drawable/somatriq_launcher_foreground.xml
drawable/somatriq_launcher_monochrome.xml
mipmap-anydpi-v26/ic_launcher.xml
mipmap-anydpi-v26/ic_launcher_navy.xml
mipmap-anydpi-v26/ic_launcher_round.xml
mipmap-anydpi-v33/ic_launcher.xml
mipmap-anydpi-v33/ic_launcher_navy.xml
mipmap-anydpi-v33/ic_launcher_round.xml
```

The names match the current fork's launcher resources, including its optional `IconNavy` activity alias. The default alias uses the canonical teal mark; the navy alias retains the same geometry with the approved blue palette. The fork has no density-specific legacy launcher files, so API 26+ devices resolve the adaptive assets directly.

The v26 assets provide the two-layer adaptive icon. The v33 variants add the single-color themed icon. Verify circle, squircle and rounded-square masks in Android Studio before release.

The fork already exposes `Somatriq` through its application label and rebrand lint while keeping internal package/class names unchanged. That separation preserves compatibility with the NOOP-derived engine without leaking the inherited name into normal product UI.
