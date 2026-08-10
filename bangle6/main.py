# main.py -- 入口文件：按正确顺序 import 各模块，然后跑主循环
import sys
from apps.bangle6.__init__ import path as this_dir
def _add_self_dir_to_path():
    if this_dir and this_dir not in sys.path:
        sys.path.insert(0, this_dir)

    print("模块搜索目录:", this_dir if this_dir else "(未知，依赖默认 sys.path)")
_add_self_dir_to_path()

import ble_bridge
from ble_bridge import _BLE_BRIDGE, BLENotifyBridge
from display_core import *
from bangle_utils import *
from screen_manager import ScreenManager
import plugin_manager

# 插件必须在这里加载：得等 display_core 这一层（DISPLAY/PALETTE/scr）
# 建好之后插件才能 import 它们；同时也要赶在 main_loop() 真正开始跑
# 之前，把插件界面接进底栏，用户才能划到它们。
plugin_manager.load_all()

_PLUGIN_ITEMS = [(p.VIEW_ID, p.ICON) for p in plugin_manager.views()]
BOTTOM_BAR.rebuild(
    [(VIEW_CLOCK, "C"), (VIEW_NOTIFICATIONS, "N"), (VIEW_MUSIC, "M"), (VIEW_STATUS, "S")]
    + _PLUGIN_ITEMS
)
# LEFT/RIGHT 划动切换界面用的顺序：核心4个 + 插件界面（按插件加载
# 顺序，也就是文件名排序）。
_ALL_VIEW_IDS = [VIEW_CLOCK, VIEW_NOTIFICATIONS, VIEW_MUSIC, VIEW_STATUS] + [vid for vid, _ico in _PLUGIN_ITEMS]

# brightness 只在这个文件里用（UP/DOWN 调亮度那两行），放在这里自己管，
# 不用像 tz_offset 那样跨模块读——之前拆分的时候这个变量原本定义在
# ble_bridge.py 里，但 main.py 只 import 了 _BLE_BRIDGE/BLENotifyBridge，
# 没把 brightness 带进来，main_loop() 里 `global brightness` 会去找
# *这个文件自己*的模块级 brightness，而这个文件里根本没定义过，第一次
# 读 `brightness + 1` 就会直接 NameError。改成在这个文件里当"正主"来
# 定义，不再依赖 ble_bridge.py 那边的旧定义（已经从那边删掉了）。
brightness = 7

try:
    from lib.hydra import loader
except ImportError:
    loader = None

try:
    from lib import gb_alarm
except ImportError:
    gb_alarm = None

def main_loop():
    global brightness
    screen = ScreenManager()
    # BLE 广播早在文件最开头就已经启动了（见 _BLE_BRIDGE），这里只是
    # 拿同一个对象来用，不再重新构造。
    ble_bridge = _BLE_BRIDGE
    try:
        from machine import Pin
        g0_pin = Pin(9, Pin.IN, Pin.PULL_UP)
        g0_last = g0_pin.value()
        print("[G0] GPIO9 initialized")
    except Exception as e:
        print("[G0] Failed to init:", e)
        g0_pin = None
    # ---- 回调函数 ------------------------------------------------

    def on_connect():
        screen.set_status("connected", True)
        screen.show_temp_message("Connected to phone!", 2)
        ble_bridge.send_version("1.0.0", "0.0.0")

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
        # 插件就是这么从"天气App弹的通知"里捞数据的，没有专门的
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

    def on_weather(data):
        # 官方 weather 事件，交给愿意认领 "weather" 类型的插件（就是
        # plugins/weather.py），核心代码不再直接管天气这件事了。
        plugin_manager.dispatch_event("weather", data, screen)

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

    def on_alarm(raw_alarm_list):
        if gb_alarm is None:
            return
        alarms = gb_alarm.parse_alarm_message(raw_alarm_list)
        gb_alarm.save_alarms(alarms)
        screen.show_temp_message(f"Alarms: {len(alarms)} set", 2)

    def on_find(active):
        if active:
            screen.show_temp_message("🔍 Finding device...", 2)

    def on_vibrate(duration):
        screen.show_temp_message(f"Vibrate: {duration}ms", 1)

    def on_activity(hrm, steps, interval):
        screen.set_status("activity_active", hrm or steps)

    def on_activity_fetch(data):
        screen.show_temp_message(f"Activity fetch: {data}", 2)

    def on_calendar(event_id, event_type, timestamp, duration, title,
                     description, location, cal_name, color, all_day):
        screen.show_temp_message(f"📅 {title or 'Calendar event'}", 2)

    def on_calendar_remove(event_id):
        screen.show_temp_message("Calendar event removed", 1)

    def on_gps(lat, lon, alt, speed, course, gps_time, satellites, hdop, external_source):
        screen.set_status("gps_active", True)
        if lat is not None and lon is not None:
            screen.show_temp_message(f"📍 GPS: {lat:.4f}, {lon:.4f}", 2)

    def on_gps_power(status):
        if status == "query":
            ble_bridge.send_command({"t": "gps_power", "status": screen.status["gps_active"]})
        else:
            screen.set_status("gps_active", status)

    def on_navigation(instruction, distance=None, action=None, eta=None):
        # 交给 plugins/navigation.py，没装这个插件的话就什么都不做
        # （之前是硬编码在这里的，现在核心代码不认识 "nav" 这个类型了）。
        data = {"instr": instruction, "distance": distance, "action": action, "eta": eta}
        if not plugin_manager.dispatch_event("nav", data, screen):
            print("[APP] nav event but no plugin handled it (navigation plugin not loaded?)")

    def on_http_request(url, xpath=None, request_id=None, insecure=False):
        screen.show_temp_message(f"🌐 HTTP: {truncate(url, 20)}", 2)

    def on_unknown(t, data):
        screen.show_temp_message(f"Unknown: {t}", 1)

    # ---- 接上BLE桥接的回调（BLE本身在文件最开头就已经在广播了）------

    ble_bridge.set_callbacks(
        on_connect=on_connect,
        on_disconnect=on_disconnect,
        on_raw_data=on_raw_data,
        on_notify=on_notify,
        on_notify_bitmap=on_notify_bitmap,
        on_notify_dismiss=on_notify_dismiss,
        on_call=on_call,
        on_weather=on_weather,
        on_musicinfo=on_musicinfo,
        on_musicstate=on_musicstate,
        on_time_sync=on_time_sync,
        on_alarm=on_alarm,
        on_find=on_find,
        on_vibrate=on_vibrate,
        on_activity=on_activity,
        on_activity_fetch=on_activity_fetch,
        on_calendar=on_calendar,
        on_calendar_remove=on_calendar_remove,
        on_gps=on_gps,
        on_gps_power=on_gps_power,
        on_navigation=on_navigation,
        on_http_request=on_http_request,
        on_unknown=on_unknown,
    )

    screen.show_temp_message("BLE Ready", 2)

    # ---- 主循环 ------------------------------------------------

    # Key label strings (LEFT/RIGHT/ENTER/A/B/...) are a guess based on
    # common MicroHydra names -- "[APP] keys pressed: [...]" is printed
    # below so you can confirm the actual names your hardware reports and
    # adjust these branches if the controls don't respond.
    while True:
        _check_backlight_timer()  # 检查背光超时
        if g0_pin is not None:
            g0_current = g0_pin.value()
            # 检测下降沿：从 1→0 表示按键按下
            if g0_current == 0 and g0_last == 1:
                print("[G0] Pressed!")
                if loader is not None:
                    try:
                        loader.launch_app("/launcher/launcher")
                    except Exception as e:
                        print("[G0] Launch failed:", e)
            g0_last = g0_current 
            
        keys = INPUT.get_new_keys()

        if keys:
            print("[APP] keys pressed:", keys)
            views = _ALL_VIEW_IDS
            if "LEFT" in keys:
                current_idx = views.index(screen.current_view)
                screen.switch_view(views[(current_idx - 1) % len(views)])
            elif "RIGHT" in keys:
                current_idx = views.index(screen.current_view)
                screen.switch_view(views[(current_idx + 1) % len(views)])

            elif "ENTER" in keys or "ENT" in keys or "GO" in keys:
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
                    toggle_theme()
                    screen.dirty = True

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
            elif "4" in keys:
                # 数字键 4 开始对应插件界面，按加载顺序（第一个插件是 "4"，
                # 第二个是 "5"，以此类推）；没有插件的话这几个键就没反应。
                if len(_ALL_VIEW_IDS) > 4:
                    screen.switch_view(_ALL_VIEW_IDS[4])
            elif "G0"in keys:
                if loader is not None:
                    loader.launch_app("/launcher/launcher")

        screen.draw()

        # LVGL 心跳：没有 MicroHydra 的主循环帮忙跑 task_handler 了，
        # 要自己喂 tick + task_handler，屏幕/触摸才会真的刷新。
        lv.tick_inc(50)
        lv.task_handler()
        time.sleep_ms(50)

main_loop()

