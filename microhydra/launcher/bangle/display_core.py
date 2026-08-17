# display_core.py -- 显示相关的全局对象/常量，不含任何事件处理逻辑。
#
# 其它模块（包括插件）统一用 `from display_core import *` 拿
# DISPLAY/PALETTE/CONFIG/INPUT/RTC/time 以及各种尺寸常量和 VIEW_* 视图
# id，不用各自重复 import lib.display / lib.hydra.config。
#
# 早期单文件版本里这些东西都是模块级全局变量，散在文件最前面；拆分
# 成多文件后必须集中到一个大家都能安全 `import *` 的地方，这里就是
# 那个地方。

from lib import display, userinput
from lib.hydra import config
import machine
import time

DISPLAY = display.Display()
CONFIG = config.Config()
INPUT = userinput.UserInput()
RTC = machine.RTC() if machine is not None else None

# 调色板缓存一份引用，代码里统一用 PALETTE[n] 取色，不用每次都敲
# CONFIG.palette[n]（跟早期版本里直接用 CONFIG.palette[n] 效果一样，
# 只是换个更短的名字，其它模块引用起来更方便）。
PALETTE = CONFIG.palette

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ 常量: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# 注意：这里故意不用 const(...)。MicroPython 的 const() 只在“同一个
# 文件内”做编译期内联替换；如果这个常量在本文件里也被别处引用了
# （比如下面 _CONTENT_HEIGHT 就用到了 _MH_DISPLAY_HEIGHT），编译器会
# 把那些引用替换成字面量，然后发现 `_MH_DISPLAY_HEIGHT = const(135)`
# 这条赋值本身已经"没人用"了，直接把它优化掉——模块对象上就不会真的
# 有 _MH_DISPLAY_HEIGHT 这个属性，导致别的文件 `from display_core
# import *` 或 `display_core._MH_DISPLAY_HEIGHT` 直接 AttributeError。
# 早期单文件版本从来没暴露过这个问题，是因为所有引用都在同一个文件
# 里，全靠编译期内联解决，没人真的需要在运行时从模块外部读这个名字。
# 拆成多文件之后，这些常量必须是普通变量才能被其它模块正常 import。
_MH_DISPLAY_HEIGHT = 135
_MH_DISPLAY_WIDTH = 240

_CHAR_WIDTH = 8
_LINE_HEIGHT = 12  # 稍微增加行高，提高可读性

_MAX_CHARS_PER_LINE = _MH_DISPLAY_WIDTH // _CHAR_WIDTH
_MAX_LINES = _MH_DISPLAY_HEIGHT // _LINE_HEIGHT

# 状态栏高度（顶部固定）
_STATUS_BAR_HEIGHT = 20
# 底部指示器高度
_BOTTOM_INDICATOR_HEIGHT = 12
# 内容区域
_CONTENT_Y_START = _STATUS_BAR_HEIGHT
_CONTENT_HEIGHT = _MH_DISPLAY_HEIGHT - _STATUS_BAR_HEIGHT - _BOTTOM_INDICATOR_HEIGHT
_CONTENT_MAX_LINES = _CONTENT_HEIGHT // _LINE_HEIGHT

# 通知卡片（标题/分隔线/内容/边框）布局
_CARD_MARGIN_X = 4
_CARD_PADDING = 4
_CARD_GAP = 4

# 核心视图 id：时钟/通知/音乐/状态，这四个是 screen_manager.py 自己画的，
# 不经过插件。天气之类原本写死在单文件里的功能，现在都改成插件自己
# 定义 VIEW_ID（见 plugins/weather.py），不再写死在这里 -- 这样以后
# 加新插件视图完全不用改这个文件。
VIEW_CLOCK = "clock"
VIEW_NOTIFICATIONS = "notifications"
VIEW_MUSIC = "music"
VIEW_STATUS = "status"

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WEEKDAY_NAMES_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

ICON_NOTIFICATION = "📬"
ICON_MUSIC = "🎵"
ICON_STATUS = "📊"

# 纯黑，跟主题палette 无关，用于统一整个 app 的基础背景色（无论用户在
# MicroHydra 系统设置里选了哪套主题，这块表都用纯黑背景，省电也更
# 一致）。RGB565 里黑色就是 0，跟具体的 565/888 之类的格式细节无关。
_COLOR_BLACK = 0x0000

# 显式 __all__ -- `from display_core import *` 默认不会带下划线开头的
# 名字（_MH_DISPLAY_WIDTH 这些），但插件和 screen_manager 全靠这些
# 常量，所以必须手动列出来，否则各处会一堆 NameError。
__all__ = [
    'DISPLAY', 'CONFIG', 'INPUT', 'RTC', 'PALETTE', 'time', 'machine',
    '_MH_DISPLAY_HEIGHT', '_MH_DISPLAY_WIDTH', '_CHAR_WIDTH', '_LINE_HEIGHT',
    '_MAX_CHARS_PER_LINE', '_MAX_LINES', '_STATUS_BAR_HEIGHT',
    '_BOTTOM_INDICATOR_HEIGHT', '_CONTENT_Y_START', '_CONTENT_HEIGHT',
    '_CONTENT_MAX_LINES', '_CARD_MARGIN_X', '_CARD_PADDING', '_CARD_GAP',
    'VIEW_CLOCK', 'VIEW_NOTIFICATIONS', 'VIEW_MUSIC', 'VIEW_STATUS',
    '_WEEKDAY_NAMES', '_WEEKDAY_NAMES_SHORT',
    'ICON_NOTIFICATION', 'ICON_MUSIC', 'ICON_STATUS', '_COLOR_BLACK',
]
