# plugins/http_plugin.py -- 手机转发过来的 HTTP 请求提示
# 没有 VIEW_ID，纯粹的事件处理器，不出现在底栏/滑动列表里。
# 文件名特意不叫 http.py，避免跟 Python/MicroPython 标准库里的
# http 模块撞名。对应早期单文件版本里的 on_http_request()。

from bangle_utils import truncate


def handle_event(t, data, state, screen):
    if t != "http":
        return False

    url = data.get("url", "")
    screen.show_temp_message(f"🌐 HTTP: {truncate(url, 20)}", 2)
    return True
