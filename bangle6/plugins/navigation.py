# plugins/navigation.py -- 导航提示插件
# 没有 VIEW_ID：这个插件不占底栏/滑动列表的位置，纯粹是个事件处理器，
# 收到 "nav" 类型的 BLE 消息就弹一条临时提示，用来示范插件不是非得有
# 界面不可。
#
# 用的是 show_temp_message(..., big=True) —— 更醒目的大号提示框，字体
# 本身没放大（8x8 点阵字体想真正放大，需要给行缓冲区多留内存，目前
# 用的这批 canvas 只有 12px 高，放大后的字（16px+）会被裁掉，得单独
# 开一块更高的常驻 buffer，大概 20KB 左右，之前几轮为了内存精打细算，
# 这里就没加了）；big=True 走的是"把整块提示框做大、居中、强调色打底
# 描边"这条路，不额外占内存，视觉上一样很显眼。真要上真正放大字体的
# 版本，加个专门的大号 buffer 就行，跟我说一声。
from bangle_utils import truncate


def handle_event(t, data, state, screen):
    if t != "nav":
        return False
    instruction = data.get("instr")
    distance = data.get("distance")
    if instruction is None:
        screen.show_temp_message("Navigation stopped", 1)
    else:
        nav_text = f"{truncate(instruction, 25)}"
        if distance:
            nav_text += f" {distance}"
        screen.show_temp_message(nav_text, 4, big=True)
    return True
