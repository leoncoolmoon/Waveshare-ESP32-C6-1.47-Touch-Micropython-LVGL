# plugins/find.py -- "查找设备" + 振动提示插件
# 没有 VIEW_ID，纯粹的事件处理器，不出现在底栏/滑动列表里。
# 对应早期单文件版本里的 on_find()/on_vibrate()，两个原本分开的回调
# 现在合并成一个插件文件（都是"手机让手表短暂闹一下"这一类事件）。


def handle_event(t, data, state, screen):
    if t == "find":
        if data.get("n", False):
            screen.show_temp_message("🔍 Finding device...", 2)
        return True

    if t == "vibrate":
        duration = data.get("n", 0)
        screen.show_temp_message(f"Vibrate: {duration}ms", 1)
        return True

    return False
