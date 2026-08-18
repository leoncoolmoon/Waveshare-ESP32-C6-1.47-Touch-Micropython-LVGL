import bluetooth
import re
import time
import json
import binascii
from lib.hydra import config as _hydra_config

from bangle_utils import extract_atob_fields, decode_espruino_image_string

# BLE 对象在这里（模块 import 时）就建出来，激活/注册服务/打开广播
# 也在这里完成（见文件最底部 `_BLE_BRIDGE = BLENotifyBridge(ble=_BLE)`）
# -- 这一步必须赶在 main.py 里 `from display_core import *`（也就是
# DISPLAY 的构造）之前跑完，才能拿到干净、连续的内部 DRAM。main.py
# 里 `import ble_bridge` 必须是最早的一条 import。

_BLE = bluetooth.BLE()


_BLE_DEVICE_NAME = "Bangle.js BLE"

_UART_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_TX = (bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E"), bluetooth.FLAG_NOTIFY)
_UART_RX = (bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E"), bluetooth.FLAG_WRITE | bluetooth.FLAG_WRITE_NO_RESPONSE)
_UART_SERVICE = (_UART_UUID, (_UART_TX, _UART_RX))

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)
_IRQ_MTU_EXCHANGED = const(21)
_IRQ_CONNECTION_UPDATE = const(27)  # 仅用来打日志确认协商结果，不订阅也不影响功能

# ---- 省电相关的可调参数 ----------------------------------------------
# 广播间隔：数值越小，手机越快能扫到/重连上设备，但没连上的时候射频
# 唤醒也越频繁、越费电。100ms（100000us）偏激进，改成 240ms 作为一个
# 更省电、但绝大多数手机仍然能在一两次扫描周期内就发现设备的折中值。
# 如果实测重连明显变慢，可以调小这个数字。
_ADV_INTERVAL_US = const(240000)

# 连接参数更新：单位不是毫秒，是 BLE 规范自己的单位 -- interval 是
# 1.25ms 的倍数，timeout 是 10ms 的倍数。
#   min/max interval: 40~80 -> 50ms~100ms。连接之后大部分时间设备是
#     空闲的（等手机推通知），没必要用手机默认协商出来的那种低延迟
#     间隔（有些手机会协商到 15ms 左右，等于每秒唤醒射频 60+ 次）。
#   latency: 4 -> 允许设备在没有数据要发的时候，最多连续 4 个连接
#     事件都不响应，进一步拉长实际的空闲间隔（配合上面的 interval，
#     真正空闲时大概每 400~500ms 才唤醒一次），但只要设备真有数据要
#     发（收到手机通知、要回 ack 等），下一个连接事件照样能发，不会
#     增加通知到达手表的延迟。
#   timeout: 400 -> 4000ms 监督超时，要大于 (latency+1)*max_interval*2
#     才合法，这里留了足够余量。
# 这几个数字是按"通知类可穿戴设备"的常见经验值给的，不是针对
# Gadgetbridge 单独测过的，如果电量测下来还是不够理想，或者发现通知
# 到达变慢，可以调整这几个常量再试。
_CONN_INTERVAL_MIN = const(40)
_CONN_INTERVAL_MAX = const(80)
_CONN_LATENCY = const(4)
_CONN_TIMEOUT = const(400)

# 单个 chunk 遇到 ENOMEM 时的重试策略：最多尝试这么多次（含第一次），
# 每次重试前等这么久。按你的要求先做"等 1 秒重试一次"（_SEND_MAX_ATTEMPTS=2
# 意味着：第一次失败 -> 等 1000ms -> 再试一次 -> 还失败就放弃）。
_SEND_MAX_ATTEMPTS = const(2)
_SEND_RETRY_DELAY_MS = const(1000)

_BARE_KEY_RE = re.compile(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:')
_SET_TIME_RE = re.compile(r"setTime\((\d+(?:\.\d+)?)\)")
_SET_TIMEZONE_RE = re.compile(r"setTimeZone\(([\-\d.]+)\)")



# 时区偏移（小时）。这里故意不通过 display_core.CONFIG 读 --
# display_core 一 import 就会把 DISPLAY（占大块连续内部 DRAM 的对象）
# 建出来，如果 ble_bridge.py 依赖它，等于把 DISPLAY 的构造提前到了
# BLE 激活之前，正好是上面说的丢包/吞吐量问题的成因。这里单独建一份
# 轻量的 Config()（lib.hydra.config 内部如果是单例模式的话，跟
# display_core.CONFIG 其实是同一个对象，不会重复占内存；就算不是
# 单例，Config() 本身也远比 Display() 轻量，不会有同样的风险）。
_cfg = _hydra_config.Config()
tz_offset = 10
try:
    tz_offset = float(_cfg["timezone"])
except Exception:
    pass

class BLENotifyBridge:
    # Impersonates Bangle.js over UART to receive all Gadgetbridge message types.

    def __init__(self, name=_BLE_DEVICE_NAME,
                 on_connect=None, on_disconnect=None,
                 on_raw_data=None,
                 on_notify=None, on_notify_bitmap=None, on_notify_dismiss=None,
                 on_call=None,
                 on_musicinfo=None, on_musicstate=None,
                 on_time_sync=None, 
                 on_unknown=None,
                 ble=None):
        # _callbacks_ready/_pending_events 必须在 set_callbacks() 第一次
        # 被调用之前就先建好 -- 见 set_callbacks() 里的说明。
        self._callbacks_ready = False
        self._pending_events = []
        self.set_callbacks(
            on_connect=on_connect, on_disconnect=on_disconnect,
            on_raw_data=on_raw_data,
            on_notify=on_notify, on_notify_bitmap=on_notify_bitmap,
            on_notify_dismiss=on_notify_dismiss,
            on_call=on_call,
            on_musicinfo=on_musicinfo, on_musicstate=on_musicstate,
            on_time_sync=on_time_sync,
            on_unknown=on_unknown,
        )

        self._last_notify_id = None
        # 复用调用方传进来的、已经 new 出来的 BLE 对象（见模块级的
        # `_BLE = bluetooth.BLE()`），不重新 new 一个 -- 只在这里真正
        # active(True)，把"创建对象"和"激活硬件"分成两步，方便调用方
        # 控制激活的确切时机。
        self._ble = ble if ble is not None else bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq)
        ((self._tx_handle, self._rx_handle),) = self._ble.gatts_register_services((_UART_SERVICE,))
        self._connections = set()
        self._mtu = {}  # conn_handle -> negotiated MTU (for chunking outbound sends)
        self._rx_buffers = {}  # conn_handle -> 该连接自己的接收缓冲区
        self._payload = self._advertising_payload(name)
        self._advertise()

    def set_callbacks(self, on_connect=None, on_disconnect=None, on_raw_data=None,
                       on_notify=None, on_notify_bitmap=None, on_notify_dismiss=None,
                       on_call=None, on_musicinfo=None, on_musicstate=None,
                       on_time_sync=None, on_unknown=None):
        # 这个类是"模块一 import 就构造好、马上开始广播"的，但那时候
        # screen 等其它状态还没建出来，回调函数没法一起传进来 --
        # __init__ 里先用 None 占位调一次这个方法，main_loop() 里
        # screen/回调都建好了之后，再真正调一次这个方法把它们补上去；
        # GATT 服务注册和广播早就已经在跑了，不受这个影响。
        #
        # 但这中间有个空档：BLE 广播/连接/收发从 import 时就已经在跑，
        # 如果手机（尤其是已经配对过、重连很快的情况）在这个空档期内
        # 就连上、甚至发过来 setTime(...) 握手，这些事件会在真正的回调
        # 还没接上之前就已经发生 -- 之前的写法是直接 `if self._on_xxx
        # is not None: ...`，回调还没接上时这些事件会被直接丢掉，什么
        # 都不会发生，导致"连上了但状态栏还显示断开"、"偶尔漏掉
        # setTime"这类问题。现在改成：回调真正接上之前发生的事件全部
        # 先缓存到 _pending_events 里，等回调真的接上了（下面判断
        # any_provided）再按顺序补放一遍，一个都不丢。
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_raw_data = on_raw_data
        self._on_notify = on_notify
        self._on_notify_bitmap = on_notify_bitmap
        self._on_notify_dismiss = on_notify_dismiss
        self._on_call = on_call
        self._on_musicinfo = on_musicinfo
        self._on_musicstate = on_musicstate
        self._on_time_sync = on_time_sync
        self._on_unknown = on_unknown

        any_provided = any((
            on_connect, on_disconnect, on_raw_data, on_notify, on_notify_bitmap,
            on_notify_dismiss, on_call, on_musicinfo, on_musicstate, on_time_sync,
            on_unknown,
        ))
        if any_provided and not self._callbacks_ready:
            self._callbacks_ready = True
            self._flush_pending_events()

    def _emit(self, attr_name, *args):
        # 统一的事件派发入口。回调还没真正接上（_callbacks_ready 为
        # False）的话，不丢事件，先攒到 _pending_events 里，等
        # set_callbacks() 真正接上回调时会按顺序补放一遍。
        if not self._callbacks_ready:
            if len(self._pending_events) < 50:
                self._pending_events.append((attr_name, args))
            else:
                print(f"[BLE] pending event queue full, dropping {attr_name} event")
            return
        cb = getattr(self, attr_name, None)
        if cb is not None:
            cb(*args)

    def _flush_pending_events(self):
        pending = self._pending_events
        self._pending_events = []
        for attr_name, args in pending:
            cb = getattr(self, attr_name, None)
            if cb is not None:
                try:
                    cb(*args)
                except Exception as e:
                    print(f"[BLE] error replaying queued {attr_name} event:", e)

    # ---- BLE plumbing -------------------------------------------------

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            self._connections.add(conn_handle)
            self._mtu[conn_handle] = 23  # BLE 默认值，交换成功之前先按这个算
            self._rx_buffers[conn_handle] = b""
            print("[BLE] central connected, handle:", conn_handle)
            # 主动发起 MTU 交换，不要只被动等对面发起 -- 见文件顶部注释。
            try:
                self._ble.gattc_exchange_mtu(conn_handle)
            except Exception as e:
                print("[BLE] MTU exchange request failed:", e)
            # 主动请求把连接间隔放宽、打开 slave latency -- 手机默认协商
            # 出来的连接参数往往偏低延迟（射频唤醒频繁、费电），但这个
            # 设备大部分时间只是等手机推通知，没必要跟着那么频繁地醒。
            # 不是所有固件/绑定版本都有这个方法，用 hasattr 兜底；就算
            # 手机不接受这个请求，也不影响正常收发，顶多是继续用手机
            # 原来协商的参数。
            if hasattr(self._ble, 'gap_connection_update'):
                try:
                    self._ble.gap_connection_update(
                        conn_handle, _CONN_INTERVAL_MIN, _CONN_INTERVAL_MAX,
                        _CONN_LATENCY, _CONN_TIMEOUT,
                    )
                except Exception as e:
                    print("[BLE] connection param update request failed:", e)
            self._emit("_on_connect")

        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn_handle, _, _ = data
            self._connections.discard(conn_handle)
            self._mtu.pop(conn_handle, None)
            self._rx_buffers.pop(conn_handle, None)
            print("[BLE] central disconnected, handle:", conn_handle)
            self._emit("_on_disconnect")
            self._advertise()
        elif event == _IRQ_MTU_EXCHANGED:
            conn_handle, mtu = data
            self._mtu[conn_handle] = mtu
            print(f"[BLE] MTU negotiated for handle {conn_handle}: {mtu}")
            # MTU 协商成功之后，RX 特征值自己的缓冲区也要跟着放大，
            # 不然协商出来的 MTU 数字虽然变大了，实际能收的单包大小
            # 还是卡在旧缓冲区大小上，等于没生效。不是所有固件/绑定
            # 版本都有这个方法，用 hasattr 兜底。
            if hasattr(self._ble, 'gatts_set_buffer'):
                try:
                    self._ble.gatts_set_buffer(self._rx_handle, mtu, False)
                    print(f"[BLE] RX buffer resized to {mtu} bytes")
                except Exception as e:
                    print(f"[BLE] gatts_set_buffer failed: {e}")
        elif event == _IRQ_GATTS_WRITE:
            conn_handle, value_handle = data
            if value_handle == self._rx_handle:
                chunk = self._ble.gatts_read(self._rx_handle)
                self._emit("_on_raw_data", chunk)
                self._feed_rx(conn_handle, chunk)

    def _feed_rx(self, conn_handle, chunk):
        # 按连接分别攒缓冲区，而不是全局共用一个 -- 就算一般只有一个
        # 手机会连上来，这样写也更稳妥，不会因为断开重连之类的时序问题
        # 把不同连接的数据串到一起。
        buffer = self._rx_buffers.get(conn_handle, b"") + chunk

        while True:
            # 协议实际用的帧格式是 "\x10 ... \n"（\x10 是起始标记）。
            # 之前纯按 "\n" 切，一旦起始标记前混进了垃圾字节（上一条
            # 消息的残留、写入没对齐等），这些垃圾字节会被当成消息的
            # 一部分去解析，导致整条消息解析失败、被直接丢弃 -- 从
            # 用户角度看就是"丢包"，其实数据都收到了，只是分帧分错了。
            start = buffer.find(b'\x10')

            if start == -1:
                # 没找到起始标记：兼容不带 \x10 前缀、直接就是
                # "GB(...)" 的旧格式/边界情况。
                if buffer.startswith(b'GB('):
                    end = buffer.find(b')')
                    if end != -1 and end < 4096:
                        msg = buffer[:end + 1]
                        buffer = buffer[end + 1:]
                        if msg:
                            self._handle_line(msg)
                        continue
                if len(buffer) > 0:
                    print(f"[BLE] no \\x10 marker found, discarding {len(buffer)} stray bytes")
                    buffer = b""
                break

            if start > 0:
                # 起始标记前面的字节是垃圾数据（不属于任何一条完整
                # 消息），跳过它们，不要把它们塞进下面的消息体里。
                buffer = buffer[start:]

            end = buffer.find(b'\n', 1)
            if end == -1:
                # 消息还没收完整，等下一次 write 事件把剩下的部分带来。
                if len(buffer) > 4096:
                    print("[BLE] buffer too large without a terminator, clearing")
                    buffer = b""
                break

            complete_msg = buffer[1:end]  # 去掉开头的 \x10 和结尾的 \n
            buffer = buffer[end + 1:]
            if complete_msg:
                self._handle_line(complete_msg)
        
        if len(buffer) > 8192:
            print("[BLE] rx buffer overflow, resetting")
            buffer = b""

        self._rx_buffers[conn_handle] = buffer

    def _advertise(self):
        self._ble.gap_advertise(_ADV_INTERVAL_US, adv_data=self._payload)

    @staticmethod
    def _advertising_payload(name):
        payload = bytearray()
        payload += bytes((2, 0x01, 0x06))
        name_bytes = name.encode("utf-8")
        payload += bytes((len(name_bytes) + 1, 0x09)) + name_bytes
        return payload

    # ---- Sending commands ----------------------------------------------

    def send_command(self, obj):
        # Send a command to Gadgetbridge (device -> phone direction).
        #
        # IMPORTANT: a single gatts_notify() call only ever produces ONE
        # BLE packet -- MicroPython does NOT split a large outgoing
        # notification for you. Anything beyond (MTU - 3) bytes gets
        # silently dropped. So we chunk manually here, the same way the
        # phone's BLE stack chunks *its* writes to us.
        #
        # 返回 True/False，表示这次是不是真的完整发出去了 -- 调用方
        # （比如 chat_llm.py）可以靠这个立刻知道"这次注定收不到回复"，
        # 不用傻等超时。
        payload = (json.dumps(obj) + "}\n").encode("utf-8")
        if not self._connections:
            print("[BLE] send_command called with no active connection, dropping:", obj)
            return False

        all_ok = True
        for conn_handle in self._connections:
            mtu = self._mtu.get(conn_handle, 23)
            # ATT 协议开销：3 字节 (opcode + handle)
            chunk_size = max(1, mtu - 3)
            total_len = len(payload)

            offset = 0
            chunk_num = 0
            conn_ok = True
            while offset < total_len:
                end = min(offset + chunk_size, total_len)
                chunk = payload[offset:end]

                sent = False
                for attempt in range(_SEND_MAX_ATTEMPTS):
                    try:
                        self._ble.gatts_notify(conn_handle, self._tx_handle, chunk)
                        sent = True
                        break
                    except OSError as e:
                        # errno 12 = ENOMEM。这个具体场景下观察到的是
                        # 偶发性的：BLE 栈内部通知缓冲池一时紧张，等一下
                        # 通常就恢复了，重试一次往往就能过 -- 不是这条
                        # chunk 本身太大（chunk 早就按 MTU 切过了）。别的
                        # OSError（比如连接已经断开）重试没意义，直接
                        # 放弃。
                        is_enomem = len(e.args) > 0 and e.args[0] == 12
                        if is_enomem and attempt + 1 < _SEND_MAX_ATTEMPTS:
                            print(f"[BLE] gatts_notify ENOMEM, retry {attempt + 1}/{_SEND_MAX_ATTEMPTS - 1} in {_SEND_RETRY_DELAY_MS}ms")
                            time.sleep_ms(_SEND_RETRY_DELAY_MS)
                            continue
                        print("[BLE] failed to send command:", e)
                        break

                if not sent:
                    conn_ok = False
                    break

                chunk_num += 1
                offset = end
                if offset < total_len:
                    time.sleep_ms(10)  # 让 BLE 栈有时间处理通知队列

            if conn_ok:
                print(f"[BLE] sent command in {chunk_num} chunk(s) of <= {chunk_size} bytes (mtu={mtu}):", obj)
            else:
                all_ok = False

        return all_ok

    def send_music_control(self, action):
        # Control music playback: play/pause/next/previous/volumeup/volumedown.
        self.send_command({"t": "music", "n": action})

    def send_call_action(self, action):
        # Control phone call.
        self.send_command({"t": "call", "n": action})

    def send_notify_action(self, notif_id, action, msg=None):
        # Dismiss, reply to notification.
        cmd = {"t": "notify", "id": notif_id, "n": action}
        if msg is not None:
            cmd["msg"] = msg
        self.send_command(cmd)

    def send_version(self, firmware="1.0.0", hardware="0.0.0"):
        # Send version info.
        self.send_command({"t": "ver", "fw": firmware, "hw": hardware})
    
    def send_battery_info(self, percentage="50", charging = "0"):
        self.send_command({"t": "status", "bat": percentage, "chg": charging})

    def send_plugin_command(self, tag, payload):
        self.send_command({"t": tag, "data": payload})
    # ---- Parsing -------------------------------------------------------

    def _handle_line(self, line):
        try:
            # \xa0（不间断空格）有些手机端 JSON 编码会混进来，解码前先
            # 去掉；errors='ignore' 让一个字节的解码问题不至于把整条
            # 本来是好的消息也搭进去丢掉。
            text = line.replace(b'\xa0', b'').decode('utf-8', 'ignore')
        except Exception as e:
            print("[BLE] failed to decode line as utf-8:", e, line)
            return

        text = text.replace("\x10", "").replace("\x03", "").strip()
        if not text:
            return

        print("[BLE] complete line:", text[:100] + "..." if len(text) > 100 else text)

        # IMPORTANT: this check must come before the GB(...) check below --
        # the time-sync script Gadgetbridge sends on connect
        # ("setTime(...);E.setTimeZone(...);...") is NOT wrapped in GB(...).
        if text.startswith("setTime("):
            self._handle_time_sync(text)
            return

        if text.startswith("GB(") and text.endswith(")"):
            data = self._parse_gb_object(text)
            if data is not None:
                self._dispatch(data)
            return

        # 兼容没有 GB(...) 包裹、直接就是一段 JSON 的情况。
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    self._dispatch(data)
                    return
            except Exception:
                pass

        print("[BLE] unrecognized message format:", text[:50])

    def _handle_time_sync(self, text):
        global tz_offset
        time_match = _SET_TIME_RE.search(text)
        print("set Time command received")
        if time_match is None:
            print("no match Time")
            return
        tz_match = _SET_TIMEZONE_RE.search(text)
        if tz_match is not None:
            tz_offsetL = float(tz_match.group(1))
            tz_offset = tz_offsetL
        else:
            # 这条消息里没带 setTimeZone(...)（比如手机后续只单独发了
            # setTime(...) 做校准），沿用当前已知的时区偏移，而不是直接
            # 在 tz_match.group(1) 上崩溃、导致这次时间同步整个被吞掉。
            tz_offsetL = tz_offset
            print("[BLE] time sync: no setTimeZone(...) in this line, reusing tz_offset =", tz_offsetL)
        epoch = float(time_match.group(1))# - (tz_offsetL * 3600)
        print(f"[BLE] time sync: epoch={epoch}, tz_offset={tz_offsetL}h")
        self._emit("_on_time_sync", epoch, tz_offsetL)

    @staticmethod
    def _parse_gb_object(raw_text):
        # Parse GB({...}) with atob() fields.
        text = raw_text.strip()
        if not (text.startswith("GB(") and text.endswith(")")):
            return None
        inner = text[3:-1]
        if not (inner.startswith("{") and inner.endswith("}")):
            return None

        # 先尝试直接解析（大部分消息不含 atob()，直接就能解析成功）
        try:
            json_str = _BARE_KEY_RE.sub(r'\1"\2":', inner)
            return json.loads(json_str)
        except Exception:
            pass

        # 如果直接解析失败，尝试处理 atob()
        inner, atob_fields = extract_atob_fields(inner)

        bitmaps = {}
        for field_name, b64 in atob_fields.items():
            if b64 is None:
                bitmaps[field_name] = None
                continue
            try:
                raw_bytes = binascii.a2b_base64(b64)
                bitmaps[field_name] = decode_espruino_image_string(raw_bytes)
            except Exception as e:
                print(f"[BLE] Failed to decode bitmap '{field_name}':", e)
                bitmaps[field_name] = None

        json_str = _BARE_KEY_RE.sub(r'\1"\2":', inner)
        try:
            data = json.loads(json_str)
        except Exception as e:
            print("[BLE] JSON parse failed:", e)
            return None

        for field_name, image in bitmaps.items():
            data[field_name] = image if image is not None else "<bitmap>"

        return data

    def _dispatch(self, data):
        t = data.get("t")

        try:
            if t == "notify":
                notif_id = data.get("id")
                if notif_id is not None and notif_id == self._last_notify_id:
                    print("[BLE] duplicate notify, skipping")
                    return
                self._last_notify_id = notif_id

                title_field = data.get("title", data.get("subject", ""))
                body_field = data.get("body", "")
                has_bitmap = isinstance(title_field, dict) or isinstance(body_field, dict)

                if has_bitmap:
                    self._emit("_on_notify_bitmap", data.get("src", "unknown"), title_field, body_field, notif_id)
                else:
                    self._emit("_on_notify", data.get("src", "unknown"), title_field, body_field, notif_id)

            elif t == "notify-":
                self._emit("_on_notify_dismiss", data.get("id"))

            elif t == "call":
                self._emit("_on_call", data.get("cmd"), data.get("name", ""), data.get("number", ""))

            elif t == "musicinfo":
                self._emit("_on_musicinfo", data)

            elif t == "musicstate":
                self._emit("_on_musicstate", data)

            else:
                # weather / alarm / act / actfetch / listRecs / fetchRec /
                # calendar / calendar- / gps / gps_power / is_gps_active /
                # nav / http / find / vibrate / 以及任何未来手机端新加的
                # 类型，全部走这里，交给 main.py -> plugin_manager 分发。
                self._emit("_on_unknown", t, data)

        except Exception as e:
            # _emit() 在回调还没接上（_callbacks_ready 为 False）的时候
            # 只会把事件存进 _pending_events，不会抛异常，所以能走到这里
            # 通常说明回调已经接上了，只是回调本身内部执行出错了（比如
            # 某个插件/回调自己有 bug）-- 与其让这条消息就这么悄无声息地
            # 丢掉，不如尽量把它降级成一条通知显示出来，好歹用户能看到
            # 点什么、也方便排查是哪条消息触发的。
            print(f"[BLE] dispatch error, falling back to notification: {e}")
            if self._on_notify is not None:
                title = (
                    data.get("title") or data.get("subject") or data.get("cmd")
                    or data.get("instr") or data.get("name") or (t.upper() if t else "Unknown")
                )
                body = data.get("body") or data.get("number") or data.get("distance") or data.get("url") or ""
                extra = []
                for key, value in data.items():
                    if key not in ("t", "title", "subject", "body", "cmd", "instr", "name", "number", "distance", "url", "id") and value:
                        extra.append(f"{key}: {value}")
                if extra:
                    body = f"{body} ({', '.join(extra)})" if body else ", ".join(extra)
                if not body:
                    body = json.dumps(data)[:100]
                self._on_notify("BLE", title, body, data.get("id"))


# BLE 对象在模块 import 时就已经建好（见上面的 `_BLE = bluetooth.BLE()`），
# 这里紧接着构造 BLENotifyBridge 完成真正的激活/服务注册/广播 -- 这一切
# 都必须赶在 main.py 里 `from display_core import *`（DISPLAY 的构造）
# 之前跑完，main.py 里 `import ble_bridge` 必须是最早的一条 import。
_BLE_BRIDGE = BLENotifyBridge(ble=_BLE)
