A BLE mouse&keybroad Thanks https://github.com/Heerkog/MicroPythonBLEHID for the original library
# BLE Combo HID (Touch Mouse / Scroll / Keyboard / Settings)

A MicroPython + LVGL touchscreen tool that turns an ESP32-class board with a
JD9853 display and AXS5106 touch panel into a **Bluetooth LE combo input
device** — mouse, scroll wheel, and full keyboard — all driven by finger
gestures on the screen, with a single GPIO button to cycle between modes.

**[English](#english) | [中文](#中文)**

---

<a id="english"></a>
## English

### Features

- **Mouse mode** — drag on the canvas to move the cursor, tap for a left
  click, dedicated `L` / `M` / `R` buttons for left / middle / right click.
- **Scroll mode** — same canvas, drag translates into wheel / pan scrolling.
- **Keyboard mode** — drag on the right edge to pick a row, then drag left to
  pick a key from a two-row preview strip; supports modifiers (`CTRL` /
  `SHIFT` / `ALT` / `OPT`), an `FN` layer, and common shortcuts
  (copy/paste/undo/cut, arrows, function keys, etc.).
- **Settings mode** — on-screen sliders/switches for:
  - Backlight brightness
  - Invert display colors
  - Mouse sensitivity
  - Scroll sensitivity
  - BLE connection status + a **Disconnect** button
  - Settings are saved to flash (`combo_settings.json`) and persist across
    reboots.
- **One button, four modes** — a single GPIO (default `GPIO9`, active-low)
  cycles: `Mouse → Scroll → Keyboard → Settings → Mouse ...`
- **Clean UI switching** — each mode's widgets are created only when you
  switch into it and are fully deleted when you switch out, so there's no
  leftover borders/labels from other modes.
- **Status indicators**
  - Top-right dot: BLE connection state (blue = connected, white =
    disconnected).
  - Top-left dot (Mouse/Scroll mode): green `M` = mouse, white `S` = scroll.
  - Top-left dot (Keyboard mode): red `C` = Caps Lock on, white `C` = off.

### Hardware

- ESP32-class MCU running MicroPython with `lvgl` bindings
- JD9853 SPI LCD (172×320, rotated to 320×172 landscape)
- AXS5106 I2C capacitive touch panel
- One GPIO button on `GPIO9` (pulled up, triggers on falling edge) used to
  cycle modes

### Project structure

The tool is split into four modules that each own their own data, BLE
actions, and UI:

```
main.py       # Framework: display/touch/BLE init, GPIO9 mode cycling,
              #            top-right BLE status dot, main loop
settings.py   # Settings data (load/save to flash) + Settings screen UI
mouse.py      # Mouse/scroll BLE actions + mouse/scroll touch UI
keyboard.py   # Keymap data + keyboard BLE actions + keyboard touch UI
```

`main.py` is the entry point; it imports the other three modules and creates
their UI on demand. `settings.py`, `mouse.py`, and `keyboard.py` do not
depend on each other — they only talk to `main.py`'s shared BLE state.

### Installation

1. Copy all four files (`main.py`, `settings.py`, `mouse.py`, `keyboard.py`)
   into the **same folder** on the device's filesystem, e.g. `/myapp/v1`.
   They locate each other automatically at runtime (see
   [Auto-locating modules](#auto-locating-modules)), so the folder can be
   renamed or moved without editing any code.
2. Make sure the following are available on the device (either as built-in
   firmware modules or installed separately, not included in this repo):
   `lcd_bus`, `jd9853`, `axs5106`, `i2c`, `touch_cal_data`, `hid_services`
   (providing a `ComboHID` class compatible with the calls used in
   `main.py`).
3. Set the folder to run at boot (e.g. point `boot.py`/`main.py` on the
   device at it, or `import` it after `sys.path` is set up), then power
   cycle / reset the board.

#### Auto-locating modules

`main.py` inspects its own `__file__` to find the folder it's running from
and inserts that folder at the front of `sys.path`, so `settings.py`,
`mouse.py`, and `keyboard.py` are found regardless of where the folder
lives on the filesystem. If you run the script by streaming it straight
into the REPL (e.g. `mpremote run main.py`, Thonny "Run current script")
instead of loading it from flash, `__file__` will be `'<stdin>'` and the
code falls back to `os.getcwd()` — in that case `cd` into the folder on the
device first (e.g. `mpremote fs cd :/myapp/v1 run main.py`), or just deploy
the files to flash and let them run as the boot script instead of streaming.

### Usage

1. Power on — the device starts advertising as a BLE HID device named
   `"Touch Combo HID"`; pair it from your computer/phone's Bluetooth
   settings.
2. Default mode is **Mouse**. Press the `GPIO9` button to cycle modes.
3. **Mouse/Scroll**: drag inside the canvas panel; tap for a left click; use
   the `L`/`M`/`R` buttons for explicit clicks.
4. **Keyboard**: touch and drag on the right-hand strip to choose a row
   (1–4), then drag left onto the two preview rows to pick a key; lift your
   finger to send it. Modifier keys (`CTRL`/`SHIFT`/`ALT`/`OPT`) latch on
   tap and release after the next key you send; `FN` toggles the
   symbol/function-key layer; `CAP` toggles the on-screen Caps Lock
   indicator.
5. **Settings**: scroll the panel vertically if content overflows; adjust
   backlight, color inversion, mouse/scroll sensitivity; tap **Disconnect**
   to force-drop the current BLE connection (the device will start
   advertising again automatically).

### Known limitations

- The Caps Lock indicator reflects a **locally tracked toggle**, not the
  host's real keyboard LED state (simple BLE HID keyboards typically don't
  expose the host's LED output report back to the firmware).
- `apply_invert()` in `settings.py` tries a few common display-driver method
  names (`set_color_inversion` / `invert_colors` / `invert_display`) — verify
  which one your `jd9853` build actually exposes.
- `ComboHID` (in `hid_services.py`, not included here) must expose
  `conn_handle` and `_ble` attributes compatible with the BLE disconnect
  call used in `main.py`'s `BLEState.disconnect()`.

### License

GPL v3.0

---

<a id="中文"></a>
## 中文

### 功能特性

- **鼠标模式** —— 在画布区域拖动控制鼠标移动，轻点画布触发左键点击，另外提供
  `L`/`M`/`R` 三个按钮分别对应左键/中键/右键点击。
- **滚轮模式** —— 复用同一块画布，拖动手势转换为滚轮/横向滚动。
- **键盘模式** —— 在屏幕右侧拖动选择行(1~4)，再向左拖动在两行预览区里选择具体按键；
  支持修饰键(`CTRL`/`SHIFT`/`ALT`/`OPT`)、`FN` 副层，以及复制/粘贴/撤销/剪切、方向键、
  功能键等常用组合。
- **设置模式** —— 屏幕上直接调整：
  - 背光亮度
  - 屏幕是否反色
  - 鼠标灵敏度
  - 滚轮灵敏度
  - 显示当前BLE连接状态，并提供 **Disconnect(断开连接)** 按钮
  - 所有设置会保存到 flash 上的 `combo_settings.json` 文件，重启后依然生效
- **一个按键切换四种模式** —— 单个GPIO(默认 `GPIO9`，低电平触发)循环切换：
  `鼠标 → 滚轮 → 键盘 → 设置 → 鼠标 ...`
- **控件按需创建/销毁** —— 每个模式的控件只在切换进入该模式时创建，切换出去时整体删除，
  不会残留其他模式的边框或文字。
- **状态指示灯**
  - 右上角圆点：BLE连接状态(蓝色=已连接，白色=未连接)
  - 左上角圆点(鼠标/滚轮模式)：绿色 `M` = 鼠标模式，白色 `S` = 滚轮模式
  - 左上角圆点(键盘模式)：红色 `C` = 大写锁定已开启，白色 `C` = 未开启

### 硬件

- 运行 MicroPython + `lvgl` 绑定的 ESP32 系列主控
- JD9853 SPI屏幕(172×320，旋转后为320×172横屏)
- AXS5106 I2C电容触摸屏
- 一个接在 `GPIO9` 上的按键(上拉、下降沿触发)，用于循环切换模式

### 项目结构

工具拆分成四个模块，各自管理自己的数据、BLE动作和UI：

```
main.py       # 主框架: 显示屏/触摸/BLE初始化、GPIO9模式循环切换、
              #         右上角BLE状态指示灯、主循环
settings.py   # 设置数据(读取/保存到flash) + 设置界面UI
mouse.py      # 鼠标/滚轮的BLE动作 + 鼠标/滚轮触控UI
keyboard.py   # 键位数据 + 键盘的BLE动作 + 键盘触控UI
```

`main.py` 是入口文件，负责导入另外三个模块并按需创建对应UI。`settings.py`、
`mouse.py`、`keyboard.py` 三者互不依赖，只与 `main.py` 里共享的BLE状态对象交互。

### 安装部署

1. 把四个文件(`main.py`、`settings.py`、`mouse.py`、`keyboard.py`)拷贝到设备文件系统的
   **同一个文件夹**下，比如 `/myapp/v1`。它们运行时会自动找到彼此(见下方
   [自动定位模块](#自动定位模块))，所以这个文件夹改名或者挪到别的路径都不用改代码。
2. 确保设备上已经具备以下模块(固件自带或另行安装，本仓库不包含)：
   `lcd_bus`、`jd9853`、`axs5106`、`i2c`、`touch_cal_data`、`hid_services`
   (需要提供一个与 `main.py` 里调用方式兼容的 `ComboHID` 类)。
3. 把这个文件夹设置为开机自动运行(比如让设备上的 `boot.py`/`main.py` 指向它，或者在
   配置好 `sys.path` 后 `import` 它)，然后重新上电/复位开发板。

#### 自动定位模块

`main.py` 会读取自身的 `__file__` 来定位当前所在文件夹，并把这个目录加到
`sys.path` 最前面，这样 `settings.py`/`mouse.py`/`keyboard.py` 无论被放在哪个路径下
都能被正确找到。如果你是用"流式执行"的方式跑脚本(比如 `mpremote run main.py`、
Thonny 的"运行当前脚本")，而不是先把文件真正加载进设备的flash文件系统，那么
`__file__` 会变成 `'<stdin>'`，代码会退回用 `os.getcwd()` 兜底——这种情况下需要先在
设备上 `cd` 到目标文件夹(比如 `mpremote fs cd :/myapp/v1 run main.py`)，或者干脆把
文件部署到flash上、作为开机脚本运行，而不是流式跑。

### 使用方法

1. 上电后设备会以 `"Touch Combo HID"` 的名字开始BLE广播，在电脑/手机蓝牙设置里配对即可。
2. 默认进入**鼠标模式**，按一下 `GPIO9` 按键循环切换模式。
3. **鼠标/滚轮模式**：在画布区域内拖动；轻点画布触发左键点击；也可以直接点
   `L`/`M`/`R` 按钮完成对应点击。
4. **键盘模式**：在右侧竖条上按住拖动选择行(1~4)，再向左拖到两行预览区选择具体按键，
   松手即发送。修饰键(`CTRL`/`SHIFT`/`ALT`/`OPT`)点一下锁定，下一个按键发送完自动释放；
   `FN` 用来切换符号/功能键副层；`CAP` 用来切换屏幕上的大写锁定指示灯状态。
5. **设置模式**：内容超出屏幕高度时可以竖直滑动查看；可以调整背光、反色、鼠标/滚轮灵敏度；
   点击 **Disconnect** 可以主动断开当前BLE连接(断开后设备会自动重新开始广播)。

### 已知局限

- 大写锁定指示灯反映的是**本地记录的开关状态**，并不是主机端真实的键盘LED状态
  (简单的BLE HID键盘方案通常不会把主机的LED输出报文回传给设备固件)。
- `settings.py` 里的 `apply_invert()` 依次尝试了几个常见的显示驱动反色方法名
  (`set_color_inversion` / `invert_colors` / `invert_display`)，具体你的 `jd9853`
  版本支持哪个需要自行验证。
- `ComboHID`(在本仓库未包含的 `hid_services.py` 里)需要暴露 `conn_handle` 和 `_ble`
  这两个属性，且行为要兼容 `main.py` 里 `BLEState.disconnect()` 的调用方式。

### 许可证

GPL v3.0
