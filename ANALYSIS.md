# PhotoProcess 1.0 逆向分析

本文记录对 `照片处理工具.exe`（PhotoProcess 1.0，2014 年版，2024-10 更新）的静态分析，
以及 Linux 移植版中每条规则的来源。

## 1. 压缩包构成

| 文件 | 作用 |
|---|---|
| `照片处理工具.exe` | 主程序，PE32 GUI，MSVC 6.0 / MFC42 |
| `cv100.dll`、`cxcore100.dll`、`cvaux100.dll`、`highgui100.dll`、`ml100.dll`、`cvcam100.dll`、`cxts001.dll` | OpenCV 1.0（2006-10-18 构建） |
| `haarcascade_frontalface_alt2.xml` | 2005-03 的正面人脸 Haar 级联 |
| `haarcascade_frontalface_alt_tree.xml` | 备用级联（主程序未使用） |
| `photo.ini` | 判定阈值 |
| `interface00.bmp` | 701×488 界面皮肤位图 |
| `使用说明.doc` | 使用说明 |

主程序只导入了 2 个 highgui 函数（`cvLoadImage`、`cvSaveImage`）、4 个 cv 函数
（`cvHaarDetectObjects`、`cvResize`、`cvCvtColor`、`cvReleaseHaarClassifierCascade`）
和 9 个 cxcore 函数。除此之外还静态链接了 **CxImage**（含 libjpeg、libpng、libtiff、
JasPer、dcraw），这解释了 9.9 MB 的体积，以及为什么最终 JPEG 会由 CxImage 重新编码。

PE 信息：ImageBase `0x400000`，`.text` 从 `0x401000` 开始；**程序自身逻辑只占
`0x401000`–`0x403520` 约 9 KB**，其余是 MFC/CxImage/CRT 代码。

## 2. 分析工具与方法

```bash
objdump -d -M intel tool.exe
retdec-decompiler --select-ranges 0x401000-0x403520 tool.exe
analyzeHeadless ... DumpRange.java 0x401000 0x403520   # Ghidra 11.4.2
```

Ghidra 对 x87 浮点栈的重建明显好于 RetDec，本文的浮点细节以反汇编 + Ghidra 交叉核对为准。
字符串用 GBK 解码；INI 键名、错误提示、文件名都在 `.data` 段（`0x5d2000` 起）。

## 3. 函数地图（程序自身代码）

| 地址 | 作用 |
|---|---|
| `0x401000` | 背景颜色分类器：输入 H、S、I 和阈值，返回 0=白底 / 1=蓝底 / 3=不合格 |
| `0x401070` | 背景取样：取 3 个 3×3 小块，转 HSI，调用 `0x401000` |
| `0x4013a0` | 主流程：读 INI → 尺寸检查 → 背景 → 人脸 → 头部比例 → 裁剪 → 缩放 |
| `0x401d60` | 打开照片：判断 JPEG 后调用主流程 |
| `0x401e10` | 在文件末尾追加 10 字节标记 |
| `0x401e90` | 读取文件头 2 字节，判断是否为 `FF D8` |
| `0x401f10` | 用 CxImage 以 quality 40 重新编码 JPEG |
| `0x402c10` | “打开照片文件”按钮处理（含界面皮肤、预览合成） |
| `0x403010` | “保存照片文件”按钮处理 |
| `0x4024d0` 等 | 位图皮肤绘制、窗口初始化 |

## 4. 参数（photo.ini 与内置默认值）

程序用 `GetPrivateProfileStringA("PhotoInfo", key, default, ...)` 读取，
键名和默认值都硬编码在 `.data`：

| 键 | 代码默认值 | 随包 photo.ini | 含义 |
|---|---|---|---|
| `min_width` | 295 | — | 最小宽度 |
| `min_height` | 413 | — | 最小高度 |
| `dst_width` | 295 | — | 输出宽度 |
| `dst_height` | 413 | — | 输出高度 |
| `ratio_x_min` / `ratio_x_max` | 0.0 / 0.5 | — | 人脸左右边距允许范围 |
| `ratio_y_min` / `ratio_y_max` | 0.0 / 0.5 | — | 人脸上边距允许范围 |
| `blue_h_min` / `blue_h_max` | 2.5 / 4.5 | — | 蓝色背景色相范围（弧度） |
| `red_h_min` / `red_h_max` | 0.5 / 5.0 | — | 读取但未参与判定（死参数） |
| `i_min` / `i_max` | 0.1 / **0.9** | **0.7** | 亮度范围 |
| `s_min` / `s_max` | 0.05 / **0.3** | **0.1** | 饱和度范围 |

随包 ini 把 `s_max` 从 0.3 收紧到 0.1、`i_max` 从 0.9 收紧到 0.7。

## 5. 校验顺序与返回码

`FUN_00401d60` 先调用 `FUN_00401e90`：读取文件前两字节，等于 `FF D8` 返回 2，
否则返回 0 → 主程序返回 **1**（类型错误）。是 JPEG 时用 `cvLoadImage(path, 1)`
加载彩色图，进入 `FUN_004013a0`。

| 返回码 | 触发条件 | 原版提示 |
|---|---|---|
| 0 | 通过 | 照片符合要求 |
| 1 | 不是 JPEG / 级联加载失败 | 类型不是 JPG/JPEG 格式 |
| 2 | `width < 295` 或 `height < 413` | 像素数低 |
| 3 | 背景不合格 | 背景不符合要求 |
| 4 | 人脸太小或边距不合格 | 人像头部比例不符合要求 |
| 5 | 未检测到人脸 | 不符合要求，请选择正面人像证件照 |
| 7 | 文件能加载但未处理（打开流程内部用） | — |

注意：判断类型只看文件头，**不看扩展名**。

## 6. 背景检测（0x401070 / 0x401000）

### 6.1 取样点

图像宽 `w`、高 `h`，整数除法：

```
P0 = (w/10, h/10)
P1 = (w/10, h/4)
P2 = (9w/10, h/6)
```

以每个点为中心取 3×3 像素（BGR），分别求和后：

```
B = sum_B / 2295, G = sum_G / 2295, R = sum_R / 2295
```

`2295 = 9 × 255`，即把平均值归一化到 0–1。

### 6.2 转 HSI

```
I = (R + G + B) / 3
S = 1 - 3 * min(R, G, B) / (R + G + B)
H = acos( 0.5 * ((R-G) + (R-B)) / sqrt((R-G)^2 + (R-B)(G-B)) )
if (G < B) H = 6.28318 - H
```

`6.28318` 是二进制中写死的常量（不是精确 2π）。

### 6.3 分类

```
if (I >= i_min) {
    if (I >= i_max && S <= s_min) return 0;                 // 白底
    if (blue_h_min <= H <= blue_h_max && S >= s_max) return 1; // 蓝底
}
return 3;                                                    // 不合格
```

三个取样点必须得到**同一个** 0 或 1，否则整体判为 3。
这解释了两个现象：纯色背景很容易通过；而人脸/头发靠近取样点、或渐变背景会失败。

## 7. 人脸检测

```c
cvCvtColor(color, gray, CV_BGR2GRAY);
cvHaarDetectObjects(gray, cascade, storage,
                    scale_factor=1.1,
                    min_neighbors=2,
                    flags=CV_HAAR_DO_CANNY_PRUNING (=1),
                    min_size=cvSize(30, 30));
```

级联文件：程序目录下的 `haarcascade_frontalface_alt2.xml`（`cvLoad` 加载）。
检测到多张脸时，**取宽度最大的一个**（不是面积最大）。

## 8. 头部比例与裁剪

设人脸矩形 `(fx, fy, fw, fh)`，图像宽 `w`。

1. 头部宽度占比：`fw / w` 必须 **≥ 0.32**，否则返回 4。
2. 裁剪宽度：

   ```
   crop_w = trunc(fw * 1.6181229773462784)
   ```

   这个常数就是 `1 / 0.618`：让头部宽度约占画面宽度的 61.8%（黄金比例）。

3. 裁剪高度：

   ```
   crop_h = trunc(crop_w * dst_height / dst_width + 0.5)
   ```

4. 左上角：

   ```
   x0 = fx - (crop_w - fw) / 2
   y0 = fy - (crop_h - fh) / 2
   ```

   若裁剪框大于图像，则贴边并缩到图像尺寸；否则把 `x0/y0` 夹到 `[0, w-crop_w]`。

5. 长宽比修正（裁剪框与目标比例误差超过 ±10% 时）：

   ```
   r = (crop_h * dst_width) / (crop_w * dst_height)
   if r < 0.9:  crop_w = trunc(crop_h * dst_width / dst_height);  x0 += (old_w - new_w)/2
   if r > 1.1:  crop_h = trunc(crop_w * dst_height / dst_width);  y0 += (old_h - new_h)/2
   ```

6. 边距检查（都是相对裁剪框的比例）：

   ```
   left  = (fx - x0) / crop_w
   right = (crop_w - (fx - x0) - fw) / crop_w
   top   = (fy - y0) / crop_h
   ```

   三者都必须落在 `ratio_x_min..ratio_x_max` / `ratio_y_min..ratio_y_max`（默认 0–0.5）。
   不满足返回 4。

7. 裁剪并缩放：

   ```c
   cvSetImageROI(img, cvRect(x0, y0, crop_w, crop_h));
   dst = cvCreateImage(cvSize(dst_width, dst_height), 8, 3);
   cvResize(img, dst, CV_INTER_LINEAR);
   cvResetImageROI(img);
   ```

最终得到的就是 **295 × 413** 的 BGR 图。

## 9. 保存流程（0x403010）

1. `CFileDialog` 保存对话框，默认文件名 `报名照片.jpg`，过滤器
   `jpg(*.jpg;*.jpeg;*.jpe;*.jfif)|...`。
2. 若路径扩展名不是 `.jpg`（比较最后 4 个字符），自动追加 `.jpg`。
3. `cvSaveImage(path, img)`。
4. `FUN_00401f10(path, 40.0f)`：CxImage 载入该 JPEG，`SetJpegQuality(40)`，再存回原路径。
   所以最终 JPEG 是 **libjpeg quality 40**。
5. `FUN_00401e10(path)`：以 `rb+` 打开，`fseek(END)`，追加 10 字节：

   ```
   FF 02 00 08 67 6A 67 77 79 32      // \xff\x02\x00\x08gjgwy2
   ```

   这 10 字节位于 JPEG EOI 之后，普通解码器会忽略，但报名网站可能用它判断
   “照片已由官方工具处理”。
6. 释放图像，弹框“照片已保存成功，是否退出”，选“是”关闭程序。

输出大小实测在 **8–20 KB**，与说明文档“10K 左右”一致。

## 10. 界面行为

- 主窗口是皮肤化的 `CDialog`，背景 `interface00.bmp`（701×488）。
- “打开照片文件”选择 JPG 后校验，通过则把处理结果显示在预览区；
  失败则用 `MessageBox` 显示对应提示。
- “保存照片文件”只有在存在处理结果时才保存。
- 打开流程里还会把处理结果缩放到 175×228，贴到界面皮肤 `interface00.bmp`
  的 `(127,182)` 预览槽位，生成界面预览图；这不会影响最终保存的 295×413 照片。

## 11. Linux 移植的对应实现

| 原版 | 移植版（`gwy_photo/core.py`） |
|---|---|
| `cvLoadImage` | `cv2.imread(..., IMREAD_COLOR \| IMREAD_IGNORE_ORIENTATION)` |
| `cvCvtColor` | `cv2.cvtColor(..., COLOR_BGR2GRAY)` |
| `cvHaarDetectObjects` | `cv2.CascadeClassifier.detectMultiScale`（同参数、同级联） |
| `cvSetImageROI` + `cvResize` | NumPy 切片 + `cv2.resize(INTER_LINEAR)` |
| `cvSaveImage` | `cv2.imencode(quality=95)` |
| CxImage q40 | Pillow(`libjpeg-turbo`) `quality=40` |
| 追加标记 | `MARKER = b"\xff\x02\x00\x08gjgwy2"` |

`photo.ini`、Haar 级联、阈值、返回码、提示语、标记字节全部沿用原版。

## 12. 与官方输出的逐段对比

拿到一张由 Windows 原版工具处理出的成品后做了字节级解析：

| 段 | 原版输出 | 移植版 |
|---|---|---|
| 文件大小 | 7054 B | 内容相关（同参数） |
| APP0 | `JFIF 1.01`，units=1，96×96 dpi，无缩略图 | 已改为完全一致 |
| DQT ×2 | 标准 IJG q40 表 | 逐字节相同 |
| SOF0 | 8-bit，295×413，3 分量，0x22/0x11/0x11（4:2:0） | 逐字节相同 |
| DHT ×4 | 标准 Huffman 表（未优化） | 逐字节相同 |
| SOS | 3 分量，baseline，Ss=0x3f | 逐字节相同 |
| EOI 之后 | `FF 02 00 08 67 6A 67 77 79 32` | 逐字节相同 |

也就是说：**除了熵编码后的像素数据（取决于图像内容和 libjpeg 实现）之外，文件结构完全一致**。
这次对比还发现并修正了 JFIF 密度：原版是 **96×96 dpi（units=1）**，最初移植版写的是 1×1 dpi。

另外，这张官方成品只有 **7054 字节**，说明“10K 左右”只是约数，网站并不会强制 ≥10 KB。

对这张成品再跑一次 alt2 人脸检测：人脸框 186×186，`186 / 295 = 0.631`，与设计的
`1 / 1.618 = 0.618` 只差 2%（检测框、整数取整与缩放插值造成），从侧面验证了
“裁剪宽度 = 人脸宽 × 1.618”这条规则。

## 13. 端到端复现验证（同一张原图）

测试样本 A：295 × 414，13840 字节，无 JFIF 头、无尾部标记。

移植版处理结果：

```
背景：白色
人脸：约 187 × 187，头部占比 0.634
裁剪：(0,0,295,414)   ← 原图只比目标高 1 px，裁剪框被夹到整幅图
输出：295 × 413，7066 字节
```

与官方成品（295 × 413，7054 字节）对比：

| 指标 | 结果 |
|---|---|
| 文件大小 | 7066 vs 7054（+12 B，0.17%） |
| 像素平均绝对误差 | 0.87 / 255 |
| PSNR | **41.43 dB** |
| 垂直位移 | 0 px 时 MSE 4.68；±1 px 时约 97，确认像素对齐 |
| 人脸框 | 官方 186×186，移植版 185×185（位置相差 1 px） |
| APP0/DQT/SOF0/DHT/SOS | **全部逐字节相同** |
| 尾部标记 | **逐字节相同** |

差异只来自熵编码（libjpeg-turbo vs IJG）的舍入，肉眼不可见。也就是说，
在这张照片上，移植版的输出与官方工具的输出实际上是同一张图。

测试样本 B：600×841 的高分辨率版本，人脸 372×372（占宽 62.0%），
`1.618 × 372 = 602 > 600`，裁剪框同样被夹到整幅图，输出 295×413 / 6865 字节；
成品人脸框为 186×186，与官方成品**完全一致**（头部占比 63.1%）。
这说明：当原图头部已经接近 61.8% 时，工具实际上只做校验和缩放；只有当头部明显偏小
（<61.8% 画宽）时，才会真正触发黄金比例裁剪。

## 14. 验证

- 用 Lena 与自造的 400×533 白底/蓝底证件照跑通全流程：
  背景取样分别判为白/蓝，人脸 163×163，头部占比 40.7%，
  裁剪 263×368，输出 295×413、8.6 KB。
- 纯白图 → 状态 5（无人脸）；200×200 → 状态 2；绿色背景 → 状态 3；
  非 JPEG → 状态 1。
- 回归测试：`tests/test_core.py`。
