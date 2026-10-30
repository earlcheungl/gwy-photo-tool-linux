"""GTK4 / libadwaita front-end, modelled on the original MFC dialog."""
from __future__ import annotations

import io
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ASSETS = APP_DIR / "assets"

CSS = """
.preview-card {
    background: alpha(currentColor, 0.06);
    border: 1px solid alpha(currentColor, 0.12);
    border-radius: 14px;
}
.preview-empty { color: alpha(currentColor, 0.45); }
.headline { font-size: 1.45em; font-weight: 800; }
.status-ok { color: #26a269; }
.status-err { color: #e01b24; }
.field-name { opacity: 0.62; }
.field-value { font-weight: 600; }
.hint { opacity: 0.62; font-size: 0.9em; }
.badge {
    border-radius: 999px;
    padding: 3px 12px;
    font-weight: 700;
    font-size: 0.85em;
}
.badge-ok { background: alpha(#26a269, 0.16); color: #26a269; }
.badge-err { background: alpha(#e01b24, 0.14); color: #e01b24; }
"""


def _load_gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

    return Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk


Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk = _load_gtk()

from .config import load_params  # noqa: E402
from .core import encode_output_jpeg, load_cascade, process_file  # noqa: E402

DEFAULT_OUTPUT_NAME = "报名照片.jpg"


def texture_from_bgr(bgr):
    import cv2
    from PIL import Image

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    loader = GdkPixbuf.PixbufLoader()
    loader.write(buf.getvalue())
    loader.close()
    return Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())


class PhotoWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="照片处理工具")
        self.set_default_size(940, 660)
        self.add_css_class("app-window")

        self.params = load_params(ASSETS / "photo.ini")
        try:
            self.cascade = load_cascade(ASSETS / "haarcascade_frontalface_alt2.xml")
        except Exception:  # noqa: BLE001
            self.cascade = None
        self.result = None
        self.current_path: str | None = None
        self._busy = False

        self._build_ui()
        self._install_actions()
        self._install_dnd()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        header = Adw.HeaderBar()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        about_btn = Gtk.Button(icon_name="help-about-symbolic")
        about_btn.set_tooltip_text("关于")
        about_btn.connect("clicked", lambda *_: self.activate_action("about", None))
        header.pack_end(about_btn)

        self.toast_overlay = Adw.ToastOverlay()
        toolbar.set_content(self.toast_overlay)
        self.set_content(toolbar)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=34)
        main.set_halign(Gtk.Align.CENTER)
        main.set_margin_top(22)
        main.set_margin_bottom(22)
        main.set_margin_start(26)
        main.set_margin_end(26)
        self.toast_overlay.set_child(main)

        # ---- left: preview -------------------------------------------------
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left.set_hexpand(False)
        left.set_halign(Gtk.Align.CENTER)
        left.set_valign(Gtk.Align.CENTER)

        self.preview_frame = Gtk.AspectFrame(ratio=295 / 413, obey_child=False)
        self.preview_frame.add_css_class("preview-card")
        self.preview_frame.set_size_request(295, 413)
        self.preview_frame.set_hexpand(False)
        self.preview_frame.set_vexpand(False)

        overlay = Gtk.Overlay()
        self.picture = Gtk.Picture()
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_can_shrink(True)
        overlay.set_child(self.picture)

        self.empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.empty_box.set_halign(Gtk.Align.CENTER)
        self.empty_box.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("preview-empty")
        label = Gtk.Label(label="打开一张 JPG 证件照\n或把照片拖到这里")
        label.set_justify(Gtk.Justification.CENTER)
        label.add_css_class("preview-empty")
        self.empty_box.append(icon)
        self.empty_box.append(label)
        overlay.add_overlay(self.empty_box)

        self.preview_frame.set_child(overlay)
        left.append(self.preview_frame)

        self.caption = Gtk.Label(label="尚未选择照片")
        self.caption.add_css_class("hint")
        left.append(self.caption)

        main.append(left)

        # ---- right: status + details + buttons -----------------------------
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        right.set_size_request(380, -1)
        right.set_hexpand(False)
        right.set_valign(Gtk.Align.CENTER)

        self.badge = Gtk.Label(label="等待照片")
        self.badge.add_css_class("badge")
        self.badge.set_halign(Gtk.Align.START)

        self.status_label = Gtk.Label(label="选择一张照片开始检查")
        self.status_label.set_xalign(0)
        self.status_label.set_wrap(True)
        self.status_label.set_max_width_chars(22)
        self.status_label.add_css_class("headline")

        self.detail_label = Gtk.Label(label="")
        self.detail_label.set_xalign(0)
        self.detail_label.set_wrap(True)
        self.detail_label.set_max_width_chars(42)
        self.detail_label.add_css_class("hint")
        self.detail_label.set_visible(False)

        right.append(self.badge)
        right.append(self.status_label)
        right.append(self.detail_label)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        right.append(sep)

        self.fields = {}
        grid = Gtk.Grid(column_spacing=18, row_spacing=9)
        rows = [
            ("size", "照片尺寸"),
            ("background", "背景颜色"),
            ("face", "人脸区域"),
            ("head", "头部宽度占比"),
            ("crop", "裁剪区域"),
            ("output", "输出文件"),
        ]
        for i, (key, name) in enumerate(rows):
            n = Gtk.Label(label=name)
            n.set_xalign(0)
            n.add_css_class("field-name")
            v = Gtk.Label(label="—")
            v.set_xalign(0)
            v.set_wrap(True)
            v.add_css_class("field-value")
            grid.attach(n, 0, i, 1, 1)
            grid.attach(v, 1, i, 1, 1)
            self.fields[key] = v
        right.append(grid)

        hint = Gtk.Label(
            label=(
                "要求：JPG/JPEG，宽 ≥ 295、高 ≥ 413 像素，白色或蓝色背景，"
                "正面免冠证件照。处理后自动裁剪为 295 × 413。"
            )
        )
        hint.set_xalign(0)
        hint.set_wrap(True)
        hint.set_max_width_chars(42)
        hint.add_css_class("hint")
        right.append(hint)

        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        buttons.set_margin_top(6)

        self.open_btn = Gtk.Button(label="打开照片文件")
        self.open_btn.add_css_class("suggested-action")
        self.open_btn.add_css_class("pill")
        self.open_btn.connect("clicked", lambda *_: self.activate_action("open", None))

        self.save_btn = Gtk.Button(label="保存照片文件")
        self.save_btn.add_css_class("pill")
        self.save_btn.set_sensitive(False)
        self.save_btn.connect("clicked", lambda *_: self.activate_action("save", None))

        quit_btn = Gtk.Button(label="退出")
        quit_btn.add_css_class("flat")
        quit_btn.connect("clicked", lambda *_: self.close())

        buttons.append(self.open_btn)
        buttons.append(self.save_btn)
        buttons.append(quit_btn)
        right.append(buttons)

        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        right.append(self.spinner)

        main.append(right)

    def _install_actions(self):
        actions = [
            ("open", self._on_open),
            ("save", self._on_save),
            ("about", self._on_about),
            ("quit", lambda *_: self.close()),
        ]
        for name, cb in actions:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

        app = self.get_application()
        app.set_accels_for_action("win.open", ["<Control>o"])
        app.set_accels_for_action("win.save", ["<Control>s"])
        app.set_accels_for_action("win.quit", ["<Control>q"])

    def _install_dnd(self):
        try:
            target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        except Exception:  # noqa: BLE001
            return
        target.connect("drop", self._on_drop)
        self.add_controller(target)

    def _on_drop(self, _target, value, _x, _y):
        try:
            files = value.get_files()
        except Exception:  # noqa: BLE001
            return False
        if not files:
            return False
        path = files[0].get_path()
        if path:
            self.load_photo(path)
            return True
        return False

    # ------------------------------------------------------------- actions
    def _jpeg_filter(self):
        f = Gtk.FileFilter()
        f.set_name("JPG/JPEG 照片")
        for pat in ("*.jpg", "*.jpeg", "*.jpe", "*.jfif", "*.JPG", "*.JPEG"):
            f.add_pattern(pat)
        return f

    def _on_open(self, *_args):
        dlg = Gtk.FileDialog(title="打开照片文件")
        f = self._jpeg_filter()
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)
        dlg.set_default_filter(f)
        dlg.open(self, None, self._open_done)

    def _open_done(self, dlg, result):
        try:
            gfile = dlg.open_finish(result)
        except GLib.Error:
            return
        if gfile and gfile.get_path():
            self.load_photo(gfile.get_path())

    def _on_save(self, *_args):
        if not self.result or not self.result.ok:
            return
        dlg = Gtk.FileDialog(title="保存照片", initial_name=DEFAULT_OUTPUT_NAME)
        f = self._jpeg_filter()
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)
        dlg.set_default_filter(f)
        dlg.save(self, None, self._save_done)

    def _save_done(self, dlg, result):
        try:
            gfile = dlg.save_finish(result)
        except GLib.Error:
            return
        if not gfile:
            return
        path = gfile.get_path()
        if not path:
            return
        if not path.lower().endswith((".jpg", ".jpeg", ".jpe", ".jfif")):
            path += ".jpg"
        try:
            data = encode_output_jpeg(self.result.image, self.params)
            Path(path).write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            self.toast_overlay.add_toast(Adw.Toast(title=f"保存失败：{exc}"))
            return
        self.toast_overlay.add_toast(
            Adw.Toast(title=f"已保存 {Path(path).name}（{len(data) / 1024:.1f} KB）")
        )

    def _on_about(self, *_args):
        about = Adw.AboutDialog(
            application_name="照片处理工具",
            application_icon="io.github.gwyphoto.Linux",
            developer_name="Linux port of PhotoProcess 1.0",
            version="1.0.0",
            comments=(
                "国家公务员考试报名照片处理工具的 Linux 移植版。\n"
                "按原程序的规则检查 JPG 格式、像素数、白/蓝背景与人像头部比例，"
                "裁剪为 295 × 413 并以约 10 KB 保存。"
            ),
            license_type=Gtk.License.MIT_X11,
        )
        about.present(self)

    # ------------------------------------------------------------ workflow
    def load_photo(self, path: str):
        if self._busy:
            return
        self._busy = True
        self.current_path = path
        self.spinner.set_visible(True)
        self.spinner.start()
        self.open_btn.set_sensitive(False)
        self.save_btn.set_sensitive(False)
        self.caption.set_text(Path(path).name)
        threading.Thread(target=self._worker, args=(path,), daemon=True).start()

    def _worker(self, path: str):
        if self.cascade is None:
            GLib.idle_add(self._show_error, "无法加载 assets/haarcascade_frontalface_alt2.xml")
            return
        try:
            res = process_file(path, self.params, self.cascade)
        except Exception as exc:  # noqa: BLE001
            GLib.idle_add(self._show_error, str(exc))
            return
        GLib.idle_add(self._show_result, res)

    def _show_error(self, msg: str):
        self._busy = False
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.open_btn.set_sensitive(True)
        self.badge.set_text("出错")
        self.badge.remove_css_class("badge-ok")
        self.badge.add_css_class("badge-err")
        self.status_label.set_text("处理失败")
        self.status_label.remove_css_class("status-ok")
        self.status_label.add_css_class("status-err")
        self.detail_label.set_text(msg)
        self.detail_label.set_visible(True)

    def _show_result(self, res):
        self._busy = False
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.open_btn.set_sensitive(True)
        self.result = res
        image = res.image if res.image is not None else res.source

        if image is not None:
            try:
                self.picture.set_paintable(texture_from_bgr(image))
                self.empty_box.set_visible(False)
            except Exception as exc:  # noqa: BLE001
                self._show_error(f"无法显示图片：{exc}")
                return

        ok = res.ok
        self.badge.set_text("合格" if ok else "不合格")
        self.badge.remove_css_class("badge-ok" if not ok else "badge-err")
        self.badge.add_css_class("badge-ok" if ok else "badge-err")
        self.status_label.set_text(res.message)
        self.status_label.remove_css_class("status-err" if ok else "status-ok")
        self.status_label.add_css_class("status-ok" if ok else "status-err")
        self.detail_label.set_text("")
        self.detail_label.set_visible(False)

        info = res.info
        self.fields["size"].set_text(
            f"{info.get('width', '?')} × {info.get('height', '?')} px"
        )
        bg = info.get("background")
        self.fields["background"].set_text(
            {"white": "白色", "blue": "蓝色"}.get(bg, "不符合要求")
        )
        self.fields["face"].set_text(
            f"{res.face[2]} × {res.face[3]} px" if res.face else "未检测到"
        )
        head = res.ratios.get("head_ratio")
        self.fields["head"].set_text(f"{head * 100:.1f}%" if head is not None else "—")
        self.fields["crop"].set_text(
            f"{res.crop[2]} × {res.crop[3]} px" if res.crop else "—"
        )
        if ok:
            size = len(encode_output_jpeg(res.image, self.params))
            self.fields["output"].set_text(
                f"295 × 413 px，约 {size / 1024:.1f} KB"
            )
            self.save_btn.set_sensitive(True)
        else:
            self.fields["output"].set_text("—")
            self.save_btn.set_sensitive(False)


class PhotoApp(Adw.Application):
    def __init__(self, initial_photo: str | None = None):
        super().__init__(
            application_id="io.github.gwyphoto.Linux",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.window = None
        self.initial_photo = initial_photo

    def do_activate(self):
        if self.window is None:
            self.window = PhotoWindow(self)
        self.window.present()
        if self.initial_photo:
            path, self.initial_photo = self.initial_photo, None
            GLib.timeout_add(150, lambda: (self.window.load_photo(path), False)[1])

    def do_startup(self):
        Adw.Application.do_startup(self)
        try:
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            theme.add_search_path(str(ASSETS / "icons"))
        except Exception:  # noqa: BLE001
            pass
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def run_gui(photo: str | None = None, argv: list[str] | None = None) -> int:
    app = PhotoApp(initial_photo=photo)
    return app.run(argv if argv is not None else [])
