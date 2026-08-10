# ble_bridge.py -- BLE 收音机 + GadgetBridge 协议解析
# import 这个模块就会立刻 active(True) + 开始广播 + 注册GATT服务，
# main.py 必须把这个放在所有硬件(显示/触摸)初始化之前 import。
import bluetooth
import re
import json
import binascii
import utime as time

from bangle_utils import extract_atob_fields, decode_espruino_image_string

_BLE = bluetooth.BLE()
_BLE.active(True)

_BLE_DEVICE_NAME = "Bangle.js BLE"

_UART_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_TX = (bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E"), bluetooth.FLAG_NOTIFY)
_UART_RX = (bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E"), bluetooth.FLAG_WRITE | bluetooth.FLAG_WRITE_NO_RESPONSE)
_UART_SERVICE = (_UART_UUID, (_UART_TX, _UART_RX))

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)
_IRQ_MTU_EXCHANGED = const(21)

_BARE_KEY_RE = re.compile(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:')
_SET_TIME_RE = re.compile(r"setTime\((\d+(?:\.\d+)?)\)")
_SET_TIMEZONE_RE = re.compile(r"setTimeZone\(([\-\d.]+)\)")

tz_offset = 10

class BLENotifyBridge:

    def __init__(self, name=_BLE_DEVICE_NAME,
                 on_connect=None, on_disconnect=None,
                 on_raw_data=None,
                 on_notify=None, on_notify_bitmap=None, on_notify_dismiss=None,
                 on_call=None, on_weather=None,
                 on_musicinfo=None, on_musicstate=None,
                 on_time_sync=None, on_alarm=None,
                 on_find=None, on_vibrate=None,
                 on_activity=None, on_activity_fetch=None,
                 on_calendar=None, on_calendar_remove=None,
                 on_gps=None, on_gps_power=None,
                 on_navigation=None,
                 on_http_request=None,
                 on_unknown=None,
                 ble=None):

        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_raw_data = on_raw_data
        self._on_notify = on_notify
        self._on_notify_bitmap = on_notify_bitmap
        self._on_notify_dismiss = on_notify_dismiss
        self._on_call = on_call
        self._on_weather = on_weather
        self._on_musicinfo = on_musicinfo
        self._on_musicstate = on_musicstate
        self._on_time_sync = on_time_sync
        self._on_alarm = on_alarm
        self._on_find = on_find
        self._on_vibrate = on_vibrate
        self._on_activity = on_activity
        self._on_activity_fetch = on_activity_fetch
        self._on_calendar = on_calendar
        self._on_calendar_remove = on_calendar_remove
        self._on_gps = on_gps
        self._on_gps_power = on_gps_power
        self._on_navigation = on_navigation
        self._on_http_request = on_http_request
        self._on_unknown = on_unknown

        self._last_notify_id = None
        # BLE 控制器初始化需要向系统要一整块内部 DRAM（不是我们这些小
        # canvas 那种能碎着分配的内存），如果等到这里（前面已经建了一堆
        # UI 对象之后）才 bluetooth.BLE().active(True)，经常会因为内部
        # RAM 不够/太碎而失败（"ble ll env init error code:-10" 就是这个）。
        # 所以现在 BLE 收音机改成在文件最开头、还没建任何 UI 对象之前就
        # 激活好了，这里优先复用那个已经 active 的实例，不重新 active 一遍。
        self._ble = ble if ble is not None else bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq)
        ((self._tx_handle, self._rx_handle),) = self._ble.gatts_register_services((_UART_SERVICE,))
        self._connections = set()
        self._mtu = {}  # conn_handle -> negotiated MTU (for chunking outbound sends)
        self._rx_buffer = b""
        self._payload = self._advertising_payload(name)
        self._advertise()

    def set_callbacks(self, **kwargs):
        # 这个类现在改成"文件最开头就构造好、马上开始广播"，但那时候
        # screen 等其它状态还没建出来，回调函数没法一起传进来。改成
        # 构造的时候先不带回调，等 main_loop() 里真正的 screen/回调都
        # 建好了之后，再调这个方法把它们补上去——GATT 服务注册和广播
        # 早就已经在跑了，不受这个影响。
        for k, v in kwargs.items():
            attr = "_" + k
            if hasattr(self, attr):
                setattr(self, attr, v)

    # ---- BLE plumbing -------------------------------------------------

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            self._connections.add(conn_handle)
            self._mtu[conn_handle] = 23  # 初始值
            
            # 重要：为每个连接创建独立的缓冲区
            if not hasattr(self, '_rx_buffers'):
                self._rx_buffers = {}
            self._rx_buffers[conn_handle] = b""
            
            # 重要：请求MTU交换
            print("[BLE] central connected, handle:", conn_handle)
            try:
                self._ble.gattc_exchange_mtu(conn_handle)
            except Exception as e:
                print("[BLE] MTU exchange failed:", e)
            
            if self._on_connect is not None:
                self._on_connect()
                
        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn_handle, _, _ = data
            self._connections.discard(conn_handle)
            self._mtu.pop(conn_handle, None)
            if hasattr(self, '_rx_buffers') and conn_handle in self._rx_buffers:
                del self._rx_buffers[conn_handle]
            print("[BLE] central disconnected, handle:", conn_handle)
            if self._on_disconnect is not None:
                self._on_disconnect()
            self._advertise()
            
        elif event == _IRQ_MTU_EXCHANGED:
            conn_handle, mtu = data
            self._mtu[conn_handle] = mtu
            print(f"[BLE] MTU negotiated for handle {conn_handle}: {mtu}")
            
            # 重要：MTU协商完成后，重新设置接收缓冲区大小
            # 某些BLE栈需要在MTU变更后重新配置
            if hasattr(self._ble, 'gatts_set_buffer'):
                try:
                    self._ble.gatts_set_buffer(self._rx_handle, mtu, False)
                    print(f"[BLE] RX buffer set to {mtu} bytes")
                except Exception as e:
                    print(f"[BLE] Failed to set buffer: {e}")
            
        elif event == _IRQ_GATTS_WRITE:
            conn_handle, value_handle = data
            if value_handle == self._rx_handle:
                # 读取数据
                chunk = self._ble.gatts_read(self._rx_handle)
                
                # 调试：打印接收到的数据
                print(f"[BLE] RX chunk: {len(chunk)} bytes, MTU={self._mtu.get(conn_handle, 0)}")
                if len(chunk) < 50:
                    print(f"[BLE] Data: {chunk}")
                else:
                    print(f"[BLE] Data: {chunk[:50]}...")
                
                # 获取或创建缓冲区
                if not hasattr(self, '_rx_buffers'):
                    self._rx_buffers = {}
                if conn_handle not in self._rx_buffers:
                    self._rx_buffers[conn_handle] = b""
                
                # 追加数据
                self._rx_buffers[conn_handle] += chunk
                
                # 处理完整的消息
                buffer = self._rx_buffers[conn_handle]
                
                # 查找所有以 \x10 开头，以 \n 结尾的完整消息
                while True:
                    # 查找消息开始
                    start = buffer.find(b'\x10')
                    if start == -1:
                        # 没有消息头，可能是垃圾数据或协议开始
                        if len(buffer) > 0:
                            # 如果缓冲区有数据但没有 \x10，可能是第一个包
                            # 检查是否是 GB( 开头（兼容旧协议）
                            if buffer.startswith(b'GB('):
                                # 处理 GB( 格式
                                end = buffer.find(b')')
                                if end != -1 and end < 100:  # 限制搜索范围
                                    msg = buffer[:end+1]
                                    buffer = buffer[end+1:]
                                    self._rx_buffers[conn_handle] = buffer
                                    if msg:
                                        self._handle_line(msg)
                                    continue
                            # 否则，可能是错误数据，清空
                            print(f"[BLE] No message header, clearing {len(buffer)} bytes")
                            self._rx_buffers[conn_handle] = b""
                        break
                    
                    # 如果有垃圾数据在 \x10 之前，丢弃
                    if start > 0:
                        print(f"[BLE] Discarding {start} bytes before \\x10")
                        buffer = buffer[start:]
                        self._rx_buffers[conn_handle] = buffer
                    
                    # 查找消息结束
                    end = buffer.find(b'\n', 1)  # 从第1个字节开始找
                    if end == -1:
                        # 没有完整的消息
                        # 但如果缓冲区太大，可能消息丢失了
                        if len(buffer) > 4096:
                            print("[BLE] Buffer too large without \\n, clearing")
                            self._rx_buffers[conn_handle] = b""
                        break
                    
                    # 提取完整消息（去掉 \x10 和 \n）
                    complete_msg = buffer[1:end]
                    buffer = buffer[end+1:]
                    self._rx_buffers[conn_handle] = buffer
                    
                    # 处理消息
                    if complete_msg:
                        print(f"[BLE] Complete message ({len(complete_msg)} bytes)")
                        print(f"[BLE] Raw: {complete_msg[:100]}...")
                        cleaned_msg = complete_msg.replace(b'\xa0', b' ')

                        # 解码并处理
                        try:
                            line = cleaned_msg.decode('utf-8')
                            line = line.replace('\xa0', ' ')
                            self._handle_line(line)
                        except Exception as e:
                            print(f"[BLE] Decode error: {e}")
                            # 尝试作为原始字节处理
                            self._handle_line(complete_msg)
                
                # 安全限制
                if len(self._rx_buffers.get(conn_handle, b"")) > 8192:
                    print("[BLE] Buffer overflow, resetting")
                    self._rx_buffers[conn_handle] = b""


    def _advertise(self):
        self._ble.gap_advertise(100000, adv_data=self._payload)

    @staticmethod
    def _advertising_payload(name):
        payload = bytearray()
        payload += bytes((2, 0x01, 0x06))
        name_bytes = name.encode("utf-8")
        payload += bytes((len(name_bytes) + 1, 0x09)) + name_bytes
        return payload

    # ---- Sending commands ----------------------------------------------

    def send_command(self, obj):
        payload = (json.dumps(obj) + "}\n").encode("utf-8")
        if not self._connections:
            print("[BLE] send_command called with no active connection, dropping:", obj)
            return
        for conn_handle in self._connections:
            mtu = self._mtu.get(conn_handle, 23)
            # ATT 协议开销：3 字节 (opcode + handle)
            chunk_size = max(1, mtu - 3)
            total_len = len(payload)
            
            try:
                offset = 0
                chunk_num = 0
                while offset < total_len:
                    end = min(offset + chunk_size, total_len)
                    chunk = payload[offset:end]
                    self._ble.gatts_notify(conn_handle, self._tx_handle, chunk)
                    chunk_num += 1
                    offset = end
                    # 让 BLE 栈有时间处理通知队列
                    if offset < total_len:
                        time.sleep_ms(10)
                
                print(f"[BLE] sent command in {chunk_num} chunk(s) of <= {chunk_size} bytes (mtu={mtu}):", obj)
                
            except Exception as e:
                print("[BLE] failed to send command:", e)

    def send_music_control(self, action):
        self.send_command({"t": "music", "n": action})

    def send_call_action(self, action):
        self.send_command({"t": "call", "n": action})

    def send_notify_action(self, notif_id, action, msg=None):
        cmd = {"t": "notify", "id": notif_id, "n": action}
        if msg is not None:
            cmd["msg"] = msg
        self.send_command(cmd)

    def send_version(self, firmware="1.0.0", hardware="0.0.0"):
        self.send_command({"t": "ver", "fw": firmware, "hw": hardware})

    # ---- Parsing -------------------------------------------------------

    def _handle_line(self, line):
        try:
            # 尝试解码为字符串
            if isinstance(line, bytes):
                text = line.replace(b'\xa0', b'').decode('utf-8', errors='ignore')
            else:
                text = line.replace('\xa0', '')
        except Exception as e:
            print("[BLE] failed to decode line as utf-8:", e, line)
            return

        # 清理控制字符（保留必要的）
        text = text.replace("\x10", "").replace("\x03", "").strip()
        if not text:
            return

        print("[BLE] complete line:", text[:100] + "..." if len(text) > 100 else text)

        # 处理时间同步命令（必须在 GB 之前）
        if text.startswith("setTime("):
            self._handle_time_sync(text)
            return

        # 处理 GB 格式的数据
        if text.startswith("GB(") and text.endswith(")"):
            data = self._parse_gb_object(text)
            if data is not None:
                self._dispatch(data)
            return
        
        # 尝试处理其他格式的 JSON（没有 GB 包裹的）
        if text.startswith("{") and text.endswith("}"):
            try:
                import json
                data = json.loads(text)
                if isinstance(data, dict):
                    print("[BLE] parsed direct JSON:", data)
                    self._dispatch(data)
                    return
            except:
                pass
        
        # 如果都没匹配，记录警告
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
            # setTime(...) 做校准，不是连接时那条完整命令），沿用当前
            # 已知的时区偏移，而不是直接在 tz_match.group(1) 上崩溃、
            # 导致这次时间同步整个被吞掉。
            tz_offsetL = tz_offset
            print("[BLE] time sync: no setTimeZone(...) in this line, reusing tz_offset =", tz_offsetL)
        epoch = float(time_match.group(1)) - (tz_offsetL * 3600)
        print(f"[BLE] time sync: epoch={epoch}, tz_offset={tz_offsetL}h")
        if self._on_time_sync is not None:
            self._on_time_sync(epoch, tz_offsetL)

    @staticmethod
    def _parse_gb_object(raw_text):
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
                    self._on_notify_bitmap(data.get("src", "unknown"), title_field, body_field, notif_id)
                else:
                    self._on_notify(data.get("src", "unknown"), title_field, body_field, notif_id)
                    
            elif t == "notify-":
                self._on_notify_dismiss(data.get("id"))
                    
            elif t == "call":
                self._on_call(data.get("cmd"), data.get("name", ""), data.get("number", ""))
                    
            elif t == "weather":
                self._on_weather(data)
                    
            elif t == "musicinfo":
                self._on_musicinfo(data)
                    
            elif t == "musicstate":
                self._on_musicstate(data)
                    
            elif t == "alarm":
                self._on_alarm(data.get("d", []))
                    
            elif t == "find":
                self._on_find(data.get("n", False))
                    
            elif t == "vibrate":
                self._on_vibrate(data.get("n", 0))
                    
            elif t == "act":
                self._on_activity(data.get("hrm", False), data.get("stp", False), data.get("int", 0))
                    
            elif t == "actfetch":
                self._on_activity_fetch(data.get("ts", 0))
                    
            elif t == "listRecs" or t == "fetchRec":
                self._on_activity_fetch(data.get("id", ""))
                    
            elif t == "calendar":
                self._on_calendar(data.get("id"), data.get("type"), data.get("timestamp"),
                                 data.get("durationInSeconds"), data.get("title"),
                                 data.get("description"), data.get("location"),
                                 data.get("calName"), data.get("color"), data.get("allDay", False))
                                 
            elif t == "calendar-":
                self._on_calendar_remove(data.get("id"))
                    
            elif t == "gps":
                self._on_gps(data.get("lat"), data.get("lon"), data.get("alt"),
                            data.get("speed"), data.get("course"), data.get("time"),
                            data.get("satellites"), data.get("hdop"), data.get("externalSource", False))
                            
            elif t == "gps_power":
                self._on_gps_power(data.get("status", False))
                    
            elif t == "is_gps_active":
                self._on_gps_power("query")
                    
            elif t == "nav":
                if "instr" in data:
                    self._on_navigation(data.get("instr"), data.get("distance"),
                                       data.get("action"), data.get("eta"))
                else:
                    self._on_navigation(None)
                    
            elif t == "http":
                self._on_http_request(data.get("url"), data.get("xpath"),
                                     data.get("id"), data.get("insecure", False))
                    
            else:
                # 未知类型，抛异常触发降级
                raise ValueError(f"Unknown type: {t}")
                
            # 如果执行到这里，说明成功处理了，直接返回
            return
            
        except Exception as e:
            # 任何异常（函数为空、类型错误等）都降级为通知
            print(f"[BLE] Fallback to notification: {e}")
            
            if self._on_notify is not None:
                # 提取标题
                title = (data.get("title") or data.get("subject") or 
                        data.get("cmd") or data.get("instr") or 
                        data.get("name") or t.upper() if t else "Unknown")
                
                # 提取内容
                body = (data.get("body") or data.get("number") or 
                       data.get("distance") or data.get("url") or "")
                
                # 收集其他字段作为额外信息
                extra = []
                for key, value in data.items():
                    if key not in ["t", "title", "subject", "body", "cmd", "instr", 
                                  "name", "number", "distance", "url", "id"]:
                        if value:
                            extra.append(f"{key}: {value}")
                
                if extra:
                    body = f"{body} ({', '.join(extra)})" if body else ", ".join(extra)
                
                if not body:
                    import json
                    body = json.dumps(data)[:100]
                
                self._on_notify("BLE", title, body, data.get("id"))
            
            # 同时也调用 _on_unknown
            if self._on_unknown is not None:
                self._on_unknown(t, data)

_BLE_BRIDGE = BLENotifyBridge(ble=_BLE)

