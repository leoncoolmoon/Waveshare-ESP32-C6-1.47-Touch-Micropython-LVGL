# MicroHydra for Waveshare ESP32-C6 1.47" Touch

[English](#english) | [中文](#中文说明)

---

## English

This project is a modified port of [MicroHydra](https://github.com/echo-lalia/MicroHydra), tailored and adapted specifically for the **Waveshare ESP32-C6 1.47-inch Touch Display** board.

We replaced the original launcher with a custom-designed **MicroPython Smartband Interface (Bangle UI)**, bringing smartband plugin extensibility, backlight control, MicroPython app execution, storage expansion, partial Gadgetbridge SDK compatibility, and AI query capabilities.

### 🌟 Features

* **Hardware Adaptation & Backlight Control**: Fully adapted screen and touch drivers for the Waveshare ESP32-C6 1.47" touch display, featuring built-in **backlight turn-off control** for power saving.
* **Custom Bangle Smartband UI**: Modern smartband interface implemented in MicroPython, providing fluid navigation and controls.
* **Smartband Plugin System**: Plugins are designed specifically for the **Bangle smartband interface**, allowing dynamic features (weather, alarm, AI assistant, etc.) on the watch face.
* **MicroPython Apps**: Applications (`apps/`) run directly on top of **MicroPython**, offering independent tool and utility execution.
* **SD Card / Storage Expansion**: Full support for SD card storage expansion to store additional apps, plugins, fonts, and user data.
* **Gadgetbridge SDK Compatibility**: Partially compatible with [Gadgetbridge](https://gadgetbridge.org/) SDK protocols for companion app communication.
* **AI Query & LLM Chat**: Integrated AI query module (`chat_llm.py`) enabling intelligent assistant capabilities directly from the band.
* **Built-in Utilities**:
  * **WiFi Time Synchronization (`TimeSyn.py`)**: Synchronizes real-time clock over NTP via WiFi.
  * **Bluetooth Combo Remote (`BLEcomboV4`)**: Bluetooth HID keyboard and mouse simulator, turning the display into a wireless touchpad/keyboard.

### 📁 Directory Structure

```text
microhydra/
├── apps/               # MicroPython applications (including TimeSyn.py, BLEcomboV4, etc.)
├── font/               # Font files
├── launcher/           # Launcher and UI modules (Bangle smartband UI)
├── lib/                # System libraries and drivers (hardware drivers, backlight control, etc.)
├── plugins/            # Plugins created specifically for the Bangle smartband interface
├── llm_chat_config.json# AI chat configuration
├── llm_chat_skills.json# AI chat skills description
└── main.py             # Main entry point
```

# Software Operation Logic Guide

This project is built for the **Waveshare ESP32-C6 1.47" Touch Display** board, combining MicroHydra and the Bangle Smartband UI. It provides touch gesture keyboard simulation, edge-swipe virtual keyboard input, multi-functional physical button controls, and a crash recovery REPL mode.

---

### 1. Touch Gestures & Keyboard Simulation

In the main canvas area (center of the screen), touch gestures directly simulate standard keyboard inputs:

* **Arrow Keys Simulation (Swiping)**:
  * **Swipe Up**: Simulates `UP` arrow key
  * **Swipe Down**: Simulates `DOWN` arrow key
  * **Swipe Left**: Simulates `RIGHT` arrow key
  * **Swipe Right**: Simulates `LEFT` arrow key
* **Enter Key Simulation**:
  * Tap and hold in the center canvas area for **more than 150ms** and release to simulate the `ENTER` key.

---

### 2. Edge-Swipe Virtual Keyboard Input (`vKey`)

The system includes an edge-swipe virtual keyboard algorithm (`vKey`) for full character input without physical hardware:

1. **Row Selection (Edge Sliding)**:
   * Press and slide vertically along the **left or right edges** of the screen to select one of the 4 keyboard rows.
   * The preview bar at the bottom displays the full key layout of the currently selected row in real-time.
2. **Column Selection & Character Entry (Sliding Inwards)**:
   * After selecting a row, slide horizontally **inward towards the center canvas** to choose from 14 key columns.
   * Highlighted indicators at the top and bottom show the currently selected character.
   * **Type Character**: Release your finger inside the canvas area to output the selected character.
   * **Cancel**: Slide back to the edge or release outside the canvas to cancel input.
3. **Keyboard Modes & Modifiers**:
   * **SHIFT / FN**: Select `SHIFT` or `FN` to toggle capital letters, special symbols, and function keys (F1-F10, ESC, DEL, etc.).
   * **Modifier Locking (CTL / ALT / OPT)**: Select `CTL`, `ALT`, or `OPT` to lock a modifier key (indicated by a badge badge in the corner). The next character typed will combine with the modifier (e.g., `Ctrl + C`).
   * **ESC Shortcut**: Tap near the top or bottom extreme edges of the canvas to trigger `ESC`.

---

### 3. Physical Button G0 (GPIO 9) Operation Logic

The physical button **G0** on the side serves different functions depending on the current system state:

* **In Smartband Interface (Bangle UI)**:
  * Acts as the **Home Button**.
  * Pressing G0 on any view or plugin page immediately returns to the MicroHydra main launcher (`/launcher/launcher`).
* **In MicroHydra Launcher**:
  * Acts as the **Menu Button**.
  * Pressing G0 in the launcher opens the quick option menu (Home, System Reset, USB / REPL mode, etc.).
* **During Boot / System Crash Recovery (REPL Mode)**:
  * Acts as the **Crash Recovery / Debug Interrupt Button**.
  * If the system crashes or during power-on/reset, **hold down G0**. The boot script (`main.py`) detects G0 as LOW, raises a `KeyboardInterrupt`, and drops directly into the MicroPython **REPL command line** for debugging and recovery.

---

### 4. Smartband UI (Bangle UI) Navigation & Shortcuts

* **View Switching**: Swipe left/right (or use `LEFT` / `RIGHT` keys) to switch between Clock, Notifications, Music, Status, and plugin views (Weather, Alarm, GPS, Chat LLM, etc.).
* **Page Scrolling**: Swipe up/down (or use `UP` / `DOWN` keys) to scroll lists or long text pages.
* **Music View**:
  * `ENTER` (Hold screen): Play / Pause
  * `UP` / `DOWN` (or `a` / `b` keys): Previous / Next track
  * `VOL+` / `VOL-`: Volume control
* **Status View**:
  * `UP` / `DOWN`: Adjust screen backlight brightness
  * `ENTER`: Toggle display color inversion

---
### 🙏 Credits & References

* [MicroHydra](https://github.com/echo-lalia/MicroHydra) - Lightweight MicroPython app launcher and OS framework.
* [MicroPythonBLEHID](https://github.com/Heerkog/MicroPythonBLEHID) - The foundation for the Bluetooth HID keyboard and mouse library used in `BLEcomboV4`.

---

## 中文说明

本项目基于 [MicroHydra](https://github.com/echo-lalia/MicroHydra) 进行二次开发与移植，专门适配了 **Waveshare ESP32-C6 1.47-inch 触摸屏** 开发板。

我们在原版 MicroHydra 的基础上进行了深度定制，将主界面替换为自研的 **MicroPython 智能手环界面（Bangle UI）**，同时引入了手环插件机制、Gadgetbridge 协议兼容以及 AI 对话查询功能，并内置了多种实用工具与背光控制。

### 🌟 主要特性

* **硬件适配与背光控制**：专门针对 Waveshare ESP32-C6 1.47" 触摸屏进行屏幕与触控驱动适配，并内置**屏幕背光关闭功能**，有效节省功耗。
* **全新 Bangle 手环主界面**：基于 MicroPython 自研的手环 UI，操作流畅，视觉体验更佳。
* **插件（Plugins）系统**：专为 **Bangle 手环界面** 设计的插件拓展机制，可动态增强手环主界面功能（如天气、闹钟、日程、AI助手等）。
* **独立应用（Apps）生态**：应用（Apps）直接运行于 **MicroPython** 环境中，提供丰富且独立的功能体验。
* **存储卡扩展支持**：支持 SD 卡 / 外接存储扩展，方便存放应用、插件、字体与配置文件。
* **Gadgetbridge SDK 兼容**：兼容部分 [Gadgetbridge](https://gadgetbridge.org/) SDK 规范与协议，可与手机端的 Android/iOS 配套应用建立连接并互动。
* **AI 查询与对话**：内置 AI / LLM 查询功能（如 `chat_llm.py`），支持智能交互。
* **内置实用应用**：
  * **WiFi 自动对时 (`TimeSyn.py`)**：通过 WiFi 连接网络 NTP 服务器进行精确时间同步。
  * **蓝牙键鼠模拟器 (`BLEcomboV4`)**：支持蓝牙 HID 鼠标和键盘模拟，可将设备用作无线的蓝牙触控鼠标与键盘控制器。

### 📁 目录结构

```text
microhydra/
├── apps/               # 运行在 MicroPython 上的独立应用 (包含 TimeSyn.py, BLEcomboV4 等)
├── font/               # 字体文件
├── launcher/           # 启动器与界面 (包含 Bangle 手环主界面)
├── lib/                # 系统库与驱动 (硬件驱动、背光控制、蓝牙、网络、GUI 辅助等)
├── plugins/            # 专供 Bangle 手环界面使用的插件目录 (如 chat_llm, weather, alarm 等)
├── llm_chat_config.json# AI 对话配置文件
├── llm_chat_skills.json# AI 技能配置文件
└── main.py             # 入口引导程序
```

---

# 软件操作逻辑指南

本项目基于 Waveshare ESP32-C6 1.47" Touch 开发板，结合 MicroHydra 与 Bangle 智能手环 UI，提供了触摸手势模拟键盘、边缘全键盘输入、物理按键多功能控制以及 REPL 崩溃恢复机制。

---

### 1. 触摸手势与键盘模拟

在设备屏幕中央的主画布区域，可通过手势直接模拟标准键盘按键输入：

* **方向键模拟（上下左右划动）**：
  * **向上划动**：模拟键盘 `UP` 键
  * **向下划动**：模拟键盘 `DOWN` 键
  * **向左划动**：模拟键盘 `RIGHT` 键
  * **向右划动**：模拟键盘 `LEFT` 键
* **Enter (回车键) 模拟**：
  * 在屏幕中央区域点击并按住**超过 150ms** 后释放，模拟键盘 `ENTER` 键。

---

### 2. 边缘滑动虚拟全键盘输入

设备支持无需物理全键盘即可完成全字符输入的边缘触摸键盘算法（`vKey`）：

1. **边缘选行 (Row Selection)**：
   * 在屏幕**左侧或右侧边缘**按下手指并上下滑动，可以在 4 行键盘布局中切换选择目标行。
   * 屏幕底部预览区域会实时高亮显示当前选中行的完整按键（包含字母、数字、标点及功能键）。
2. **向内划动选列与输入 (Column Selection & Input)**：
   * 在边缘选中目标行后，按住手指**向屏幕中间画布区域水平滑动**，可在 14 个按键列中选择具体的字符/按键。
   * 屏幕顶部与底部会高亮显示当前选中的字符。
   * **松开手指**：在画布区域释放手指，即完成该字符/命令的键盘输入。
   * **取消输入**：如果滑回屏幕边缘或滑出画布区域松开，本次按键输入将自动取消。
3. **键盘模式与修饰键 (Keymaps & Modifiers)**：
   * **SHIFT / FN**：选定点击 `SHIFT` 或 `FN` 键可切换大写字符/符号/功能键（F1-F10、ESC、DEL 等）模式。
   * **修饰键锁定 (CTL / ALT / OPT)**：选择 `CTL`、`ALT` 或 `OPT` 修饰键可将其锁定，角落会显示提示 Badge，随后输入的下一个字符将自动附带修饰键（例如实现 `Ctrl + C` 等快捷组合键）。
   * **ESC 快捷区域**：在画布顶部或底部的极窄区域点击可直接触发 `ESC` 键。

---

### 3. 物理按键 G0 (GPIO 9) 操作逻辑

设备侧边的物理按键 **G0** 在不同系统状态下扮演不同的核心功能：

* **手环主界面 (Bangle UI) 下**：
  * 相当于 **Home 键**。
  * 在手环的任何视图或插件界面下按下 G0 键，将直接加载并返回 MicroHydra 主启动器 (`/launcher/launcher`)。
* **MicroHydra 启动器 (Launcher) 下**：
  * 相当于 **菜单键 (Menu)**。
  * 在启动器主界面按下 G0 键，会弹出系统快捷选项菜单（包括返回 Home、重启系统 Reset、进入 USB / REPL 模式等）。
* **开机 / 彻底崩坏状态下的 REPL 恢复模式**：
  * 相当于 **崩溃恢复 / 调试中断键**。
  * 在遇到程序彻底崩坏、死循环或系统复位（Reset / 开机）时，**按住 G0 键不放**，引导程序（`main.py`）检测到 G0 为低电平后会立即抛出 `KeyboardInterrupt`，中断引导流并直通 MicroPython **REPL 命令行模式**，方便开发者进行调试、修复或重新烧录。

---

### 4. 手环界面 (Bangle UI) 交互与快捷控制

* **视图切换**：左右划动屏幕（或模拟 `LEFT` / `RIGHT` 按键）可在时钟 (Clock)、通知 (Notifications)、音乐 (Music)、状态设置 (Status) 及各拓展插件（天气、闹钟、GPS、AI 对话等）之间平滑切换。
* **内容滚动**：在通知列表、设置菜单或插件页面中上下划动屏幕（或模拟 `UP` / `DOWN` 按键）进行页面滚动。
* **音乐控制页 (Music View)**：
  * `ENTER`（长按屏）：暂停 / 播放控制
  * `UP` / `DOWN`（或 `a` / `b` 键）：上一曲 / 下一曲
  * `VOL+` / `VOL-`：音量调节
* **状态设置页 (Status View)**：
  * `UP` / `DOWN`：调节屏幕背光亮度
  * `ENTER`：切换屏幕反色显示模式
### 🙏 鸣谢与参考项目

* [MicroHydra](https://github.com/echo-lalia/MicroHydra) - 优秀的 ESP32 / MicroPython 轻量级操作系统与 App 加载器。
* [MicroPythonBLEHID](https://github.com/Heerkog/MicroPythonBLEHID) - 蓝牙键盘与鼠标模拟器（`BLEcomboV4`）所使用的 BLE HID 底层库基础来源。
