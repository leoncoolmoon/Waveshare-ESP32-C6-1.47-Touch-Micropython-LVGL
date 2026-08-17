# main.py -- 入口文件：按正确顺序 import 各模块，然后跑主循环。
#
# 早期单文件版本里所有东西都挤在一个 .py 里；现在拆开之后，"谁先 import
# 谁"变得重要了：
#   1. ble_bridge 必须是整个文件最早的一条 import。ble_bridge.py 一
#      import 就会把 BLE 对象建好、激活、注册 GATT 服务、打开广播
#      （见 ble_bridge.py 顶部注释）-- ESP32 的 BLE 控制器初始化需要
#      一整块连续的内部 DRAM，如果等到 DISPLAY 这种占用大块内存的
#      对象建好之后才激活 BLE，容易表现成"能连上但吞吐量差、丢包
#      严重"。ble_bridge.py 自己也不依赖 display_core，就是为了不让
#      它把 DISPLAY 的构造意外提前到 BLE 前面。
#   2. display_core 提供 DISPLAY/CONFIG/INPUT/RTC 等全局对象，其它模块
#      普遍靠 `from display_core import *` 拿这些对象来用。
#   3. plugin_manager.load_all() 必须等 display_core 建好之后才能跑，
#      因为插件文件里普遍会 `from display_core import *`；同时也要
#      赶在 main_loop() 真正开始跑之前，把插件视图接进底栏，用户才能
#      划到它们。
import ble_bridge
from ble_bridge import _BLE_BRIDGE
from display_core import *
from bangle_utils import *
from screen_manager import ScreenManager
import plugin_manager
from lib.hydra import loader
from machine import ADC

# 插件必须在这里加载：得等 display_core 这一层（DISPLAY/PALETTE 等）
# 建好之后插件才能 import 它们。
plugin_manager.load_all()
_PLUGIN_ITEMS = [(p.VIEW_ID, p.ICON, getattr(p, "TITLE", p.ICON)) for p in plugin_manager.views()]

# brightness 只在这个文件里用（UP/DOWN 调亮度那两行），放在这里自己管
# -- 早期单文件版本里它是模块级全局变量，跟 CONFIG['brightness'] 初始
# 化写在同一个文件里，main_loop() 里 `global brightness` 找的就是本
# 文件自己的模块级变量，拆分之后原样保留这个写法就行，不用额外传参
# 或者跨模块读写。
brightness = 7
try:
    brightness = CONFIG['brightness']
except Exception:
    pass    
firmware="1.0.0"
hardware="0.0.0"

adc = ADC(0)
adc.atten(ADC.ATTN_6DB)
_MIN = const(1050000) # 3.15v
_MAX = const(1400000) # 4.2v
_last_v = None
_last_t = 0
_charging = False
_battery_report_interval = 60000
# 主循环轮询间隔。这个纯粹是按键/画面轮询用的，跟 BLE 完全无关 -- BLE
# 收发是中断驱动的（_irq 回调），不受这个循环快慢影响，通知不会因为
# 这里改慢就跟着变慢到达。50ms(20Hz) 对人手按键来说明显快过实际需要，
# 改成 80ms(12.5Hz) 依然感觉不出延迟，但能少让 CPU 醒过来一截，省一点
# 电；如果按键手感变迟钝可以调回小一点。
_LOOP_INTERVAL_MS = const(80)


def main_loop():
    # Run the main loop of the program.
    global brightness
    screen = ScreenManager()
    screen.register_plugin_views(_PLUGIN_ITEMS)
    # _BLE_BRIDGE 早在 ble_bridge.py 模块 import 的时候就已经构造好、
    # 激活、开始广播了（这是这次改动的重点：BLE 硬件初始化要尽量抢在
    # DISPLAY 这种占大块内存的对象之前完成，见 ble_bridge.py 顶部注释），
    # 这里只是拿同一个对象来用，不用再等、也不用占位技巧。
    ble_bridge = _BLE_BRIDGE

    # ---- 回调函数 ------------------------------------------------

    def on_connect():
        global firmware, hardware
        screen.set_status("connected", True)
        screen.show_temp_message("Connected to phone!", 2)
        ble_bridge.send_version(firmware, hardware)

    def on_disconnect():
        screen.set_status("connected", False)
        screen.show_temp_message("Disconnected", 2)

    def on_raw_data(chunk):
        screen.set_status("packet_count", screen.status["packet_count"] + 1)

    def on_notify(app_name, title, body, notif_id=None):
        screen.add_notification(app_name, title, body, notif_id=notif_id)
        screen.set_status("last_event", f"{app_name}: {truncate(title, 30)}")
        screen.show_temp_message(f"📬 {app_name}: {truncate(title, 20)}", 2)
        screen.switch_view(VIEW_NOTIFICATIONS)

        # 让每个插件都看一眼这条通知，愿意的话自己从里面猜数据（天气
        # 插件就是这么从"天气 App 弹的通知"里捞数据的，没有专门的
        # weather 事件时兜底用）。
        plugin_manager.dispatch_notification_guess(app_name, title, body, screen)

    def on_notify_bitmap(app_name, title_field, body_field, notif_id=None):
        screen.add_notification(app_name, "<image>", "<image>", True, title_field, body_field, notif_id=notif_id)
        screen.set_status("last_event", f"{app_name}: <bitmap>")
        screen.show_temp_message(f"📬 {app_name}: <image>", 2)
        screen.switch_view(VIEW_NOTIFICATIONS)

    def on_notify_dismiss(notif_id):
        # 手机那边主动 dismiss 了（比如用户在手机上划掉了通知），把手表
        # 上对应的那条也同步移出去，而不是只弹个提示、留着一条已经不存
        # 在的通知在列表里。
        screen.dismiss_notification_by_id(notif_id)
        screen.show_temp_message("Notification dismissed", 1)

    def on_call(cmd, name, number):
        if cmd == "incoming":
            screen.show_temp_message(f"📞 Incoming: {name or number}", 3)
        elif cmd == "outgoing":
            screen.show_temp_message(f"📞 Calling: {name or number}", 2)
        elif cmd == "end":
            screen.show_temp_message("Call ended", 1)

    # 记录上一次的曲目签名，用来判断 musicinfo 是不是真的换歌了
    _last_track_sig = [None]

    def on_musicinfo(data):
        track = data.get("track", "")
        artist = data.get("artist", "")
        track_sig = (repr(track), repr(artist))
        track_changed = track_sig != _last_track_sig[0]

        screen.update_music(data)

        if track_changed:
            _last_track_sig[0] = track_sig
            screen.switch_view(VIEW_MUSIC)
            track_desc = f"<bitmap {track['width']}x{track['height']}>" if isinstance(track, dict) else track
            if track_desc:
                screen.show_temp_message(f"🎵 {truncate(track_desc, 20)}", 3)
            else:
                screen.show_temp_message("🎵 Music info received", 2)

        print(f"[APP] Music info updated: track={track!r}, changed={track_changed}")

    def on_musicstate(data):
        # Deliberately does NOT call show_temp_message()/switch_view() here:
        # musicstate arrives right after musicinfo, and popping a temp
        # message would immediately cover up the track info card that was
        # just drawn.
        screen.update_music_state(data)

    def on_time_sync(epoch, tz_offset):
        rtc_tuple = epoch_to_rtc_tuple(epoch, tz_offset)
        rtc_write_ok = True
        if RTC is not None:
            try:
                RTC.datetime(rtc_tuple)
                print(f"[RTC] Set to: {rtc_tuple}")
            except Exception as e:
                rtc_write_ok = False
                print("[RTC] Failed to set RTC:", e, rtc_tuple)
        else:
            print(f"[RTC] machine.RTC unavailable, would have set: {rtc_tuple}")

        if rtc_write_ok:
            screen.set_status("time_synced", True)
            screen.show_temp_message("Time synced", 1)
        else:
            # RTC 没写成功，不能假装同步好了。保留原来的 time_synced 状态
            # 不动 -- 如果开机检测发现 RTC 年份 > 2000 已经是 True，就继续
            # 显示那个旧时间；如果本来就是 False，就继续等下一次 setTime。
            screen.show_temp_message("Time sync failed", 2)

    def on_unknown(t, data):
        # weather / alarm / act / calendar / gps / nav / http / find /
        # vibrate ... 这些早期版本里各自专属的回调，现在统一在这里转给
        # plugin_manager，由对应插件的 handle_event() 接住。
        if not plugin_manager.dispatch_event(t, data, screen):
            print("[APP] no plugin handled it")
            screen.show_temp_message(f"no plugin handled it {t}", 1)

    # 真正的回调这里才补上去 -- GATT 服务注册和广播早在 ble_bridge.py
    # 模块 import 时就已经在跑了，跟这里补回调是两件独立的事。
    ble_bridge.set_callbacks(
        on_connect=on_connect,
        on_disconnect=on_disconnect,
        on_raw_data=on_raw_data,
        on_notify=on_notify,
        on_notify_bitmap=on_notify_bitmap,
        on_notify_dismiss=on_notify_dismiss,
        on_call=on_call,
        on_musicinfo=on_musicinfo,
        on_musicstate=on_musicstate,
        on_time_sync=on_time_sync,
        on_unknown=on_unknown,
    )
    # 插件的 handle_event/handle_keys 本来就会收到 screen 这个实例，
    # 挂在它身上插件就能主动往手机发消息了（比如 gps.py 里
    # screen.ble.send_command({...})），不用再改 plugin_manager 里
    # 任何函数的签名去专门传一个 ble_bridge 进去。
    screen.ble = ble_bridge

    screen.show_temp_message("BLE Ready", 2)
    #------------ 报告电量-------------------------
    def _report_battery():
        global _last_v, _last_t, _charging, _battery_report_interval
        now = time.ticks_ms()
        if time.ticks_diff(now, _last_t) < _battery_report_interval: return
        _last_t = now   
        raw = adc.read_uv()
        
        if raw <= _MIN:
            pct = 0
        elif raw >= _MAX:
            pct = 100
        else:
            pct = int(((raw - _MIN) / (_MAX - _MIN)) * 100)
        
        v = raw / 1000000 * 3
        if _last_v is not None:
            diff = v - _last_v
            if abs(diff) > 0.008:
                _charging = diff > 0
        _last_v = v
        print(f"battery = {pct},charging={_charging}")
        ble_bridge.send_battery_info(
            percentage=str(pct),
            charging="1" if _charging else "0"
        )
    # ---- 主循环 ------------------------------------------------

    # Key label strings (LEFT/RIGHT/ENTER/A/B/...) are a guess based on
    # common MicroHydra names -- "[APP] keys pressed: [...]" is printed
    # below so you can confirm the actual names your hardware reports and
    # adjust these branches if the controls don't respond.
    while True:
        _report_battery()

        keys = INPUT.get_new_keys()

        if keys:
            print("[APP] keys pressed:", keys)

            # 当前视图如果正好是某个插件自己的界面，且这个插件定义了
            # handle_keys，就先把这轮按键交给它，让它自己决定要不要
            # 接管。插件说处理了（返回 True）就直接跳过下面这一整套
            # LEFT/RIGHT/UP/DOWN/ENTER 通用逻辑；插件没定义这个钩子、
            # 或者对这轮按键不感兴趣（返回 False），就照常往下走，跟
            # 以前的行为完全一样。
            if plugin_manager.dispatch_keys(screen.current_view, keys, screen):
                screen.draw()
                time.sleep_ms(_LOOP_INTERVAL_MS)
                continue

            views = screen.all_view_ids()
            if "LEFT" in keys:
                current_idx = views.index(screen.current_view)
                screen.switch_view(views[(current_idx - 1) % len(views)])
            elif "RIGHT" in keys:
                current_idx = views.index(screen.current_view)
                screen.switch_view(views[(current_idx + 1) % len(views)])

            elif "ENTER" in keys or "ENT" in keys:
                if screen.current_view == VIEW_MUSIC:
                    if screen.music_info["playing"]:
                        ble_bridge.send_music_control("pause")
                        # 本地乐观更新：不用等手机蓝牙回传 musicstate
                        # 才刷新图标，按下就立刻切换。
                        screen.music_info["playing"] = False
                        screen.dirty = True
                        screen.show_temp_message("⏸ Paused", 1)
                    else:
                        ble_bridge.send_music_control("play")
                        screen.music_info["playing"] = True
                        screen.dirty = True
                        screen.show_temp_message("▶ Playing", 1)
                elif screen.current_view == VIEW_NOTIFICATIONS:
                    if screen.notifications:
                        ble_bridge.send_notify_action(screen.notifications[0].get("id", 0), "DISMISS")
                        
                        # 本地乐观更新：按下就立刻把这条从列表里移出去，
                        # 不用等手机蓝牙回传确认才刷新。
                        screen.dismiss_notification(0)
                elif screen.current_view == VIEW_STATUS:
                    # 原来这里是"跳转到 replMode"，按你的要求改成显示
                    # 反色（参考 lvgl 版本的做法）。
                    screen.toggle_invert()

            # 大写 A/B 基本用不到，改成 UP/DOWN 只在音乐播放界面才当
            # 上一曲/下一曲用；其它界面 UP/DOWN 还是滚动（见下面兜底分支）。
            elif ("UP" in keys and screen.current_view == VIEW_MUSIC) or "a" in keys:
                ble_bridge.send_music_control("previous")
                screen.show_temp_message("⏮ Previous", 1)
            elif ("DOWN" in keys and screen.current_view == VIEW_MUSIC) or "b" in keys:
                ble_bridge.send_music_control("next")
                screen.show_temp_message("⏭ Next", 1)
            elif "VOL+" in keys:
                ble_bridge.send_music_control("volumeup")
                screen.show_temp_message("🔊 Volume +", 1)
            elif "VOL-" in keys:
                ble_bridge.send_music_control("volumedown")
                screen.show_temp_message("🔉 Volume -", 1)
            elif "UP" in keys and screen.current_view == VIEW_STATUS:
                brightness = min(10, brightness + 1)
                print(f"brightness = {brightness}")
                DISPLAY.set_brightness(brightness)
            elif "DOWN" in keys and screen.current_view == VIEW_STATUS:
                brightness = max(0, brightness - 1)
                print(f"brightness = {brightness}")
                DISPLAY.set_brightness(brightness)
            elif "UP" in keys:
                screen.scroll(-1)
            elif "DOWN" in keys:
                screen.scroll(1)

            elif "0" in keys:
                screen.switch_view(VIEW_CLOCK)
            elif "1" in keys:
                screen.switch_view(VIEW_NOTIFICATIONS)
            elif "2" in keys:
                screen.switch_view(VIEW_MUSIC)
            elif "3" in keys:
                screen.switch_view(VIEW_STATUS)
            elif "G0" in keys:
                loader.launch_app("/launcher/launcher")

        screen.draw()
        time.sleep_ms(50)


# 启动主循环
main_loop()

