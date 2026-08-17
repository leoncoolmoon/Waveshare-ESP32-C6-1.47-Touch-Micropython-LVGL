"""This app lets you download new apps from the MicroHydra apps repo.

This built-in app was partially inspired by RealClearwave's "AppStore.py",
which was contributed to the MH apps repo with commit 014f080.
Thank you for your contributions!
"""



import json
import os
import sys
import time

import network
import requests

from lib.device import Device
from lib.hydra.config import Config
from lib.hydra.i18n import I18n
from lib.hydra.simpleterminal import SimpleTerminal
from lib.zipextractor import ZipExtractor
from lib.hydra import loader


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ _CONSTANTS: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
_MH_DISPLAY_HEIGHT = const(135)
_MH_DISPLAY_WIDTH = const(240)
_DISPLAY_WIDTH_HALF = const(_MH_DISPLAY_WIDTH // 2)

_CHAR_WIDTH = const(8)
_CHAR_WIDTH_HALF = const(_CHAR_WIDTH // 2)

_RETRYS = 30

_TRANS = const("""[
{"en": "Enabling wifi...", "zh": "正在启用wifi...", "ja": "WiFiを有効にしています..."},
{"en": "Connected!", "zh": "已连接!", "ja": "接続されました!"},
{"en": "Getting app catalog...", "zh": "获取应用目录中...", "ja": "アプリカタログを取得中..."},
{"en": "Failed to get catalog.", "zh": "获取目录失败。", "ja": "カタログの取得に失敗しました。"},
{"en": "Connecting to GitHub...", "zh": "正在连接到GitHub...", "ja": "GitHubに接続中..."},
{"en": "Failed to get app.", "zh": "获取应用失败。", "ja": "アプリの取得に失敗しました。"},
{"en": "Downloading zip...", "zh": "正在下载zip文件...", "ja": "zipファイルをダウンロード中..."},
{"en": "Finished downloading 'tempapp.zip'", "zh": "已完成下载 'tempapp.zip'", "ja": "'tempapp.zip' のダウンロードが完了しました"},
{"en": "Finished extracting.", "zh": "解压完成。", "ja": "解凍が完了しました。"},
{"en": "Removing 'tempapp.zip'...", "zh": "正在删除 'tempapp.zip'...", "ja": "'tempapp.zip' を削除しています..."},
{"en": "Failed to extract from zip file.", "zh": "从zip文件解压失败。", "ja": "zipファイルからの解凍に失敗しました。"},
{"en": "Done!", "zh": "完成!", "ja": "完了!"},
{"en": "Author:", "zh": "作者:", "ja": "著者:"},
{"en": "Description:", "zh": "描述:", "ja": "説明:"}
]""")  # noqa: E501

# ===== 延迟初始化：先只加载必要的模块 =====
CONFIG = Config()

NIC = network.WLAN(network.STA_IF)



# --------------------------------------------------------------------------------------------------
# -------------------------------------- function_definitions: -------------------------------------
# --------------------------------------------------------------------------------------------------

def goto_Setting():
    """跳转到设置页面（需要显示模块时再加载）"""
    # 延迟加载显示模块
    from lib.display import Display
    from lib import userinput
    
    DISPLAY = Display(use_tiny_buf=False)
    INPUT = userinput.UserInput()
    
    # 使用简单的打印，因为此时没有 TERM
    print("(Press any key to go to settings...)")
    time.sleep_ms(500)
    while not INPUT.get_new_keys():
        time.sleep_ms(10)
    loader.launch_app("/launcher/settings")


def check_wifi():
    """Verify WiFi has been configured, print error and exit if not."""
    if not CONFIG['wifi_ssid']:
        print("Error: WiFi SSID is blank!")
        print("Please use the Settings app to set up your WiFi access.")
        print('')
        goto_Setting()

def connect_wifi():
    """Connect to the configured WiFi network."""
    print("Enabling wifi...")

    if not NIC.active():
        NIC.active(True)

    if not NIC.isconnected():
        # tell wifi to connect (with FORCE)
        while True:
            try:  # keep trying until connect command works
                NIC.connect(CONFIG['wifi_ssid'], CONFIG['wifi_pass'])
                break
            except OSError as e:
                print(f"Error: {e}")
                time.sleep_ms(500)

        # now wait until connected
        attempts = 0
        while not NIC.isconnected() and attempts < _RETRYS:
            print(f"connecting... {attempts+1}")
            time.sleep_ms(1000)
            attempts += 1

    if NIC.isconnected():
        print("Connected!")

    else:
        print("Unable to connect!")
        goto_Setting()

def request_file(file_path: str) -> requests.Response:
    """Get the specific app file from GitHub."""
    print(f"{Device.name} Making request...")
    response = requests.get(
        f'https://raw.githubusercontent.com/echo-lalia/MicroHydra-Apps/main/catalog-output/{file_path}',
        headers={
            "accept": "application/vnd.github.v3.raw",
            "User-Agent": f"{Device.name} - MicroHydra",
        },
        timeout=30
    )
    print(f"Returned code: {response.status_code}")
    return response


def try_request_file(file_path: str) -> requests.Response:
    """Capture errors and keep trying to get requested file."""
    wait = 1  # time to wait between attempts (don't get rate limited)
    while True:
        try:
            return request_file(file_path)
        except (OSError, ValueError, MemoryError) as e:
            print(f"Request failed: {e}")
            

def fetch_app_catalog() -> dict:
    """Download compact app catalog from apps repo."""

    print("Getting app catalog...")

    response = try_request_file(f"{Device.name.lower()}.json")

    # 记录响应内容大小
    content_size = len(response.content)
    print(f"响应内容大小: {content_size} 字节 ({content_size/1024:.1f} KB)")
    try:
        result = json.loads(response.content)
        print(f"解析成功，应用数量: {len(result)-1}")
    except MemoryError as e:
        print(f"JSON 解析内存不足: {e}")
        raise
    finally:
        response.close()
        del response
    return result


_MAX_WBITS = const(15)
def fetch_app(app_name, mpy_matches, overlay,DISPLAY):
    """解析下载消息: DOWNLOADER:START:app_name:compiled"""
    overlay.draw_textbox("Downloading...")
    DISPLAY.show()
    compiled_path = "compiled" if mpy_matches else "raw"
    msg = f"DOWNLOADER:START:{app_name}:{compiled_path}"
    loader.launch_app("/lib/hydra/downloader", msg)


# ===== 现在加载显示相关模块（在获取数据后） =====
def init_display_modules():
    """延迟加载显示、输入、终端和国际化模块"""
    from lib.display import Display
    from lib import userinput
    from lib.hydra.simpleterminal import SimpleTerminal
    from lib.hydra import popup
    
    DISPLAY = Display(use_tiny_buf=False)
    INPUT = userinput.UserInput()
    TERM = SimpleTerminal()    
    I18N = I18n(_TRANS)
    overlay = popup.UIOverlay(i18n=I18N)
    
    return DISPLAY, INPUT, TERM, I18N, overlay

# --------------------------------------------------------------------------------------------------
# ---------------------------------------- ClassDefinitions: ---------------------------------------
# --------------------------------------------------------------------------------------------------

_AUTHOR_Y = const(_MH_DISPLAY_HEIGHT // 2)
_NAME_Y = const(_MH_DISPLAY_HEIGHT // 4 - 8)
_DESC_Y = const(_AUTHOR_Y + _NAME_Y + 6)
_MAX_H_CHARS = const(_MH_DISPLAY_WIDTH // 8)


class CatalogDisplay:
    """Construct for displaying and selecting catalog options."""

    def __init__(self, catalog: dict, display, input_obj, term, i18n):
        """Create a Catalog using given dict."""
        self.DISPLAY = display
        self.INPUT = input_obj
        self.TERM = term
        self.I18N = i18n
        self.CONFIG = CONFIG 
        self.mpy_version = catalog.pop("mpy_version")

        self.names = list(catalog.keys())
        # sort alphabetically without uppercase/lowercase discrimination:
        self.names.sort(key=lambda st: st.lower())

        self.catalog = catalog

        self.idx = 0

    def move(self, val: int):
        """Move the selector index by `val`."""
        self.idx += val
        self.idx %= len(self.names)


    def jump_to(self, letter):
        """Jump to the next app that starts with the given letter."""
        # search for that letter in the app list
        for i in range(1, len(self.names)):
            # scan to the right, starting at self.idx
            i = (i + self.idx) % len(self.names)
            name = self.names[i]
            if name.lower().startswith(letter):
                self.idx = i
                return


    @staticmethod
    def split_lines(text: str) -> list:
        """Split a string into multiple lines, based on max line-length."""
        lines = []
        current_line = ''
        words = text.split()

        for word in words:
            if len(word) + len(current_line) >= _MAX_H_CHARS:
                lines.append(current_line)
                current_line = word
            elif len(current_line) == 0:
                current_line += word
            else:
                current_line += ' ' + word

        lines.append(current_line)  # add final line

        return lines


    def draw(self):
        """Draw the selected option to the display."""
        name = self.names[self.idx]
        # separate author
        *desc, author = self.catalog[name].split(' - ')
        desc = ' - '.join(desc)

        self.DISPLAY.fill(self.CONFIG.palette[2])
        self.DISPLAY.rect(0, _NAME_Y - 8, _MH_DISPLAY_WIDTH, 24, self.CONFIG.palette[3], fill=True)

        self.DISPLAY.text('<', 8, _NAME_Y, self.CONFIG.palette[4])
        self.DISPLAY.text('>', _MH_DISPLAY_WIDTH - 16, _NAME_Y, self.CONFIG.palette[4])
        self.DISPLAY.text(name, _DISPLAY_WIDTH_HALF - (len(name) * 4), _NAME_Y+1, self.CONFIG.palette[5])
        self.DISPLAY.text(name, _DISPLAY_WIDTH_HALF - (len(name) * 4), _NAME_Y, self.CONFIG.palette[8])

        self.DISPLAY.text(self.I18N["Author:"], _DISPLAY_WIDTH_HALF - 28, _AUTHOR_Y - 14, self.CONFIG.palette[3])
        self.DISPLAY.text(author, _DISPLAY_WIDTH_HALF - (len(author) * 4), _AUTHOR_Y, self.CONFIG.palette[5])

        self.DISPLAY.text(self.I18N["Description:"], _DISPLAY_WIDTH_HALF - 48, _DESC_Y - 14, self.CONFIG.palette[3])
        desc_y = _DESC_Y
        desc_lines = self.split_lines(desc)
        for line in desc_lines:
            self.DISPLAY.text(line, _DISPLAY_WIDTH_HALF - (len(line) * 4), desc_y, self.CONFIG.palette[6])
            desc_y += 9


# --------------------------------------------------------------------------------------------------
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Main Loop: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def main_loop():
    str_msg = loader.get_args()[0]
 
    # 检查下载完成
    if str_msg.startswith("DOWNLOADER_DONE:"):
        app_name = str_msg.replace("DOWNLOADER_DONE:", "")
        print(f"[GETAPPS] 应用下载完成: {app_name}")
        show_done_msg = app_name
    elif str_msg.startswith("DOWNLOADER_ERROR:"):
        # 解析错误信息
        parts = str_msg.split(':')
        if len(parts) >= 3:
            error_type = parts[1]
            app_name = parts[2] if len(parts) > 2 else "unknown"
            print(f"[GETAPPS] 下载失败: {error_type} - {app_name}")
        show_done_msg = None
    else:
        show_done_msg = None

    """Run the main loop of the program."""

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ INITIALIZATION: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    check_wifi()
    connect_wifi()
    catalog = fetch_app_catalog()

    DISPLAY, INPUT, TERM, I18N, overlay = init_display_modules()
    mpy_str = f"{sys.implementation._mpy & 0xff}.{sys.implementation._mpy >> 8 & 3}"
    mpy_matches = (mpy_str == catalog["mpy_version"])
    print (overlay)
    # sleep so user can see confirmation message
    time.sleep_ms(400)

    catalog_display = CatalogDisplay(catalog, DISPLAY, INPUT, TERM, I18N)
    catalog_display.draw()

    # Add usage hint
    DISPLAY.text(
        "Select an app to download:",
        _DISPLAY_WIDTH_HALF - 104,
        2,
        CONFIG.palette[0],
    )
    DISPLAY.text(
        "Press backspace to exit",
        _DISPLAY_WIDTH_HALF - 92,
        _MH_DISPLAY_HEIGHT-10,
        CONFIG.palette[0],
    )
    DISPLAY.show()
    if show_done_msg:
        overlay.draw_textbox(f"✓ {show_done_msg} installed!")
        DISPLAY.show()
        time.sleep(2)

    while True:
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ INPUT: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # get list of newly pressed keys
        keys = INPUT.get_new_keys()
        INPUT.ext_dir_keys(keys)

        # if there are keys, convert them to a string, and store for display
        if keys:
            for key in keys:
                if key == 'RIGHT':
                    catalog_display.move(1)
                elif key == 'LEFT':
                    catalog_display.move(-1)
                elif key in {'G0', 'ENT', 'SPC'}:
                    fetch_app(catalog_display.names[catalog_display.idx], mpy_matches, overlay, DISPLAY)
                    time.sleep(2)

                elif key in {'ESC', 'BSPC'}:
                    NIC.active(False)
                    loader.launch_app("")

                elif len(key) == 1:
                    catalog_display.jump_to(key)

            catalog_display.draw()
            DISPLAY.show()


        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ HOUSEKEEPING: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # do nothing for 10 milliseconds
        time.sleep_ms(10)



# start the main loop
main_loop()
