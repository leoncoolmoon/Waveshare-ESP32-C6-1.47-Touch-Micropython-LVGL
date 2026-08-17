# plugins/alarm.py -- 闹钟同步：手机推过来的闹钟列表解析后存到本地。
# 没有 VIEW_ID，纯粹的事件处理器，不出现在底栏/滑动列表里。
# 早期单文件版本里这是 on_alarm() 回调，现在原样搬进插件，行为不变。

try:
    from lib import gb_alarm
except ImportError:
    gb_alarm = None


def handle_event(t, data, state, screen):
    """处理来自 Gadgetbridge 的 alarm 事件。

    闹钟列表在 "d" 这个 key 里，跟设备端原始协议一致。
    """
    if t != "alarm":
        return False

    if gb_alarm is None:
        # 设备上没有 lib/gb_alarm 这个模块，没法处理，让 main.py 那边
        # 打印"没有插件处理"的日志，而不是在这里假装处理了。
        return False

    raw_alarm_list = data.get("d", [])
    alarms = gb_alarm.parse_alarm_message(raw_alarm_list)
    gb_alarm.save_alarms(alarms)
    screen.show_temp_message(f"Alarms: {len(alarms)} set", 2)
    return True
