# 照片处理工具 · Linux 移植版

国家公务员考试报名照片处理工具（`照片处理工具.exe`，PhotoProcess 1.0）的 Linux 重实现。
按原程序的规则检查 JPG 格式、像素数、白色/蓝色背景与人像头部比例，自动裁剪并输出
**295 × 413**、约 **10 KB** 的 `报名照片.jpg`。

- 图形界面：GTK4 + libadwaita（原生 Linux 窗口，支持拖放）
- 命令行：`gwy-photo photo.jpg -o 报名照片.jpg`
- 输出与原版一致：295×413、JPEG quality 40、结尾追加原版 10 字节标记
- 逆向分析细节见 [ANALYSIS.md](ANALYSIS.md)

## 依赖

- Linux 桌面（GTK4 + PyGObject，Arch: `gtk4`、`python-gobject`）
- Python ≥ 3.10
- `uv`（推荐）或带 pip 的 Python，用于创建虚拟环境
- 首次安装需要联网下载 `opencv-python-headless<5` 与 `pillow`

> 固定 OpenCV 4.x：OpenCV 5 的 Python 轮子已移除 `CascadeClassifier`，无法加载
> 原版 2005 年的 Haar 级联。

## 快速开始

```bash
cd gwy-photo-tool-linux
./install.sh
```

`install.sh` 会创建 `.venv`（复用系统 PyGObject/GTK4），安装
`opencv-python-headless<5` 与 `pillow`，并注册应用菜单项。

```bash
# 图形界面
gwy-photo --gui
gwy-photo --gui 照片.jpg

# 命令行
gwy-photo 照片.jpg                      # 只检查，不保存
gwy-photo 照片.jpg -o 报名照片.jpg       # 检查并保存
gwy-photo 照片.jpg --json               # 机器可读输出
```

不安装也可以直接运行：

```bash
./bin/gwy-photo 照片.jpg -o 报名照片.jpg
```

## 检查规则

| 步骤 | 规则 | 不通过时的提示 |
|---|---|---|
| 1 | 文件头必须是 `FF D8`（JPEG） | 类型不是 JPG/JPEG |
| 2 | 宽 ≥ 295 且高 ≥ 413 | 像素数低 |
| 3 | 三处背景取样必须是纯白或纯蓝 | 背景不符合要求 |
| 4 | Viola-Jones 人脸检测（alt2 级联） | 未检测到正面人像 |
| 5 | 人脸宽度 / 照片宽度 ≥ 0.32 | 头部比例不符合要求 |
| 6 | 人脸左右/上边距在裁剪框的 0 ~ 0.5 之间 | 头部比例不符合要求 |

背景判定使用 HSI：

```
白底：I ≥ i_max 且 S ≤ s_min
蓝底：I ≥ i_min 且 2.5 ≤ H ≤ 4.5 且 S ≥ s_max
```

三处取样都得到同一结论才算通过（白底或蓝底均可）。

## 输出

- 尺寸：`photo.ini` 中的 `dst_width × dst_height`，默认 **295 × 413**
- 编码：先按 OpenCV 默认质量存一次，再用 libjpeg 以 **quality 40** 重编码
- JFIF 头：**96 × 96 DPI**（与原版 CxImage 输出一致）
- 文件尾：追加 `FF 02 00 08 67 6A 67 77 79 32`（即 `\xff\x02\x00\x08gjgwy2`）
- 默认文件名：`报名照片.jpg`

## 参数文件

`assets/photo.ini` 与原版一致：

```ini
[PhotoInfo]
s_min=0.05
s_max=0.1
i_min=0.1
i_max=0.7
```

其余参数取程序内置默认值（`min_width=295`、`min_height=413`、`dst_width=295`、
`dst_height=413`、`blue_h_min/max=2.5/4.5`、`ratio_x/y_min/max=0.0/0.5` 等），
可用 `--ini` 指定自定义文件。

## 目录结构

```
gwy-photo-tool-linux/
├── bin/gwy-photo                 启动脚本
├── gwy_photo/
│   ├── config.py                 photo.ini 解析
│   ├── core.py                   校验、背景、人脸、裁剪、保存
│   ├── cli.py                    命令行
│   └── gui.py                    GTK4 界面
├── assets/
│   ├── haarcascade_frontalface_alt2.xml   原版 2005 年分类器
│   ├── photo.ini
│   └── icons/...
├── tests/test_core.py
├── install.sh
└── gwy-photo.desktop
```

## 已知差异

- 界面是原生 GTK4 重写，不是原版位图皮肤；处理逻辑与判定规则保持一致。
- 原版用 OpenCV 1.0 的 `cvHaarDetectObjects`；这里用 OpenCV 4.x 的
  `CascadeClassifier`，参数相同，个别照片的检测框可能相差几个像素。
- 原版不读取 EXIF 方向；这里同样忽略 EXIF（`IMREAD_IGNORE_ORIENTATION`），
  手机照片若方向不对，请先旋转。
- JPEG 编码使用系统 libjpeg-turbo，文件大小与 IJG libjpeg 会有几个百分点的差异。
- 原版保存时的 10 字节标记一并保留，便于与报名网站的校验逻辑兼容。
- 已用一张 Windows 官方工具处理出的样本逐段比对：APP0（96 DPI）、DQT（q40 标准表）、
  SOF0（295×413、4:2:0）、DHT（标准表）、SOS 与尾部标记全部一致，仅熵编码数据因
  图像内容和 libjpeg 版本不同而不同。
- 用同一张原图做端到端对比：输出 7066 B（官方 7054 B），PSNR 41.43 dB，
  像素零位移，非熵编码段全部逐字节相同。

## 测试

```bash
.venv/bin/python tests/test_core.py
```

## 许可说明

- 本项目代码为 MIT。
- `assets/haarcascade_frontalface_alt2.xml` 来自 OpenCV（Intel 2005，BSD-3-Clause），
  随原工具一同分发，这里保留以保证检测结果一致。
- 原 `照片处理工具.exe` 的著作权归其作者所有；本项目是行为兼容的独立重实现，
  不包含原程序的二进制代码。

## 免责声明

本项目用于在 Linux 上处理**本人**的报名照片，行为兼容原版工具，仅供个人使用。
考试照片要求（含“不得美化、修饰”）以考试机构的最新规定为准；使用本工具产生的
任何后果由使用者自行承担。
