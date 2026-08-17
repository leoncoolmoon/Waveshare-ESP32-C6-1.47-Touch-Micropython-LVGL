# plugins/chat_llm.py -- 240x135 屏幕上的极简 LLM 单轮问答插件。
#
# ============================================================
# 架构：输入交给 lib.hydra.popup，等回复自己手绘
# ============================================================
# lib.hydra.popup.UIOverlay 提供的 text_entry()/popup_options()/popup()/
# error() 全是"阻塞式"的——自己起一个 while 循环等按键，按下 ENT/G0 才
# 返回。这一点对"输入问题"这一步完全没问题（本来就是在 main_loop 的
# 按键路径 handle_keys() 里触发的，阻塞多久都行，跟 calendar.py 那些
# 用法没区别）。
#
# 但"等 LLM 网络响应"这一步不一样：那是 handle_event() 被 BLE 那边异步
# 回调触发的，跟按键无关，很可能是在 BLE 的 IRQ/调度路径上执行的。在
# 这条路径上放一个会 time.sleep_ms 空转等按键的阻塞弹窗，有卡 BLE 栈
# 甚至干扰它调度的风险，没法验证安全，所以刻意不这么做：handle_event()
# 里只碰 state，画面交给我们自己在 draw() 里手绘，走跟 calendar.py 一样
# 的"改 state -> 设 dirty -> 下一帧 main_loop 自然重绘"路径。
#
#   splash 状态（常驻主页）：
#     ENTER -> 弹出 text_entry() 问问题（阻塞，中途随时按 G0 取消回到
#              splash，不发送）；提交后请求发出去，但**不**立刻跳转，
#              还停在 splash，只是文字从 "Press ENTER to ask" 换成
#              "Thinking..."，直到 handle_event 收到响应/出错/超时。
#   answer 状态（请求结束后才出现，带边框像个弹窗）：
#     手绘展示 "You: .../AI: ..."，UP/DOWN 翻长回答；G0 退回 splash
#     （同时清空 status/error，splash 文字变回最初那句）；ENTER 直接
#     追问下一句（同样弹 text_entry()，走完流程又会先回到
#     splash-Thinking 再到新的 answer 窗口）。
#
# ============================================================
# "/" 技能菜单
# ============================================================
# TextEntry 本身没有"输入中途插入菜单"的钩子，所以用一个很薄的子类
# _SkillTextEntry 覆盖它的 main()：字符循环里先检查是不是 "/"，是的话
# 弹一层 PopupOptions（depth=1，跟外层文本框在配色上区分开，这是
# popup.py 自己注释里写的"depth"的用途），选中后把预设文本追加进当前
# 输入内容，继续编辑。draw() 直接继承 TextEntry 的，不用重写。
#
# ============================================================
# 网络协议（已用 Gadgetbridge 真实源码核实：BangleJSDeviceSupport.java
# 的 handleHttp()，约第 866~992 行）
# ============================================================
#   手表 -> 手机：
#     {"t":"http", "id":"3", "url":"...", "method":"post",
#      "headers":{"Authorization":"Bearer sk-...","Content-Type":"application/json"},
#      "body":"<json.dumps() 之后的字符串，不是嵌套对象>"}
#   手机 -> 手表：
#     成功: {"t":"http","id":"3","resp":"<原始响应体字符串>"}
#     失败: {"t":"http","id":"3","err":"错误描述"}
#   前提：手机装的是 "Bangle.js Gadgetbridge"（带 INTERNET_ACCESS 编译
#   标记的版本），并且在 Gadgetbridge 设备设置里打开 "Allow Internet Access"。
#
#   文件名特意叫 chat_llm.py（字母序排在 http_plugin.py 前面），保证
#   plugin_manager 按文件名排序加载时这个插件先被 dispatch_event 问到；
#   响应 id 对不上自己发出去的请求就返回 False，让给 http_plugin.py。
#
# ============================================================
# 对话策略：单轮，不带上文，只写不读的本地历史
# ============================================================
# 每次发送只带 system_prompt（可选）+ 当前这一句，不拼历史——这台设备
# 是 ESP32-C6 上套 MicroPython 模拟 MicroHydra、再模拟 Bangle.js 协议
# 这么一层层叠上来的，内存/算力都紧张。完整问答记录写本地 NDJSON
# （收到响应/失败时才写一行），复盘查看交给外部程序，不在表上做。
from display_core import *
from bangle_utils import wrap_text
from __init__ import path as _this_dir
from lib.hydra import popup as _hydra_popup
import json
import os
import time

VIEW_ID = "LLMChat"
ICON = "AI"          # 底栏图标必须 ASCII（见 plugin_manager.py 顶部注释）
TITLE = "🤖 LLM Chat"

_CONFIG_PATH = "/llm_chat_config.json"
_SKILLS_PATH = "/llm_chat_skills.json"
_HISTORY_PATH = "/llm_chat_history.ndjson"
_HISTORY_MAX_BYTES = 200 * 1024  # 超过就把旧文件轮转成 .bak

_MAX_INPUT_LEN = 200
_TIMEOUT_MS = 30000

_ENTER_KEYS = ("ENT", "ENTER", "RETURN")  # popup.py 里用的是 "ENT"，这里
                                            # 多留几个别名以防不同固件版本命名不同
_ESC_KEYS = ("ESC",)


def init_state():
    return {
        "mode": "splash",        # splash / answer
        "current_q": "",
        "current_a": "",
        "status": "idle",        # idle / waiting / error
        "error": None,
        "answer_scroll": 0,
        "req_id": 0,
        "pending_req_id": None,
        "sent_at": None,
    }


# ---------------- 配置 / 技能预设读取 ----------------

def _load_config():
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except Exception as e:
        print("[chat_llm] 配置读取失败，请检查", _CONFIG_PATH, ":", e)
        return {}


def _load_skills():
    try:
        with open(_SKILLS_PATH) as f:
            data = json.load(f)
        items = data.get("skills", []) if isinstance(data, dict) else data
        # 过滤掉格式不对的条目，避免脏数据崩掉整个菜单
        return [s for s in items if isinstance(s, dict) and s.get("name") and s.get("text")]
    except Exception as e:
        print("[chat_llm] skills 读取失败（没配置的话这是正常的）:", e)
        return []


# ---------------- 本地历史记录（只写不读，查看交给外部程序） ----------------

def _now_epoch():
    if RTC is None:
        return None
    try:
        y, m, d, wk, h, mi, s, _sub = RTC.datetime()
        return int(time.mktime((y, m, d, h, mi, s, wk, 0)))
    except Exception:
        return None


def _maybe_rotate_history():
    try:
        size = os.stat(_HISTORY_PATH)[6]
    except Exception:
        return
    if size <= _HISTORY_MAX_BYTES:
        return
    bak_path = _HISTORY_PATH + ".bak"
    try:
        try:
            os.remove(bak_path)
        except Exception:
            pass
        os.rename(_HISTORY_PATH, bak_path)
        print("[chat_llm] 历史文件超过大小上限，已轮转到", bak_path)
    except Exception as e:
        print("[chat_llm] 历史文件轮转失败:", e)


def _append_history(question, answer, error):
    entry = {"ts": _now_epoch(), "q": question, "a": answer, "err": error}
    try:
        with open(_HISTORY_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print("[chat_llm] 写历史记录失败:", e)
        return
    _maybe_rotate_history()


# ---------------- "/" 技能菜单：TextEntry 的薄子类 ----------------

class _SkillTextEntry(_hydra_popup.TextEntry):
    """在 TextEntry 基础上加一条：输入中敲 "/" 弹出技能选择菜单，选中
    后把预设文本追加到当前已输入内容里，继续编辑。其余按键行为跟原版
    TextEntry.main() 完全一致（照抄，只是在最前面插了一个分支）。
    """

    def __init__(self, start_text, title, ui_overlay, skills):
        self._overlay = ui_overlay
        self._skills = skills
        super().__init__(start_text, title, ui_overlay)

    def main(self):
        self.draw()
        draw_time = time.ticks_ms()

        while True:
            keys = self.kb.get_new_keys()

            for key in keys:
                if key == "/" and self._skills:
                    names = [s["name"] for s in self._skills]
                    chosen = _hydra_popup.PopupOptions(
                        [names], title="Skill", depth=1, ui_overlay=self._overlay,
                    ).main()
                    if chosen is not None:
                        idx = names.index(chosen)
                        addition = self._skills[idx]["text"]
                        if len(self.text) + len(addition) <= _MAX_INPUT_LEN:
                            self.text += addition
                    self.draw()  # 菜单画完了，把文本框重新画回来
                    continue

                if key == "SPC":
                    if len(self.text) < _MAX_INPUT_LEN:
                        self.text += " "
                elif key == "BSPC":
                    self.text = self.text[:-1]
                elif key == "ENT":
                    return self.text
                elif key in ("G0", "ESC"):
                    # 返回 None 表示"用户主动取消"，跟"提交了内容"严格
                    # 区分开——不能直接 return self.start_text，因为
                    # 发送失败重试时 start_text 会被设成上一次失败的
                    # 原文，如果取消也返回它，会被上层误判成"又提交了
                    # 一遍"。
                    return None
                elif key == "DEL":
                    self.text = ""
                elif len(key) == 1 and len(self.text) < _MAX_INPUT_LEN:
                    self.text += key

            time_now = time.ticks_ms()
            if keys or time.ticks_diff(time_now, draw_time) > 500:
                self.draw()
                draw_time = time_now
            else:
                time.sleep_ms(10)


def _ask_question(state, screen):
    """弹出（可能带 "/" 技能菜单的）文本输入框，问完就发请求。全程
    阻塞，返回时这一轮彻底结束（用户取消，或者已经成功把请求发出去）。

    如果发送这一步失败（BLE 层面，比如重试完还是 ENOMEM），不会丢掉
    用户打的字——重新弹一次输入框，把原文带回去，标题换成提示失败，
    让用户能直接改/重试。"""
    overlay = _hydra_popup.UIOverlay()
    skills = _load_skills()
    prefill = ""
    title = "Ask AI (/=skill)"

    while True:
        text = _SkillTextEntry(
            start_text=prefill, title=title, ui_overlay=overlay, skills=skills,
        ).main()

        if text is None:
            screen.dirty = True
            return  # 用户主动取消（G0/ESC），不发送

        text = text.strip()
        if not text:
            screen.dirty = True
            return

        cfg = _load_config()
        if not cfg.get("base_url") or not cfg.get("api_key"):
            overlay.error(f"Missing config:\n{_CONFIG_PATH}")
            screen.dirty = True
            return

        state["current_q"] = text
        state["current_a"] = ""
        state["answer_scroll"] = 0
        state["mode"] = "splash"
        sent_ok = _send_llm_request(state, screen, cfg, text)
        screen.dirty = True

        if sent_ok:
            return  # 正常路径：停在 splash 显示 Thinking...，等 handle_event

        # 发送失败：带着刚才打的字重新弹输入框，而不是把内容丢掉。
        prefill = text
        title = "Send failed, retry:"


# ---------------- 发送逻辑 ----------------

def _send_llm_request(state, screen, cfg, question):
    state["req_id"] += 1
    req_id = str(state["req_id"])
    state["pending_req_id"] = req_id
    state["sent_at"] = time.ticks_ms()
    state["status"] = "waiting"
    state["error"] = None

    messages = []
    system_prompt = cfg.get("system_prompt", "")
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": question})  # 单轮，不带历史

    openai_body = {
        "model": cfg.get("model", "gpt-4o-mini"),
        "messages": messages,
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens", 200),
    }

    # body 必须是已经 dumps 过的字符串（Gadgetbridge 源码是
    # json.getString("body") 直接当字节转发，不会帮你二次序列化）。
    ok = screen.ble.send_command({
        "t": "http",
        "id": req_id,
        "url": cfg.get("base_url", ""),
        "method": "post",
        "headers": {
            "Authorization": "Bearer " + cfg.get("api_key", ""),
            "Content-Type": "application/json",
        },
        "body": json.dumps(openai_body),
    })

    if not ok:
        # BLE 这一步就确定没发出去（send_command 内部的 ENOMEM 重试也
        # 已经用过了）。状态复位干净，交给 _ask_question 决定要不要带
        # 着原文重新弹输入框，而不是在这里直接展示错误窗口——这跟"已经
        # 发出去、只是网络那边出错/超时"是两种不同的失败，分开处理。
        state["pending_req_id"] = None
        state["status"] = "idle"
        state["sent_at"] = None

    return ok


# ---------------- 事件 / 按键 / 绘制 ----------------

def handle_event(t, data, state, screen):
    """注意：这里绝对不能调用任何 popup.py 的阻塞弹窗——这个函数是被
    BLE 异步回调触发的，不是按键路径，安全性没法验证，只改 state +
    手绘 draw()。"""
    if t != "http":
        return False

    resp_id = data.get("id")
    if resp_id is None or resp_id != state.get("pending_req_id"):
        return False  # 不是这轮聊天发起的请求，让 http_plugin.py 接手

    state["pending_req_id"] = None
    question = state.get("current_q", "")

    if "err" in data:
        err_text = str(data["err"])
        state["status"] = "error"
        state["error"] = err_text[:60]
        state["mode"] = "answer"  # 弹出窗口展示错误，跟成功时一致
        _append_history(question, None, err_text[:300])
        screen.dirty = True
        return True

    raw_resp = data.get("resp", "")
    try:
        parsed = json.loads(raw_resp) if isinstance(raw_resp, str) else raw_resp
        reply = parsed["choices"][0]["message"]["content"]
    except Exception as e:
        state["status"] = "error"
        state["error"] = "解析响应失败"
        state["mode"] = "answer"
        print("[chat_llm] 响应解析失败:", e, raw_resp[:200] if isinstance(raw_resp, str) else raw_resp)
        _append_history(question, None, "parse_failed")
        screen.dirty = True
        return True

    state["current_a"] = reply
    state["status"] = "idle"
    state["answer_scroll"] = 0
    state["mode"] = "answer"  # 请求结束（成功），这里才弹出窗口
    _append_history(question, reply, None)
    screen.dirty = True
    return True


def handle_keys(keys, state, screen):
    # 超时检测放最前面、跟 mode 无关：等待期现在停留在 splash（显示
    # Thinking...），不再是单独的 answer 模式，所以这里不能只在
    # mode=="answer" 时才检查。
    if state["status"] == "waiting" and state.get("sent_at") is not None:
        if time.ticks_diff(time.ticks_ms(), state["sent_at"]) > _TIMEOUT_MS:
            state["status"] = "error"
            state["error"] = "timeout"
            state["pending_req_id"] = None
            state["mode"] = "answer"  # 超时也当一次"请求结束"，弹窗告知
            screen.dirty = True

    mode = state.get("mode", "splash")

    if mode == "splash":
        for k in keys:
            if k in _ENTER_KEYS:
                if state["status"] == "waiting":
                    return True  # 请求还在飞，先不响应新的 ENTER
                _ask_question(state, screen)  # 阻塞，问完/取消才返回
                return True
        return False  # 其余按键不接管，跟别的核心视图手感一致

    # ---- mode == "answer" ----
    handled = False
    for k in keys:
        if k in ("LEFT", "RIGHT"):
            # 不处理，返回 False 让系统处理
            screen.dirty = True
            return False
        if k in _ESC_KEYS:
            state["mode"] = "splash"
            state["status"] = "idle"
            state["error"] = None  # 清空，splash 文字回到最初那句
            handled = True
        elif k in _ENTER_KEYS:
            _ask_question(state, screen)  # 追问下一句，阻塞
            handled = True
        elif k == "UP":
            state["answer_scroll"] = max(0, state.get("answer_scroll", 0) - 1)
            handled = True
        elif k == "DOWN":
            state["answer_scroll"] = state.get("answer_scroll", 0) + 1
            handled = True
        else:
            handled = True  # answer 模式下无条件拦截整轮按键

    if handled:
        screen.dirty = True
    return True


def _draw_splash(state):
    prompt = "Thinking..." if state.get("status") == "waiting" else "Press ENTER to ask"
    lines = ["🤖 LLM Chat", "", prompt]
    y = _CONTENT_Y_START + 24
    for line in lines:
        if not line:
            y += _LINE_HEIGHT
            continue
        x = (_MH_DISPLAY_WIDTH - len(line) * _CHAR_WIDTH) // 2
        color = PALETTE[6] if line == "Thinking..." else PALETTE[8]
        DISPLAY.text(line, x, y, color)
        y += _LINE_HEIGHT


def _draw_answer(state):
    # 加一圈边框，视觉上像"弹出来的一个窗口"，但不是真的 popup.py 阻塞
    # 弹窗——理由见文件顶部注释（handle_event 是 BLE 异步路径，不敢在
    # 那条路径上跑阻塞等按键的循环），这里纯粹是手绘 + UP/DOWN 翻页。
    border_x = 3
    border_y = _CONTENT_Y_START + 1
    border_w = _MH_DISPLAY_WIDTH - border_x * 2
    border_h = _CONTENT_HEIGHT - 2
    DISPLAY.rect(border_x, border_y, border_w, border_h, PALETTE[4])

    max_chars = max(1, (border_w - 6) // _CHAR_WIDTH)
    y = border_y + 3

    status_row_h = _LINE_HEIGHT if state.get("status") == "error" else 0
    content_bottom = border_y + border_h - status_row_h - 2

    lines = []
    if state.get("current_q"):
        for w in wrap_text("You: " + state["current_q"], max_chars):
            lines.append((w, "q"))
    if state.get("current_a"):
        for w in wrap_text("AI: " + state["current_a"], max_chars):
            lines.append((w, "a"))

    visible_rows = max(1, (content_bottom - y) // _LINE_HEIGHT)
    total = len(lines)
    max_offset = max(0, total - visible_rows)
    offset = min(state.get("answer_scroll", 0), max_offset)
    state["answer_scroll"] = offset
    end = total - offset
    start = max(0, end - visible_rows)

    cy = y
    for text, role in lines[start:end]:
        color = PALETTE[8] if role == "q" else PALETTE[6]
        DISPLAY.text(text, border_x + 3, cy, color)
        cy += _LINE_HEIGHT

    if state.get("status") == "error":
        DISPLAY.text(f"! {state.get('error') or 'error'}", border_x + 3, content_bottom, PALETTE[8])


def draw(state):
    if state.get("mode", "splash") == "splash":
        _draw_splash(state)
    else:
        _draw_answer(state)