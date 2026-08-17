# MicroHydra for Waveshare ESP32-C6 1.47" Touch

[中文](#中文说明) | [English](#english)

---

## 中文说明

本项目基于 [MicroHydra](https://github.com/echo-lalia/MicroHydra) 进行二次开发与移植，专门适配了 **Waveshare ESP32-C6 1.47-inch 触摸屏** 开发板。

我们在原版 MicroHydra 的基础上进行了深度定制，将主界面替换为自研的 **MicroPython 智能手环界面**，同时引入了插件扩展机制、Gadgetbridge 协议兼容以及 AI 对话查询功能，并内置了多种实用工具。

### 🌟 主要特性

* **硬件适配**：专门针对 Waveshare ESP32-C6 1.47" 触摸屏进行屏幕与触控驱动适配。
* **全新手环主界面**：基于 MicroPython 自研的智能手环 UI，操作流畅，视觉体验更佳。
* **插件扩展系统**：支持插件（Plugins）热加载与管理，方便功能扩展。
* **Gadgetbridge SDK 兼容**：兼容部分 [Gadgetbridge](https://gadgetbridge.org/) SDK 规范与协议，可与手机端的 Android/iOS 配套应用建立连接并互动。
* **AI 查询与对话**：内置 AI / LLM 查询功能（如 `chat_llm.py`），支持智能交互。
* **内置实用应用**：
  * **WiFi 自动对时 (`TimeSyn.py`)**：通过 WiFi 连接网络 NTP 服务器进行精确时间同步。
  * **蓝牙键鼠模拟器 (`BLEcomboV4`)**：支持蓝牙 HID 鼠标和键盘模拟，可将设备用作无线的蓝牙触控鼠标与键盘控制器。

### 📁 目录结构

```text
microhydra/
├── apps/               # 内置应用目录 (包含 TimeSyn.py, BLEcomboV4 等)
├── font/               # 字体文件
├── launcher/           # 启动器与界面 (包含 bangle 手环主界面)
├── lib/                # 系统库与驱动 (硬件驱动、蓝牙、网络、GUI 辅助等)
├── plugins/            # 插件目录 (包含 chat_llm, weather, alarm 等)
├── llm_chat_config.json# AI 对话配置文件
├── llm_chat_skills.json# AI 技能配置文件
└── main.py             # 入口引导程序
```

### 🙏 鸣谢与参考项目

* [MicroHydra](https://github.com/echo-lalia/MicroHydra) - 优秀的 ESP32 / MicroPython 轻量级操作系统与 App 加载器。
* [Waveshare ESP32-C6 1.47 Touch BLE_mouse_keybroad](https://github.com/leoncoolmoon/Waveshare-ESP32-C6-1.47-Touch-Micropython-LVGL/tree/main/BLE_mouse_keybroad) - 蓝牙键盘与鼠标模拟器 `BLEcomboV4` 的底层移植来源。

---

## English

This project is a modified port of [MicroHydra](https://github.com/echo-lalia/MicroHydra), tailored and adapted specifically for the **Waveshare ESP32-C6 1.47-inch Touch Display** board.

We replaced the original launcher with a custom-designed **MicroPython Smartband Interface**, bringing plugin extensibility, partial Gadgetbridge SDK compatibility, AI query capabilities, and several built-in applications.

### 🌟 Features

* **Hardware Adaptation**: Fully adapted screen and touch drivers for the Waveshare ESP32-C6 1.47" touch screen.
* **Custom Smartband UI**: Modern smartband interface implemented in MicroPython, providing fluid navigation and controls.
* **Plugin Architecture**: Easily extend functionalities through dynamically loaded plugins.
* **Gadgetbridge SDK Compatibility**: Partially compatible with [Gadgetbridge](https://gadgetbridge.org/) SDK protocols for companion app communication.
* **AI Query & LLM Chat**: Integrated AI query module (`chat_llm.py`) enabling intelligent assistant capabilities directly from the band.
* **Built-in Utilities**:
  * **WiFi Time Synchronization (`TimeSyn.py`)**: Synchronizes real-time clock over NTP via WiFi.
  * **Bluetooth Combo Remote (`BLEcomboV4`)**: Bluetooth HID keyboard and mouse simulator, turning the display into a wireless touchpad/keyboard.

### 📁 Directory Structure

```text
microhydra/
├── apps/               # Built-in applications (including TimeSyn.py, BLEcomboV4, etc.)
├── font/               # Font files
├── launcher/           # Launcher and UI modules (bangle smartband UI)
├── lib/                # System libraries and drivers
├── plugins/            # Extensible plugins (chat_llm, weather, alarm, etc.)
├── llm_chat_config.json# AI chat configuration
├── llm_chat_skills.json# AI chat skills description
└── main.py             # Main entry point
```

### 🙏 Credits & References

* [MicroHydra](https://github.com/echo-lalia/MicroHydra) - Lightweight MicroPython app launcher and OS framework.
* [Waveshare ESP32-C6 1.47 Touch BLE_mouse_keybroad](https://github.com/leoncoolmoon/Waveshare-ESP32-C6-1.47-Touch-Micropython-LVGL/tree/main/BLE_mouse_keybroad) - Implementation reference for the `BLEcomboV4` Bluetooth mouse/keyboard simulator.
