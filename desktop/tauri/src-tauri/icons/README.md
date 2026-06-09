# Icons

These desktop icons are committed and generated from `../app-icon.svg`.

To regenerate after changing the source SVG (run from `src-tauri/`):

```bash
cargo tauri icon app-icon.svg -o icons
```

This produces `32x32.png`, `128x128.png`, `128x128@2x.png`, `icon.icns`,
`icon.ico` (matching `bundle.icon` in `../tauri.conf.json`) plus Windows Store
logos. The `android/` and `ios/` variants it also emits are git-ignored (this is
a desktop-only build).
