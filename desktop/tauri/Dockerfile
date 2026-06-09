# Build/check environment for the Tauri desktop app.
#
# Debian bookworm ships webkit2gtk-4.1 + glib >= 2.74, which the host distro
# (Alibaba Cloud Linux 3 / EL8) lacks — so this image is the way to compile,
# `cargo check`, or fully package the Linux desktop build locally.
#
# Used by scripts/desktop_in_docker.sh (make desktop-check / desktop-build-linux).
FROM rust:1-bookworm

# Use China mirrors (Aliyun) for Debian apt + npm so dependency downloads don't
# time out from CN networks. bookworm uses the deb822 sources file.
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
      /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
      /etc/apt/sources.list 2>/dev/null || true

# Tauri v2 Linux build dependencies + full-package toolchain (node, python,
# pyinstaller for the sidecar, patchelf/file/fuse for AppImage).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libwebkit2gtk-4.1-dev \
      libgtk-3-dev \
      libayatana-appindicator3-dev \
      librsvg2-dev \
      libssl-dev \
      pkg-config \
      file \
      patchelf \
      libfuse2 \
      curl \
      ca-certificates \
      nodejs \
      npm \
      python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Tauri CLI (standalone `tauri` binary) for `tauri build`, via npm China mirror.
RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g @tauri-apps/cli@^2

# China mirror for crates.io so first-run dependency downloads are fast.
# (config.toml lives at CARGO_HOME root; the registry/ cache can be a volume.)
RUN printf '%s\n' \
  '[source.crates-io]' \
  'replace-with = "rsproxy-sparse"' \
  '[source.rsproxy-sparse]' \
  'registry = "sparse+https://rsproxy.cn/index/"' \
  '[net]' \
  'git-fetch-with-cli = true' \
  > "${CARGO_HOME:-/usr/local/cargo}/config.toml"

WORKDIR /work
