# plugin_manager.py -- 扫描 /plugins 目录，把每个 .py 文件当一个插件
# 动态 import 进来。插件是"鸭子类型"约定，不用继承什么基类：
#
#   VIEW_ID = "weather"     可选。给了就是一个可以左右划切换到的界面
#   ICON = "W"               VIEW_ID 有值的话必须给，底栏显示的短标签
#                             （ASCII，跟现在的字体一致，别用 emoji）
#   TITLE = "Weather"         VIEW_ID 有值的话必须给，顶栏标题文字
#
#   def init_state():        可选，返回这个插件自己的初始状态 dict，
#                             不给的话默认是空 dict {}
#
#   def draw(state):          VIEW_ID 有值的话必须给，画这个插件的界面。
#                             DISPLAY/PALETTE/_MH_DISPLAY_WIDTH 这些
#                             直接在插件文件里自己 `from display_core
#                             import *`，不用穿参数进来。
#
#   def on_hide(state):       可选。离开这个插件的界面时调用一次。
#                             大多数插件只用 DISPLAY.text/line 画画，
#                             下次别的视图重绘自然就把内容覆盖掉了，
#                             不需要管这个钩子；只有插件自己创建了
#                             不受画布重绘覆盖的额外资源时才需要。
#
#   def handle_keys(keys, state, screen):
#                             可选。只有当前视图正好是这个插件自己的
#                             界面时才会被问到（不是这个插件的界面，
#                             压根不会调它）。想自己接管这轮按键就处理
#                             完返回 True，main.py 会跳过后面
#                             LEFT/RIGHT/UP/DOWN/ENTER 那一整套通用逻辑；
#                             对这轮按键不感兴趣就返回 False（或者干脆
#                             不定义这个函数），main.py 照常处理。
#
#   def handle_event(t, data, state, screen):
#                             可选。BLE 那边收到一条消息，依次问每个
#                             插件愿不愿意处理，返回 True 就算处理完了。
#                             screen 是 ScreenManager 实例，插件可以调
#                             screen.show_temp_message()/switch_view()/
#                             set_status() 之类的，也能用 screen.ble
#                             主动往手机发东西。
#
#   def on_notification_guess(app_name, title, body, state, screen):
#                             可选。普通通知进来时，没有专门事件类型
#                             时的兜底猜测入口（天气插件用这个从"天气
#                             App 弹的通知"里捞数据）。
#
# 没有 VIEW_ID 的插件就是纯粹的事件处理器，不出现在底栏/滑动列表里
# （比如 gps.py / navigation.py 这种）。
import os
import sys
from __init__ import path as this_dir

_PLUGIN_DIR = "/plugins"
if _PLUGIN_DIR not in sys.path:
    sys.path.append(_PLUGIN_DIR)

PLUGINS = []          # 按加载顺序排的插件模块列表
_STATES = {}           # 插件模块 -> 它自己的状态 dict


def load_all():
    PLUGINS.clear()
    _STATES.clear()
    try:
        files = os.listdir(_PLUGIN_DIR)
    except OSError:
        print(f"[plugin] 没找到 {_PLUGIN_DIR} 目录，跳过插件加载")
        return PLUGINS

    for fname in sorted(files):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        modname = fname[:-3]
        try:
            mod = __import__(modname)
            PLUGINS.append(mod)
            _STATES[mod] = _init_state(mod)
            view_id = getattr(mod, "VIEW_ID", None)
            print(f"[plugin] 加载成功: {modname}" + (f" (view={view_id})" if view_id else " (无界面，纯事件处理)"))
        except Exception as e:
            print(f"[plugin] 加载失败: {modname}: {e}")
    return PLUGINS


def _init_state(mod):
    init_fn = getattr(mod, "init_state", None)
    try:
        return init_fn() if init_fn else {}
    except Exception as e:
        print(f"[plugin] {mod.__name__} init_state() 出错: {e}")
        return {}


def state_for(mod):
    return _STATES.setdefault(mod, _init_state(mod))


def views():
    """所有定义了 VIEW_ID 的插件，按加载顺序（文件名排序）"""
    return [p for p in PLUGINS if getattr(p, "VIEW_ID", None)]


def find_view(view_id):
    for p in views():
        if p.VIEW_ID == view_id:
            return p
    return None


def draw_view(view_id):
    p = find_view(view_id)
    if p is None:
        return False
    try:
        p.draw(state_for(p))
    except Exception as e:
        print(f"[plugin] {p.__name__} draw() 出错: {e}")
    return True


def hide_view(view_id):
    """离开某个插件的界面时调用一次，给插件一个清理机会。

    view_id 可能根本不是插件界面（比如时钟/通知这些核心界面），这种
    情况 find_view 找不到，直接安静返回 False，不算错误。

    插件没定义 on_hide 就什么都不做。
    """
    p = find_view(view_id)
    if p is None:
        return False
    hide_fn = getattr(p, "on_hide", None)
    if hide_fn is None:
        return True
    try:
        hide_fn(state_for(p))
    except Exception as e:
        print(f"[plugin] {p.__name__} on_hide() 出错: {e}")
    return True


def dispatch_keys(view_id, keys, screen):
    """当前视图如果正好是某个插件的界面，且这个插件定义了
    handle_keys，就把这轮按键先交给它。插件说自己处理了（返回 True）
    调用方就该跳过后面通用的 LEFT/RIGHT/UP/DOWN/ENTER 等逻辑；插件
    没定义这个钩子，或者对这轮按键不感兴趣（返回 False），就照常走
    默认逻辑。

    view_id 如果不是插件界面（核心的时钟/通知/音乐/状态），find_view
    找不到，直接安静返回 False。
    """
    p = find_view(view_id)
    if p is None:
        return False
    handler = getattr(p, "handle_keys", None)
    if handler is None:
        return False
    try:
        return bool(handler(keys, state_for(p), screen))
    except Exception as e:
        print(f"[plugin] {p.__name__} handle_keys() 出错: {e}")
        return False


def dispatch_event(t, data, screen):
    """依次问每个插件愿不愿意处理这个事件，第一个说处理了就停，
    返回 True/False 表示有没有插件接手。"""
    for p in PLUGINS:
        handler = getattr(p, "handle_event", None)
        if handler is None:
            continue
        try:
            if handler(t, data, state_for(p), screen):
                return True
        except Exception as e:
            print(f"[plugin] {p.__name__} handle_event() 出错: {e}")
    return False


def dispatch_notification_guess(app_name, title, body, screen):
    """普通通知进来时，依次问每个插件想不想从里面猜点什么（天气插件
    就是这么从"天气 App 弹的通知"里捞数据的，没有专门的 weather 事件
    时兜底用）。跟 handle_event 不一样，这个不是"谁处理了就停"，是
    每个插件都问一遍，因为多个插件可能都对同一条通知感兴趣。"""
    for p in PLUGINS:
        fn = getattr(p, "on_notification_guess", None)
        if fn is None:
            continue
        try:
            fn(app_name, title, body, state_for(p), screen)
        except Exception as e:
            print(f"[plugin] {p.__name__} on_notification_guess() 出错: {e}")
