# plugins/navigation.py -- 导航提示插件
# 没有 VIEW_ID：这个插件不占底栏/滑动列表的位置，纯粹是个事件处理器，
# 收到 "nav" 类型的 BLE 消息就弹一条临时提示。对应早期单文件版本里的
# on_navigation()，行为保持一致。
from bangle_utils import truncate
from display_core import INPUT

def handle_event(t, data, state, screen):
    if t != "nav":
        return False
    instruction = data.get("instr")
    distance = data.get("distance")
    if instruction is None:
        screen.show_temp_message("Navigation stopped", 1)
    else:
        INPUT._register_touch_activity()
        nav_text = f"🧭 {truncate(instruction, 25)}"
        if distance:
            nav_text += f" {distance}"
        screen.show_temp_message(nav_text, 4)
    return True
