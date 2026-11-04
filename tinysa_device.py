"""tinySA との通信部分（実機 + ダミー/シミュレーション）。

tinySA は USB シリアル(CDC) 上でシェルを提供する。
  - コマンドは "cmd\r" で送信、応答は "cmd\r\n"(エコー) + 本文 + "ch> "(プロンプト)
  - sweep        -> "start stop points"
  - frequencies  -> 1行1周波数 (Hz)
  - data 0..2    -> 1行1値 (0=temp, 1=stored trace, 2=measurement)
  - marker       -> "id index freq value" (有効マーカーのみ)
  - trace        -> "n: unit reflevel scale [, frozen]"
  - capture      -> 画面の生ピクセル RGB565 (tinySA:320x240 / Ultra:480x320)
"""

import math
import random
import re
import threading
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

PROMPT = b"ch> "
_PROMPT_END = re.compile(rb"ch> [\r\n]*$")
SCREEN_SIZES = {320 * 240 * 2: (320, 240), 480 * 320 * 2: (480, 320)}
_NUM = r"[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?"


@dataclass
class Marker:
    id: int
    index: int
    freq: float
    value: float


@dataclass
class TraceInfo:
    id: int
    unit: str
    reflevel: float
    scale: float
    frozen: bool


class TinySABase:
    """コマンド応答の解析など、実機/ダミー共通の処理。"""

    version = ""
    on_timeout = None
    on_slow = None  # 長い掃引を待ち続けているときの通知先 (コマンド, 待った秒数)

    def cmd(self, text, timeout=3.0, max_wait=None):  # -> list[str]
        raise NotImplementedError

    def capture_raw(self):  # -> bytes (RGB565)
        raise NotImplementedError

    def close(self):
        pass

    # ---- 解析付きの取得関数 ----
    def get_sweep(self):
        for line in self.cmd("sweep"):
            nums = re.findall(_NUM, line)
            if len(nums) >= 3:
                return float(nums[0]), float(nums[1]), int(float(nums[2]))
        raise RuntimeError("could not parse the reply to sweep")

    def get_frequencies(self):
        return np.array([float(x) for x in self.cmd("frequencies") if re.fullmatch(_NUM, x.strip())])

    def get_data(self, which=2):
        return np.array([float(x) for x in self.cmd(f"data {which}") if re.fullmatch(_NUM, x.strip())])

    def get_markers(self):
        out = []
        for line in self.cmd("marker"):
            nums = re.findall(_NUM, line)
            if len(nums) >= 4:
                out.append(Marker(int(nums[0]), int(nums[1]), float(nums[2]), float(nums[3])))
        return out

    def get_traces(self):
        out = []
        for line in self.cmd("trace"):
            m = re.match(rf"\s*(\d+):\s+(\S+)\s+({_NUM})\s+({_NUM})", line)
            if m:
                out.append(TraceInfo(int(m[1]), m[2], float(m[3]), float(m[4]), "frozen" in line))
        return out

    @property
    def is_ultra(self):
        return "tinySA4" in self.version or "ULTRA" in self.version.upper()

    @property
    def max_points(self):
        return 450 if self.is_ultra else 290

    def set_rbw(self, khz):
        """RBW を kHz で設定（"auto" または 0 で自動）。"""
        self.cmd("rbw auto" if khz in ("auto", 0) else f"rbw {khz:g}")

    def set_ultra(self, on=True):
        """Ultra / Ultra+ の ULTRA モード（ノーマルモードの上限を超える周波数を測る）を切り替える。"""
        self.cmd("ultra on" if on else "ultra off")

    def set_attenuation(self, db):
        """内部アッテネータを dB で設定（0〜31）。表示レベルは本体側で補正される。"""
        self.cmd(f"attenuate {int(db)}")

    def get_attenuation(self):
        """現在の内部アッテネータ [dB]。応答の最後の数値の行を読む。"""
        for line in reversed(self.cmd("attenuate")):
            if re.fullmatch(rf"\s*{_NUM}\s*", line):
                return float(line)
        return None

    def get_rbw(self):
        """現在の実際の RBW [kHz]。応答の "3kHz" / "200Hz" / "1000Hz" の行を読む
        （本体は単位の前に k・M を付けて答える。ファームウェアの cmd_rbw は %F = 接頭語付きの書式）。"""
        for line in self.cmd("rbw"):
            m = re.search(rf"({_NUM})\s*([kMG]?)Hz", line)
            if m:
                return float(m[1]) * {"": 1, "k": 1e3, "M": 1e6, "G": 1e9}[m[2]] / 1e3
        return None

    def get_calc(self):
        """トレース 1 の計算モード（"OFF" / "MAXH" など）。引数なしの calc の 2 行目を読む。読めなければ ""。"""
        lines = self.cmd("calc")
        last = lines[-1].strip() if lines else ""
        return "" if "calc" in last.lower() else last.upper()  # 1 行目（usage: calc ...）しか無ければ読めない

    def get_identity(self):
        """(型番, ハードの版, ファームウェアの版)。info の 1 行目と version の答えから読む。読めない所は ""。"""
        model = hw = fw = ""
        try:
            lines = self.cmd("info")
            model = lines[0].strip() if lines else ""
        except Exception:
            pass
        for part in self.version.split("HW Version:"):
            part = part.strip()
            if part.lower().startswith("tinysa"):
                fw = part.split()[0]
        if "HW Version:" in self.version:
            hw = self.version.split("HW Version:", 1)[1].strip()
        return model, hw, fw

    def has_command(self, name):
        """本体のファームウェアがその命令を持っているか（help の一覧で調べる。結果は覚えておく）。"""
        if getattr(self, "_commands", None) is None:
            words = set()
            for line in self.cmd("help"):
                words.update(line.replace(":", " ").split())
            self._commands = words
        return name in self._commands

    def wait_scan(self, timeout, max_wait=None):
        """今の掃引が最後まで終わるのを待ち、本体を一時停止させる（waitscan 命令。ファームウェア v1.4-216 以降）。
        max_wait を渡すと、timeout を過ぎても本体が命令を受け取っている（エコーが返っている）間は max_wait 秒まで待つ。"""
        self.cmd("waitscan", timeout=timeout, max_wait=max_wait)

    def get_sweeptime(self):
        """1 回の掃引にかかる時間 [秒]。応答の "4.204s" / "700ms" / "500us" の行を読む。読めなければ None。"""
        for line in self.cmd("sweeptime"):
            m = re.fullmatch(rf"\s*({_NUM})\s*(m|u)?s\s*", line)
            if m:
                return float(m[1]) * {"m": 1e-3, "u": 1e-6}.get(m[2], 1.0)
        return None

    def set_sweep(self, start, stop):
        """本体の掃引範囲（開始・終了 Hz）を設定。"""
        self.cmd(f"sweep {int(start)} {int(stop)}")

    def scan(self, start, stop, points, raw=False):
        """指定範囲を1回掃引して (周波数配列, レベル配列) を返す。
        応答は1行1点 "freq value 0.0"。outmask: 1=周波数, 2=測定トレース(calc 適用後), 8=生の値(calc 前)。
        raw=True なら本体の MAX HOLD 等の影響を受けない生の値を読む。"""
        f, v = [], []
        for line in self.cmd(f"scan {int(start)} {int(stop)} {int(points)} {9 if raw else 3}", timeout=30.0):
            nums = re.findall(_NUM, line)
            if len(nums) >= 2:
                f.append(float(nums[0]))
                v.append(float(nums[1]))
        if raw and not v:  # 古いファームで生の値が読めない場合は測定トレースで
            return self.scan(start, stop, points, raw=False)
        return np.array(f), np.array(v)

    def capture_image(self, big_endian=True):
        raw = self.capture_raw()
        if len(raw) not in SCREEN_SIZES:
            raise RuntimeError(f"unexpected capture size: {len(raw)} bytes")
        w, h = SCREEN_SIZES[len(raw)]
        px = np.frombuffer(raw, dtype=">u2" if big_endian else "<u2").astype(np.uint32)
        r = ((px >> 11) & 0x1F) * 255 // 31
        g = ((px >> 5) & 0x3F) * 255 // 63
        b = (px & 0x1F) * 255 // 31
        rgb = np.stack([r, g, b], axis=-1).astype(np.uint8).reshape(h, w, 3)
        return Image.fromarray(rgb, "RGB")


class TinySA(TinySABase):
    """実機（COMポート）接続。"""

    def __init__(self, port):
        import serial  # pyserial

        # 本体が固まったときにアプリまで止まらないよう、書き込みにも制限時間を付ける
        self.ser = serial.Serial(port, 115200, timeout=0.2, write_timeout=2)
        self.lock = threading.Lock()
        self.dirty = False  # 前の応答が途中で切れた（残りが後から届くかもしれない）
        self.on_timeout = None  # 合図 "ch> " が届かなかったときの通知先 (コマンド, 受信バイト数, 最後の受信)
        time.sleep(0.1)
        self.ser.reset_input_buffer()
        # プロンプトを一度出させて同期を取る
        with self.lock:
            self.ser.write(b"\r")
            try:
                self._read_until(lambda b: b.endswith(PROMPT), 2.0)
            except TimeoutError:
                pass
        self.version = " ".join(self.cmd("version"))

    def _read_until(self, done, timeout, idle=2.0, max_wait=None, echo=None):
        """done(buf) が真になるまで読む。最初の応答（掃引の終わり）は timeout 秒まで待つが、
        本文が流れ始めた後に idle 秒途切れたら、それ以上は来ないとみなす。
        max_wait と echo を渡すと、timeout を過ぎてもエコーだけ届いて本文がまだ（掃引の途中）なら max_wait 秒まで待ち続ける
        （狭い RBW などで掃引が長いとき、途中で諦めて次の命令を送ると、返事がずっとずれたままになるため。2026-11-04）。"""
        buf = bytearray()
        started = time.time()
        deadline = started + timeout
        while True:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                buf += chunk
                streaming = buf.count(b"\n") >= 2  # エコー行の後に本文が来ている
                deadline = time.time() + (min(timeout, idle) if streaming else timeout)
                if done(buf):
                    return bytes(buf)
            elif time.time() > deadline:
                elapsed = time.time() - started
                if max_wait and elapsed < max_wait and buf.count(b"\n") < 2 and echo and echo in buf:
                    if self.on_slow:
                        self.on_slow(echo.decode("ascii", errors="replace").strip(), elapsed)
                    deadline = time.time() + min(2.0, max_wait - elapsed)
                    continue
                err = TimeoutError(f"no reply from tinySA (received {len(buf)} bytes, "
                                   f"last: {bytes(buf[-40:])!r})")
                err.partial = bytes(buf)
                raise err

    def cmd(self, text, timeout=3.0, max_wait=None):
        with self.lock:
            # 前のコマンドがタイムアウトして応答の残りが届いていると、次の応答とずれてしまうので捨てる
            # （毎回は捨てない。実機では毎回捨てなくても data 2 が正しく届くことを確かめた）
            if self.dirty:
                self.ser.reset_input_buffer()
                self.dirty = False
            self.ser.write((text + "\r").encode("ascii"))
            try:
                # 終わりの合図 "ch> " の後ろに改行などが付いて届いても終わりと判断する
                raw = self._read_until(lambda b: _PROMPT_END.search(b[-16:]) is not None, timeout,
                                       max_wait=max_wait, echo=(text + "\r\n").encode("ascii"))
            except TimeoutError as e:
                self.dirty = True
                # 合図だけ届かず本文は届いている場合（実機で発生）は、届いた行を使う。2 行未満なら失敗扱い
                part = getattr(e, "partial", b"")
                if part.count(b"\n") < 3:
                    raise
                if self.on_timeout:
                    self.on_timeout(text, len(part), part[-40:])
                raw = part + PROMPT
        raw = raw[: raw.rfind(PROMPT)] if PROMPT in raw else raw
        lines = raw.decode("ascii", errors="replace").replace("\r", "").split("\n")
        if lines and lines[0].strip() == text:  # エコー行を除去
            lines = lines[1:]
        return [ln for ln in lines if ln.strip()]

    def capture_raw(self):
        echo = b"capture\r\n"

        def pixels(b):
            i = b.find(echo)
            return b[i + len(echo):] if i >= 0 else None

        def done(b):
            # エコー + ピクセルデータ + "ch> "。データ長が既知の画面サイズに一致したら終了
            p = pixels(b)
            return p is not None and p.endswith(PROMPT) and (len(p) - len(PROMPT)) in SCREEN_SIZES

        with self.lock:
            if self.dirty:
                self.ser.reset_input_buffer()
                self.dirty = False
            self.ser.write(b"capture\r")
            try:
                raw = self._read_until(done, 5.0)
            except TimeoutError:
                self.dirty = True  # 残りが後から届くかもしれないので、次の命令の前に捨てる
                raise
        return pixels(raw)[: -len(PROMPT)]

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


class SimulatedTinySA(TinySABase):
    """実機が無いときの動作確認用ダミー。実機と同じテキスト応答を返す。"""

    version = "tinySA4 (simulation) v0.0 HW Version:V0.5.4 ZS407"
    # 疑似送信機: 145.000 MHz 付近の送信波 + 高調波 + 適当なスプリアス
    CARRIER = 145.0e6
    NORMAL_MAX = 900e6  # Ultra+ のノーマルモード上限。ultra off だとこれより上は見えない

    def __init__(self):
        self.start, self.stop, self.points = 0.0, 350e6, 450
        self.reflevel, self.scale = 10.0, 10.0
        self.rbw_khz = 300.0
        self.paused = False
        self.calc = "off"
        self.t0 = time.time()
        self.stored = None
        self.last = None
        self.ultra = False
        self.atten = 0.0
        self.set_markers = {}  # 本体のマーカー {番号: 点の番号 or None（消えている）}。動作確認では直接書き換えて本体での操作をまねる
        self.ext_gain = 0.0
        self.marker_delta = {}  # {番号: 差（Δ）表示か}

    def _freqs(self):
        return np.linspace(self.start, self.stop, self.points)

    def _model(self, f):
        """周波数配列 f [Hz] に対するレベル [dBm]（RBW を反映した簡易モデル）。"""
        t = time.time() - self.t0
        rbw = self.rbw_khz * 1e3
        # 内部ATT を入れるとノイズフロアがその分上がる（信号のレベルは本体が補正するので変わらない）
        y = -110 + self.atten + 10 * math.log10(max(self.rbw_khz, 0.1)) + np.random.normal(0, 2.0, f.size)
        fc = self.CARRIER + 300 * math.sin(t)  # 少し揺れる
        sigs = [(fc, -1.0 + random.random(), 0.1e3)]  # 無変調の搬送波（幅は RBW で決まる）
        sigs += [(fc * n, lv, 1.5e3 * n) for n, lv in [(2, -58), (3, -64), (4, -72), (5, -75), (7, -82), (9, -85)]]
        sigs += [(100.0e6, -78, 2e3), (12.0e6, -88, 1e3), (1.03e9, -80, 5e3)]
        for f0, lv, bw in sigs:
            w = max(bw, rbw)
            df = np.abs(f - f0)
            shape = lv - (df / w) ** 2 * 6
            skirt = lv - 45 - 20 * np.log10(1 + df / 5e3)  # 送信波のすそ（帯域外っぽい成分）
            y = np.maximum(y, np.maximum(shape, skirt if f0 == fc else -200))
        if not self.ultra:
            y = np.where(f > self.NORMAL_MAX, -120 + np.random.normal(0, 2.0, f.size), y)
        return y

    def _spectrum(self):
        if self.paused and self.last is not None:  # pause 中は波形が止まる
            return self.last
        y = self._model(self._freqs())
        if self.calc == "maxh" and self.last is not None and self.last.shape == y.shape:
            y = np.fmax(self.last, y)
        self.last = y
        if self.stored is None:
            self.stored = y - 5
        return y

    def _markers(self):
        y = self.last if self.last is not None else self._spectrum()
        f = self._freqs()
        return [Marker(n, i, f[i], y[i]) for n, i in sorted(self.set_markers.items())
                if i is not None and 0 <= i < len(f)]

    def cmd(self, text, timeout=3.0, max_wait=None):
        time.sleep(0.03)  # USB の往復時間っぽく
        args = text.split()
        name = args[0] if args else ""
        if name == "version":
            return [self.version]
        if name == "sweep":
            if len(args) >= 3 and args[1] == "cw":  # ゼロスパン（開始＝終了）
                self.start = self.stop = float(args[2])
                self.stored = None
                return []
            if len(args) >= 3 and args[1] in ("start", "stop", "center", "span"):
                v = float(args[2])
                c, s = (self.start + self.stop) / 2, self.stop - self.start
                if args[1] == "start":
                    self.start, self.stop = v, max(self.stop, v)
                elif args[1] == "stop":
                    self.start, self.stop = min(self.start, v), v
                elif args[1] == "center":
                    self.start, self.stop = max(v - s / 2, 0.0), v + s / 2
                else:
                    self.start, self.stop = max(c - v / 2, 0.0), c + v / 2
                self.stored = None
                return []
            if len(args) >= 3:
                self.start, self.stop = float(args[1]), float(args[2])
                if len(args) >= 4:
                    self.points = int(args[3])
                self.stored = None
                return []
            return [f"{int(self.start)} {int(self.stop)} {self.points}"]
        if name == "sweeptime":
            return ["usage: sweeptime 0.003..60", "0.500s"]
        if name == "info":
            return ["tinySA ULTRA+ ZS407 (simulation)", "Version: tinySA4 (simulation)"]
        if name == "help":
            return ["commands: sweep pause resume wait waitscan trace marker rbw attenuate ext_gain ultra calc sweeptime",
                    "Other commands: version data frequencies scan capture help"]
        if name == "waitscan":  # 1 回掃引して一時停止（一時停止中でも 1 回掃引する。実機と同じ）
            time.sleep(0.5)
            self.paused = False
            self._spectrum()
            self.paused = True
            return []
        if name in ("pause", "resume"):
            self.paused = name == "pause"
            return []
        if name == "calc":
            opt = [a for a in args[1:] if not a.isdigit()]
            if opt:
                self.calc = opt[0]
                if self.calc == "off":
                    self.last = None
                return []
            return ["calc [{trace#}] off|minh|maxh|maxd|aver4|aver16|aver|quasi|log|lin", self.calc.upper()]
        if name == "rbw":
            if len(args) >= 2:
                self.rbw_auto = args[1] in ("auto", "0")
                self.rbw_khz = 300.0 if self.rbw_auto else min(max(float(args[1]), 0.2), 850)
                return []
            hz = self.rbw_khz * 1e3  # 実機と同じく 1000 Hz を超えると k を付ける
            return ["rbw 0.2..850|auto", f"{hz / 1e3:g}kHz" if hz > 1000 else f"{hz:g}Hz"]
        if name == "ext_gain":  # 実機と同じく、引数なしでは今の値を返さない
            if len(args) >= 2:
                self.ext_gain = float(args[1])
                return []
            return ["usage: ext_gain -100.0..+100.0"]
        if name == "attenuate":
            if len(args) >= 2:
                if args[1] != "auto":
                    self.atten = min(max(float(args[1]), 0), 31)
                return []
            return ["attenuate 0..31|auto", f"{self.atten:4.2f}"]
        if name == "ultra":
            if len(args) >= 2:
                self.ultra = args[1] in ("on", "auto")
                return []
            return ["ultra off|on|auto|start|harm {freq}"]
        if name == "scan":
            s, e, n = float(args[1]), float(args[2]), int(args[3])
            if getattr(self, "rbw_auto", False):  # AUTO: SPAN に合わせた RBW（ゼロスパンは 1 kHz）
                self.rbw_khz = min(max((e - s) / 100e3, 0.2), 850) if e > s else 1.0
            time.sleep(0.05 + n * 0.0003 * min(10.0, max(1.0, 30 / self.rbw_khz)))  # RBW が狭いほど遅い
            f = np.linspace(s, e, n)
            y = self._model(f)
            return [f"{int(fi)} {v:e} 0.000000 " for fi, v in zip(f, y)]
        if name == "frequencies":
            return [str(int(x)) for x in self._freqs()]
        if name == "data":
            which = int(args[1]) if len(args) > 1 else 0
            y = self._spectrum() if which != 1 else (self.stored if self.stored is not None else self._spectrum())
            return [f"{v:e}" for v in y]
        if name == "marker":
            if len(args) >= 4 and args[2] == "delta":  # marker {n} delta {基準の番号}|off … 差（Δ）表示の切り替え
                self.marker_delta[int(args[1])] = args[3] != "off"
                return []
            if len(args) >= 3:  # marker {n} {点の番号}|off … 本体のマーカーの一覧を書き換える
                self.set_markers[int(args[1])] = None if args[2] == "off" else int(args[2])
                return []
            return [f"{m.id} {m.index} {int(m.freq)} {m.value:.2e}" for m in self._markers()]
        if name == "trace":
            if len(args) >= 3 and args[1] == "reflevel":
                self.reflevel = float(args[2])
                return []
            if len(args) >= 3 and args[1] == "scale":
                self.scale = float(args[2])
                return []
            return [f"1: dBm {self.reflevel:f} {self.scale:f} ",
                    f"2: dBm {self.reflevel:f} {self.scale:f} , frozen"]
        return [f"{name}?"]

    def capture_raw(self):
        """疑似的な本体画面を描いて RGB565(ビッグエンディアン) で返す。"""
        w, h = 480, 320  # Ultra+ の画面サイズ
        img = Image.new("RGB", (w, h), "black")
        d = ImageDraw.Draw(img)
        gx0, gy0, gw, gh = 40, 10, 430, 290
        for k in range(11):
            x = gx0 + gw * k / 10
            d.line([(x, gy0), (x, gy0 + gh)], fill=(64, 64, 64))
        for k in range(11):
            yv = gy0 + gh * k / 10
            d.line([(gx0, yv), (gx0 + gw, yv)], fill=(64, 64, 64))
        y = self._spectrum()
        n = y.size
        lo = self.reflevel - 10 * self.scale
        pts = [(gx0 + gw * i / (n - 1),
                gy0 + gh * (1 - (min(max(v, lo), self.reflevel) - lo) / (10 * self.scale))) for i, v in enumerate(y)]
        d.line(pts, fill=(255, 255, 0))
        for m in self._markers():
            x, yy = pts[m.index]
            d.polygon([(x - 4, yy - 10), (x + 4, yy - 10), (x, yy - 2)], fill=(0, 255, 255))
            d.text((x - 3, yy - 22), str(m.id), fill=(0, 255, 255))
        d.text((2, 2), f"{self.reflevel:.0f}", fill="white")
        d.text((gx0, h - 14), f"START {self.start/1e6:.1f}MHz   STOP {self.stop/1e6:.1f}MHz  (SIM)", fill="white")
        a = np.asarray(img).astype(np.uint16)
        px = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
        return px.astype(">u2").tobytes()
