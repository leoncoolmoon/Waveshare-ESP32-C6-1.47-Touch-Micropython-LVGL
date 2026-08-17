"""HyDE v2.x editor class."""
if __name__ == '__main__': from launcher import editor  # relative import for testing

import sys
import time
import machine

from .filelines import FileLines
from .displayline import DisplayLine
from .cursor import Cursor
from .undomanager import UndoManager

from esp32 import NVS
from lib.sdcard import SDCard
from lib.display import Display
from lib.hydra.config import Config
from lib.userinput import UserInput
from lib.hydra.statusbar import StatusBar
from lib.hydra.popup import UIOverlay
from lib.hydra import loader


# Statusbar stuff
_MH_DISPLAY_WIDTH = const(240)
_FONT_HEIGHT = const(8)
_FONT_WIDTH = const(8)

_STATUSBAR_HEIGHT = const(18)

_STATUSBAR_TEXT_Y = const((_STATUSBAR_HEIGHT - _FONT_HEIGHT) // 2)
_STATUSBAR_TEXT_X = const(4)
_STATUSBAR_TEXT_WIDTH = const((_MH_DISPLAY_WIDTH - _STATUSBAR_TEXT_X)//3 * 2)
_STATUSBAR_TEXT_CHARS = const(_STATUSBAR_TEXT_WIDTH // _FONT_WIDTH)


_DELETE_FLAG = const(0)
_INSERT_FLAG = const(1)


_ARROW_KEYS = {"LEFT", "RIGHT", "UP", "DOWN"}

# rare whitespace char is repurposed here to denote converted tab/space indents
_INDENT_SYM = const(' ')  # noqa: RUF001


# used for "exit to file browser" option:
# mh_if frozen:
# _FILE_BROWSER = const(".frozen/launcher/files")
# mh_else:
_FILE_BROWSER = const("/launcher/files")
# mh_end_if



# increased to full freq.
#machine.freq(240_000_000)
# sd needs to be mounted for any files in /sd
SDCard().mount()



class Editor:
    """Main editor class."""

    def __init__(self):
        """Initialize HyDE."""
        self.display = Display()
        self.config = Config()
        self.overlay = UIOverlay()#载入了键盘
        tokenizer.init(self.config)

        self.statusbar = StatusBar(register_overlay=False)
        #self.inpt = UserInput(allow_locking_keys=True, skip_hardware_init=True)#再次载入了键盘
        self.inpt = self.overlay.kb
        self.cursor = Cursor()
        self.select_cursor = None
        self.clipboard = ""
        self.undomanager = UndoManager(self, self.cursor)

        self.lines = None

        self.filepath = None
        self.modified = False
        
        # 搜索相关属性
        self.search_term = ""
        self.search_direction = 1  # 1 for forward, -1 for backward
        self.search_start_cursor = None  # 保存搜索起始位置
        
        # 选择模式标志
        self.selection_mode = False  # True表示正在选择中


    def save(self):
        """Save the file, display some status text."""
        if not self.modified:
            return
        self.overlay.draw_textbox("Saving...")
        self.display.show()
        self.lines.save(self.filepath)
        self.modified = False


    def open_file(self, filepath: str):
        """Open the given text file."""
        with open(filepath) as f:
            self.lines = FileLines(f.readlines())
        self.filepath = filepath


    def handle_move_selection(self, key):
        """Handle movement of selection cursor.

        select_cursor is the active/moving cursor while selecting (FileLines.draw
        follows select_cursor for scrolling/current-line highlighting when a
        selection exists), while self.cursor stays fixed as the anchor where
        the selection started.
        """
        # Make a new selection cursor if one doesn't exist
        if self.select_cursor is None:
            self.select_cursor = Cursor()
            self.select_cursor.x = self.cursor.x
            self.select_cursor.y = self.cursor.y

        # Move the selection cursor
        if key == "LEFT":
            self.select_cursor.move(self.lines, x=-1)
        elif key == "RIGHT":
            self.select_cursor.move(self.lines, x=1)
        elif key == "UP":
            self.select_cursor.move(self.lines, y=-1)
        else: # key == "DOWN":
            self.select_cursor.move(self.lines, y=1)


    def file_options(self):
        """Give file options menu."""
        choice = self.overlay.popup_options(["Back", "Save", "Tab...", "Run...", "Select...", "GoTo..."], title="GO...")

        if choice == "Save":
            self.save()
        elif choice == "Run...":
            self.run_options()
        elif choice == "GoTo...":
            self.exit_options()
        elif choice == "Tab...":
            self.tab_options()
        elif choice == "Select...":
            self.select_options()


    def select_options(self):
        """Give selection options menu."""
        if self.select_cursor is not None:
            # 有选择时，显示"停止选择"选项
            choice = self.overlay.popup_options(["Cancel", "Post Selection Option"], title="Selection Active", depth=1)
            
            if choice == "Post Selection Option":
                self.selection_actions()  # 进入下一级菜单
        else:
            # 无选择时，显示开始选择和全选            
            choice = self.overlay.popup_options(["Cancel", "Start Selection", "Select All"], title="Select...", depth=1)
            
            if choice == "Start Selection":
                self.start_selection()
            elif choice == "Select All":
                self.select_all()


    def selection_actions(self):
        """Show selection action menu (copy, cut, paste, delete)."""
        choice = self.overlay.popup_options(["Cancel", "Copy", "Cut", "Paste", "Delete"], title="Selection Actions", depth=2)
        
        if choice == "Cancel":
            self.stop_selection()
        elif choice == "Copy":
            self.copy_selection()
        elif choice == "Cut":
            self.cut_selection()
        elif choice == "Paste":
            self.paste_clipboard()
        elif choice == "Delete":
            self.delete_selection()


    def start_selection(self):
        """Start selection mode."""
        self.select_cursor = Cursor()
        self.select_cursor.x = self.cursor.x
        self.select_cursor.y = self.cursor.y
        self.selection_mode = True  # 进入选择模式
        self.overlay.draw_textbox("Selection started")
        self.display.show()
        time.sleep_ms(300)


    def stop_selection(self):
        """Stop selection mode but keep selection."""
        self.selection_mode = False  # 退出选择模式
        self.overlay.draw_textbox("Selection stopped")
        self.display.show()
        time.sleep_ms(300)


    def select_all(self):
        """Select all text in the file."""
        self.select_cursor = Cursor()
        self.select_cursor.x = 0
        self.select_cursor.y = 0
        self.cursor.x = len(self.lines.lines[-1]) if self.lines.lines else 0
        self.cursor.y = len(self.lines.lines) - 1 if self.lines.lines else 0
        self.selection_mode = False  # 全选后退出选择模式


    def copy_selection(self):
        """Copy selected text to clipboard."""
        if self.select_cursor is not None:
            self.clipboard = self.lines.get_selected_text(self.cursor, self.select_cursor)
            self.overlay.draw_textbox("Copied")
            self.display.show()
            time.sleep_ms(300)
        else:
            self.overlay.error("No selection")


    def cut_selection(self):
        """Cut selected text to clipboard."""
        if self.select_cursor is not None:
            self.clipboard = self.lines.get_selected_text(self.cursor, self.select_cursor)
            self.undomanager.record(
                "insert",
                self.clipboard,
                cursor=min(self.cursor, self.select_cursor),
            )
            self.lines.delete_selected_text(self.cursor, self.select_cursor)
            self.select_cursor = None
            self.selection_mode = False
            self.modified = True
            self.overlay.draw_textbox("Cut")
            self.display.show()
            time.sleep_ms(300)
        else:
            self.overlay.error("No selection")


    def paste_clipboard(self):
        """Paste clipboard content at cursor position."""
        if not self.clipboard:
            self.overlay.error("Clipboard empty")
            return
        
        # 如果有选择，先删除选择
        if self.select_cursor is not None:
            self.undomanager.record(
                "insert",
                self.lines.get_selected_text(self.cursor, self.select_cursor),
                cursor=min(self.cursor, self.select_cursor),
            )
            self.lines.delete_selected_text(self.cursor, self.select_cursor)
            self.select_cursor = None
            self.selection_mode = False
        
        # 插入剪贴板内容
        for char in self.clipboard:
            self.lines.insert(char, self.cursor)
        self.undomanager.record("backspace", self.clipboard)
        self.modified = True
        self.overlay.draw_textbox("Pasted")
        self.display.show()
        time.sleep_ms(300)


    def delete_selection(self):
        """Delete selected text without copying to clipboard."""
        if self.select_cursor is not None:
            self.undomanager.record(
                "insert",
                self.lines.get_selected_text(self.cursor, self.select_cursor),
                cursor=min(self.cursor, self.select_cursor),
            )
            self.lines.delete_selected_text(self.cursor, self.select_cursor)
            self.select_cursor = None
            self.selection_mode = False
            self.modified = True
            self.overlay.draw_textbox("Deleted")
            self.display.show()
            time.sleep_ms(300)
        else:
            self.overlay.error("No selection")


    def tab_options(self):
        """Give tab options menu."""
        title = "'tab' inserts tabs" if self.lines.use_tabs else "'tab' inserts spaces"
        _TAB_OPTIONS = const(("Back", "Use tabs", "Use spaces"))

        choice = self.overlay.popup_options(_TAB_OPTIONS, title=title, depth=1)
        nvs = NVS("editor")

        if choice == "Use tabs":
            self.lines.use_tabs = True
            nvs.set_i32("use_tabs", True)
            nvs.commit()

        elif choice == "Use spaces":
            self.lines.use_tabs = False
            nvs.set_i32("use_tabs", False)
            nvs.commit()


    def run_options(self):
        """Give run options submenu."""
        _RUN_OPTIONS = const(("Cancel", "Run here", "Restart and run"))
        choice = self.overlay.popup_options(_RUN_OPTIONS, title="Run...", depth=1)

        if choice == "Run here":
            self.run_file_here()
        elif choice == "Restart and run":
            self.boot_into_file(self.filepath)


    def jump_to_line(self):
        """Jump to a specific line number."""
        current_line = self.cursor.y + 1  # 转换为1-based行号
        line_input = self.overlay.text_entry(
            title=f"Jump to line({current_line}):"
        )
        
        if not line_input:  # 用户取消
            return
            
        try:
            target_line = int(line_input) - 1  # 转换为0-based索引
            if 0 <= target_line < len(self.lines.lines):
                self.cursor.y = target_line
                # 保持x坐标在有效范围内
                if self.cursor.x >= len(self.lines.lines[target_line]):
                    self.cursor.x = len(self.lines.lines[target_line])
                self.select_cursor = None
                self.selection_mode = False
            else:
                self.overlay.error(f"Line {target_line + 1} out of range (1-{len(self.lines.lines)})")
        except ValueError:
            self.overlay.error("Invalid line number")


    def find_text(self):
        """Find text in the file."""
        if not self.search_term:
            # 首次搜索，获取搜索词
            self.search_term = self.overlay.text_entry(
                start_value="", 
                title="Find:"
            )
            if not self.search_term:  # 用户取消或输入为空
                self.search_term = ""
                return
            self.search_start_cursor = Cursor()
            self.search_start_cursor.x = self.cursor.x
            self.search_start_cursor.y = self.cursor.y
            self.search_direction = 1  # 默认向前搜索
        
        # 执行搜索
        found = self.find_next_occurrence()
        
        if found:
            # 高亮显示找到的文本（通过选择光标）
            self.select_cursor = Cursor()
            self.select_cursor.x = self.cursor.x + len(self.search_term)
            self.select_cursor.y = self.cursor.y
            # 更新搜索起始位置为当前位置
            self.search_start_cursor.x = self.cursor.x
            self.search_start_cursor.y = self.cursor.y
            self.selection_mode = False  # 搜索高亮不使用选择模式
        else:
            self.overlay.error(f"'{self.search_term}' not found")
            # 重置搜索状态
            self.search_term = ""
            self.search_start_cursor = None


    def find_next_occurrence(self):
        """Find the next occurrence of search_term from current cursor position."""
        if not self.search_term:
            return False
        
        # 获取当前行和列
        line_idx = self.cursor.y
        col_idx = self.cursor.x
        
        # 如果是从搜索起始位置开始，并且当前位置在起始位置之后，从当前位置+1开始搜索
        if self.search_start_cursor is not None:
            if (line_idx > self.search_start_cursor.y or 
                (line_idx == self.search_start_cursor.y and col_idx >= self.search_start_cursor.x)):
                col_idx += 1
        
        # 从当前位置开始查找
        for i in range(line_idx, len(self.lines.lines)):
            line = self.lines.lines[i]
            # 如果和当前行在同一行，从col_idx开始查找
            if i == line_idx:
                start_pos = col_idx
            else:
                start_pos = 0
            
            # 查找搜索词
            pos = line.find(self.search_term, start_pos)
            if pos != -1:
                # 找到匹配
                self.cursor.y = i
                self.cursor.x = pos
                return True
        
        # 如果到达文件末尾还没有找到，从文件开头重新搜索
        if self.search_direction == 1:
            for i in range(0, line_idx + 1):
                line = self.lines.lines[i]
                if i == line_idx:
                    end_pos = col_idx
                    pos = line.find(self.search_term, 0, end_pos)
                else:
                    pos = line.find(self.search_term)
                
                if pos != -1:
                    self.cursor.y = i
                    self.cursor.x = pos
                    return True
        
        return False


    def find_next(self):
        """Find next occurrence of current search term."""
        if not self.search_term:
            self.find_text()
            return
        
        self.search_direction = 1
        self.find_text()


    def find_prev(self):
        """Find previous occurrence of current search term."""
        if not self.search_term:
            self.find_text()
            return
        
        self.search_direction = -1
        # 对于反向搜索，从当前位置往前找
        line_idx = self.cursor.y
        col_idx = self.cursor.x
        
        # 从当前位置往前搜索
        for i in range(line_idx, -1, -1):
            line = self.lines.lines[i]
            if i == line_idx:
                end_pos = col_idx
                pos = line.rfind(self.search_term, 0, end_pos)
            else:
                pos = line.rfind(self.search_term)
            
            if pos != -1:
                self.cursor.y = i
                self.cursor.x = pos
                # 高亮显示找到的文本
                self.select_cursor = Cursor()
                self.select_cursor.x = self.cursor.x + len(self.search_term)
                self.select_cursor.y = self.cursor.y
                self.selection_mode = False
                return True
        
        # 如果到达文件开头还没有找到，从文件末尾重新搜索
        for i in range(len(self.lines.lines) - 1, line_idx - 1, -1):
            line = self.lines.lines[i]
            if i == line_idx:
                start_pos = col_idx
                pos = line.rfind(self.search_term, start_pos)
            else:
                pos = line.rfind(self.search_term)
            
            if pos != -1:
                self.cursor.y = i
                self.cursor.x = pos
                self.select_cursor = Cursor()
                self.select_cursor.x = self.cursor.x + len(self.search_term)
                self.select_cursor.y = self.cursor.y
                self.selection_mode = False
                return True
        
        self.overlay.error(f"'{self.search_term}' not found")
        return False


    def search_options(self):
        """Give search options submenu."""
        _SEARCH_OPTIONS = const(("Cancel", "Find", "Find Next", "Find Prev", "Clear Search"))
        
        # 显示当前搜索状态
        title = "Search"
        if self.search_term:
            title += f" [{self.search_term}]"
        
        choice = self.overlay.popup_options(_SEARCH_OPTIONS, title=title, depth=2)

        if choice == "Find":
            self.find_text()
        elif choice == "Find Next":
            self.find_next()
        elif choice == "Find Prev":
            self.find_prev()
        elif choice == "Clear Search":
            self.clear_search()


    def clear_search(self):
        """Clear current search term and selection."""
        self.search_term = ""
        self.search_start_cursor = None
        self.select_cursor = None
        self.selection_mode = False
        self.overlay.draw_textbox("Search cleared")
        self.display.show()
        time.sleep_ms(500)  # 短暂显示提示


    def exit_options(self):
        """Give exit options submenu."""
        _EXIT_OPTIONS = const(("Cancel", "Search", "Jump to Line", "Exit to Files", "Exit to Launcher"))

        choice = self.overlay.popup_options(_EXIT_OPTIONS, title="Exit...", depth=1)

        if choice == "Exit to Files":
            if self.modified:
                choice = self.overlay.popup_options(("Save", "Discard"), title="Save changes?")
                if choice == "Save":
                    self.save()
            self.boot_into_file(_FILE_BROWSER)

        elif choice == "Exit to Launcher":
            choice = self.overlay.popup_options(("Save", "Discard"), title="Save changes?")
            if choice == "Save":
                self.save()
            self.boot_into_file('')
            
        elif choice == "Jump to Line":
            self.jump_to_line()
            
        elif choice == "Search":
            self.search_options()


    def boot_into_file(self, target_file):
        """Restart and load into target file."""
        self.overlay.draw_textbox("Restarting...")
        self.display.show()
        loader.launch_app(target_file)


    def run_file_here(self):
        """Try running the target file here."""
        self.save()
        self.overlay.draw_textbox("Running...")
        self.display.show()
        try:
            # you have to slice off the ".py" to avoid importerror
            mod = __import__(self.filepath[:-3])
            # we need to unload the module to import it again later.
            mod_name = mod.__name__
            if mod_name in sys.modules:
                del sys.modules[mod_name]

        except Exception as e:  # noqa: BLE001
            self.overlay.error(f"File closed with error: {e}")



    def _delete_and_record_selection(self):
        """Delete (and record undo step for) any selected text."""
        if self.select_cursor is not None:
            self.undomanager.record(
                "insert",
                self.lines.get_selected_text(self.cursor, self.select_cursor),
                cursor=min(self.cursor, self.select_cursor),
            )
            self.lines.delete_selected_text(self.cursor, self.select_cursor)
        self.select_cursor = None
        self.selection_mode = False
        self.modified = True


    def _insert_and_record(self, text):
        """Insert some text, and record an undo step for it."""
        self.lines.insert(text, self.cursor)
        self.undomanager.record("backspace", text)


    def handle_input(self, keys):  # noqa: PLR0912, PLR0915
        """Respond to user input."""
        mod_keys = self.inpt.get_mod_keys()

        for key in keys:
            if "CTL" in mod_keys:
                # CTRL kb commands
                if key == "LEFT":
                    self.cursor.jump(self.lines, x=-1)
                    self.select_cursor = None
                    self.selection_mode = False
                elif key == "RIGHT":
                    self.cursor.jump(self.lines, x=1)
                    self.select_cursor = None
                    self.selection_mode = False
                elif key == "UP":
                    self.cursor.move(self.lines, y=-5)
                    self.select_cursor = None
                    self.selection_mode = False
                elif key == "DOWN":
                    self.cursor.move(self.lines, y=5)
                    self.select_cursor = None
                    self.selection_mode = False


                # Undo/redo
                elif key == "z":
                    self.undomanager.undo()
                elif key in {'y', 'Z'}: # Allow both ctrl+y and ctrl+shift+z
                    self.undomanager.redo()


                # Save file
                elif key == "s":
                    self.save()


                # Clipboard
                elif key == "c":
                    if self.select_cursor is not None:
                        self.clipboard = self.lines.get_selected_text(self.cursor, self.select_cursor)

                elif key == "x":
                    if self.select_cursor is not None:
                        self.clipboard = self.lines.get_selected_text(self.cursor, self.select_cursor)
                        self.undomanager.record(
                            "insert",
                            self.clipboard,
                            cursor=min(self.cursor, self.select_cursor),
                        )
                        self.lines.delete_selected_text(self.cursor, self.select_cursor)
                        self.select_cursor = None
                        self.selection_mode = False
                        self.modified = True

                elif key == "v":
                    self._delete_and_record_selection()
                    # Chars have to be inserted individually so that line breaks work correctly.
                    # (in the future, it might be good to add a method for splitting text by newlines instead)
                    for char in self.clipboard:
                        self.lines.insert(char, self.cursor)
                    self.undomanager.record("backspace", self.clipboard)
                    self.select_cursor = None
                    self.selection_mode = False
                    self.modified = True


                elif key == "BSPC":
                    if self.select_cursor is not None:
                        self._delete_and_record_selection()
                        self.selection_mode = False
                    else:
                        self.cursor.jump(self.lines, x=-1, delete=True, undomanager=self.undomanager)
                    self.modified = True


            else:  # noqa: PLR5501
                # Normal keypress
                if key in _ARROW_KEYS:
                    # Directional input moves main, or selection cursor
                    if "SHIFT" in mod_keys or self.selection_mode:
                        # 如果按Shift键或者处于选择模式，移动选择光标
                        self.handle_move_selection(key)
                    else:
                        # 否则移动主光标并清除选择
                        self.select_cursor = None
                        self.selection_mode = False
                        if key == "LEFT":
                            self.cursor.move(self.lines, x=-1)
                        elif key == "RIGHT":
                            self.cursor.move(self.lines, x=1)
                        elif key == "UP":
                            self.cursor.move(self.lines, y=-1)
                        else: # key == "DOWN":
                            self.cursor.move(self.lines, y=1)


                elif key == "BSPC":
                    if self.select_cursor is not None:
                        self._delete_and_record_selection()
                        self.selection_mode = False
                    else:
                        # If we are at the start of the line, we should record a deleted line,
                        # otherwise just record the character before this one
                        if self.cursor.x == 0 and self.cursor.y > 0:
                            deleted_char = "\n"
                        else:
                            deleted_char = self.lines.get_char_left_of_cursor(self.cursor)
                        self.lines.backspace(self.cursor)
                        self.undomanager.record("insert", deleted_char)
                    self.modified = True


                elif key == "G0":
                    self.file_options()


                elif key == "ENT":
                    # Line-break-specific logic
                    self._delete_and_record_selection()
                    # Get the current indentation level to automatically add indents
                    indentation = self.lines.get_indentation(self.cursor.y)
                    # If there is a colon to the left of the cursor, we should probably start an indented block.
                    if self.lines.get_char_left_of_cursor(self.cursor) == ":":
                        indentation += _INDENT_SYM
                    # Insert the line break, then any additional indentation
                    self._insert_and_record("\n")
                    self._insert_and_record(indentation)
                    self.selection_mode = False


                else:
                    # Normal char input
                    # Replace named keys with their input char
                    key = {
                        "SPC":" ",
                        "TAB":_INDENT_SYM,
                    }.get(key, key)

                    # Only insert single characters (filter other named keys)
                    if len(key) == 1:
                        self._delete_and_record_selection()
                        self._insert_and_record(key)
                        self.selection_mode = False



    def draw_statusbar(self):
        """Draw the statusbar with filepath."""
        # Draw statusbar base
        self.statusbar.draw(self.display)
        # blackout clock/text backing
        self.display.rect(
            _STATUSBAR_TEXT_X,
            _STATUSBAR_TEXT_Y,
            _STATUSBAR_TEXT_WIDTH,
            _FONT_HEIGHT,
            self.display.palette[4],
            fill=True,
        )

        # slice filepath to fit, and indicate a modified file
        filepath = self.filepath
        if self.modified:
            filepath += "*"

        # 添加搜索状态信息到状态栏
        if self.search_term:
            filepath += f" [{self.search_term}]"
        
        # 添加选择状态信息
        if self.selection_mode:
            filepath += " [SELECTING]"
        elif self.select_cursor is not None:
            filepath += " [SELECTED]"

        if len(filepath) > _STATUSBAR_TEXT_CHARS:
            filepath = "..." + filepath[len(filepath) - (_STATUSBAR_TEXT_CHARS - 3):]

        # Draw text
        self.display.text(
            filepath,
            _STATUSBAR_TEXT_X,
            _STATUSBAR_TEXT_Y+1,
            self.display.palette[2]
        )
        self.display.text(
            filepath,
            _STATUSBAR_TEXT_X,
            _STATUSBAR_TEXT_Y,
            self.display.palette[7]
        )

        # Tell display to redraw keyboard overlays
        Display.draw_overlays = True


    def main(self):
        """Run the text editor."""

        self.display.fill(self.display.palette[2])
        self.lines.update_display_lines(self.cursor, force_update=True)
        self.lines.draw(self.display, self.cursor, self.select_cursor)
        self.draw_statusbar()

        while True:
            keys = self.inpt.get_new_keys()

            if keys:
                self.handle_input(keys)
                self.lines.draw(
                    self.display,
                    self.cursor,
                    self.select_cursor,
                    # self.select_cursor if self.select_cursor is not None else self.cursor,
                )
                # Draw selection if it exists:
                # if self.select_cursor is not None:
                #     self.cursor.draw_selection_cursor(self.select_cursor, self.display, self.lines)
                # Update statusbar
                self.draw_statusbar()

            else:
                if Display.draw_overlays:
                    # If the keyboard overlay is being drawn, we should probably redraw our statusbar.
                    self.draw_statusbar()
                # To smooth things out, we'll only insert a delay if we aren't redrawing the lines
                time.sleep_ms(50)

            if self.select_cursor is not None:
                self.select_cursor.draw(self.display, self.lines)
            self.cursor.draw(self.display, self.lines)
            self.display.show()




# Start editor:
filepath = loader.get_args()[0]
if not filepath:
    filepath = "/config.json" # JUSTFORTESTING
#     filepath = "/apps/gameoflifemodified.py" # JUSTFORTESTING
#     filepath = "/testfile.py"

# Import a specific tokenizer depending on the file extension
if filepath.endswith(".py"):
    from .tokenizers import python as tokenizer
else:
    from .tokenizers import plaintext as tokenizer

# Pass the tokenizer to the DisplayLine
DisplayLine.tokenizer = tokenizer


# Create the editor
editor = Editor()

# Load the file and start the editor, but catch any errors to show on display (before raising)
try:
    editor.open_file(filepath)
    editor.main()
except Exception as e:
    editor.overlay.error(f"Editor encountered an error: {e}")
    raise