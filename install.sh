#!/usr/bin/env bash
# Install the Linux port into the current user account.
set -euo pipefail

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$HERE"

echo "==> 创建 Python 虚拟环境（需要系统 PyGObject/GTK4）"
if [ ! -x .venv/bin/python ]; then
    if command -v uv >/dev/null 2>&1; then
        uv venv --python /usr/bin/python3 --system-site-packages .venv
    else
        /usr/bin/python3 -m venv --system-site-packages .venv
    fi
fi
if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python "opencv-python-headless<5" pillow
else
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install "opencv-python-headless<5" pillow
fi

echo "==> 安装图标与桌面入口"
mkdir -p "$HOME/.local/bin" \
         "$HOME/.local/share/applications" \
         "$HOME/.local/share/icons/hicolor/scalable/apps"
ln -sf "$HERE/bin/gwy-photo" "$HOME/.local/bin/gwy-photo"
install -m 644 "$HERE/assets/icons/hicolor/scalable/apps/io.github.gwyphoto.Linux.svg" \
    "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.gwyphoto.Linux.svg"
sed "s|@EXEC@|$HERE/bin/gwy-photo|g" "$HERE/gwy-photo.desktop" \
    > "$HOME/.local/share/applications/gwy-photo.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$HOME/.local/share/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

echo
echo "安装完成："
echo "  图形界面：gwy-photo --gui  （或从应用菜单启动“照片处理工具”）"
echo "  命令行：  gwy-photo 你的照片.jpg -o 报名照片.jpg"
