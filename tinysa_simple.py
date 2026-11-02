"""tinySA シンプル ビューア（画面の文字は英語。英語圏の人にも使ってもらうため）

起動:  python tinysa_simple.py
  - COM ポートを選んで「Connect」→ tinySA の掃引をそのままパソコンに表示する
    （表示する範囲は本体の設定に合わせる）
  - 「Settings...」で本体の周波数範囲・RBW・アッテネーター・External gain を、「Max hold」で本体の MAX HOLD を切り替える
  - 「Pause」で本体とグラフを今の掃引のところで止める（MAX HOLD は消えない）
  - 「Bandwidth lines」で占有周波数帯幅の目安の点線（中心 ± 幅/2）を引く。グラフの右上に測定条件を出す
  - マーカー M1〜M4: ボタンかキー 1〜4 で選び、グラフをクリックで置く、← → で 1 点ずつ動かす（本体のマーカーと両方向で連動）
  - 「Save device screen」で本体の画面を、「Save graph」でグラフを PNG に保存する（captures フォルダ）
  - ポート一覧の「Dummy (no device)」で、実機なしでも動作確認できる
"""

import json
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from tinysa_device import SimulatedTinySA, TinySA

matplotlib.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

DUMMY = "Dummy (no device)"
GRID_DIVS = 10         # 本体画面の縦の目盛り数（基準レベルから下へ scale×10。ファームウェアの NGRIDY。どの機種も 10）
# 本体の掃引時間（sweeptime）の何倍待ってから次を読むか。実際の掃引は sweeptime より長い（実機で約 1.36 倍）ので、
# 最初は 1.5 倍で始め、読んだデータを見て MIN〜MAX の間で自動で合わせる
START_FACTOR = 1.5
MIN_FACTOR = 1.05
MAX_FACTOR = 3.0
TAIL_TOL = 2           # 右端からこの点数までに変化があれば、掃引は最後まで終わったとみなす
MIN_INTERVAL = 0.3     # 読み取りの最短間隔（秒）
MARKER_COUNT = 4
MARKER_COLORS = ["#00e5ff", "#ff5cf0", "#7CFC00", "#ff9f1a"]
# exe にした場合は exe と同じ場所に保存（__file__ は一時展開フォルダを指すため）
APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
ERROR_LOG = os.path.join(APP_DIR, "error_log.txt")
# アイコンなど exe に同梱するファイルの場所（exe では一時展開フォルダ）
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
CAPTURE_DIR = os.path.join(APP_DIR, "captures")  # .gitignore で GitHub に上げない
GRAPH_SAVE_PX = (1200, 800)  # 保存するグラフの大きさ（ウィンドウの大きさに関係なく一定）
# RBW の選択肢（本体メニューと同じ。ファームウェアの ui.c に固定で書かれていて、一覧を返す命令は無い）。
# 表示 → rbw 命令の値（kHz）。将来のファームウェアで本体の選択肢が変わったら合わせる
RBW_CHOICES = {
    "ultra": [("AUTO", "auto"), ("200", "0.2"), ("1k", "1"), ("3k", "3"), ("10k", "10"), ("30k", "30"),
              ("100k", "100"), ("300k", "300"), ("600k", "600"), ("850k", "850")],
    "basic": [("AUTO", "auto"), ("3k", "3"), ("10k", "10"), ("30k", "30"), ("100k", "100"), ("300k", "300"),
              ("600k", "600")],
}
BW_LINE_CHOICES = ["0 (off)", "0.5", "3", "6", "30", "40"]  # 占有周波数帯幅の目安線 [kHz]（全体の幅）
ATTEN_CHOICES = ["AUTO", "0", "5", "10", "15", "20", "25", "30"]  # 内部アッテネーター [dB]。0〜30 の整数も直接入れられる
ATTEN_MAX = 30
EXT_GAIN_RANGE = (-100.0, 100.0)  # ext_gain 命令の範囲 [dB]
# External gain は本体から読めないので、最後に設定した値を機種ごとに覚えておく（.gitignore で GitHub に上げない）
SETTINGS_FILE = os.path.join(APP_DIR, "tinysa_simple_settings.json")


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass


def fmt_rbw(khz):
    """RBW の表示（"200 Hz" / "3 kHz"）。"""
    if khz is None:
        return "-"
    return f"{khz * 1e3:.4g} Hz" if khz < 1 else f"{khz:.4g} kHz"


def log(text):
    """原因調査用に error_log.txt に追記する（exe では画面に出ないため）。"""
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except OSError:
        pass


def capture_path(kind):
    """captures フォルダに日時の名前で保存する場所を返す（同じ秒に 2 回押しても上書きしない）。"""
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    base = os.path.join(CAPTURE_DIR, f"{kind}_{datetime.now():%Y%m%d_%H%M%S}")
    path, k = base + ".png", 2
    while os.path.exists(path):
        path, k = f"{base}_{k}.png", k + 1
    return path


def list_ports():
    try:
        from serial.tools import list_ports as lp

        ports = [f"{p.device}  {p.description}" for p in lp.comports()]
    except Exception:
        ports = []
    return ports + [DUMMY]


class Worker(threading.Thread):
    """tinySA から掃引結果を読み続けるスレッド。本体の掃引は止めず、本体が測った値を読むだけ。
    本体と話すのはこのスレッドだけ（画面の取り込みもここで行い、命令がぶつからないようにする）。"""

    def __init__(self, dev, out_q):
        super().__init__(daemon=True)
        self.dev, self.q = dev, out_q
        self.stop_ev = threading.Event()
        self.capture_ev = threading.Event()  # 本体画面の取り込みを頼まれた
        self.marker_lock = threading.Lock()
        self.marker_req = {}  # 本体へ送るマーカー {番号: 点の番号 or None（消す）}。最新の位置だけ残す
        self.marker_seq = 0   # 頼まれたマーカーの移動のうち、本体へ送り終えた最後の通し番号
        self.cmd_req = []     # 本体へ送る設定の命令（頼まれた順のまとまり。marker_lock で守る）
        self.pause_ev = threading.Event()  # Pause 中（waitscan を送らないので、本体は今の掃引が終わったところで止まる）
        self.freqs = None
        self.ylim = None
        self.sweeptime = 0.5
        self.rbw = None   # 本体の実際の RBW [kHz]
        self.calc = ""    # 本体のトレース 1 の計算モード（"MAXH" なら MAX HOLD 中）
        self.atten = None  # 本体の内部アッテネーター [dB]
        self.errors = 0

    def run(self):
        """本体は命令（data 2 も含む）が来るたびに掃引を左端からやり直すので、掃引が最後まで終わってから読む。
        新しいファームウェア（waitscan がある）なら本体に終わりを待ってもらい、古いものは待つ時間を推測する。"""
        try:
            use_waitscan = self.dev.has_command("waitscan")
        except Exception:
            use_waitscan = False
        self.q.put(("mode", use_waitscan))
        if use_waitscan:
            self._run_waitscan()
            try:
                self.dev.cmd("resume")  # 一時停止のまま残さない
            except Exception:
                pass
        else:
            self._run_timed()

    def _read_settings(self):
        """本体の範囲・縦軸・掃引時間を読む。範囲が変わったら周波数の一覧を読み直す。
        戻り値: (つないだ直後か, 範囲が変わったか)"""
        start, stop, pts = self.dev.get_sweep()
        f = self.freqs
        first = f is None
        changed = first or len(f) != pts or f[0] != start or f[-1] != stop
        if changed:
            f = self.dev.get_frequencies()
            self.freqs = f if len(f) == pts else np.linspace(start, stop, pts)
        tr = self.dev.get_traces()  # 縦軸は本体の基準レベル・目盛りに合わせる
        if tr:
            self.ylim = (tr[0].reflevel - tr[0].scale * GRID_DIVS, tr[0].reflevel)
        st = self.dev.get_sweeptime()
        if st:
            self.sweeptime = st
        # RBW・MAX HOLD・アッテネーターは測定条件の表示とチェックボックス用。読めなくても掃引の表示は続ける
        try:
            self.rbw = self.dev.get_rbw()
        except Exception:
            self.rbw = None
        try:
            self.calc = self.dev.get_calc()
        except Exception:
            self.calc = ""
        try:
            self.atten = self.dev.get_attenuation()
        except Exception:
            self.atten = None
        return first, changed

    def _info(self):
        return {"rbw": self.rbw, "calc": self.calc, "atten": self.atten}

    def send(self, cmds):
        """画面のスレッドから呼ぶ。設定の命令をまとめて頼む（次の掃引の合間に送り、終わったら "sent" を返す）。"""
        with self.marker_lock:
            self.cmd_req.append(list(cmds))

    def set_marker(self, n, index, seq):
        """画面のスレッドから呼ぶ。本体のマーカー n を index 点目へ（None なら消す）。次の掃引の合間に送る。
        seq は画面がつける通し番号（送り終えた番号を本体のマーカーの一覧と一緒に返す）。"""
        with self.marker_lock:
            self.marker_req[n] = (index, seq)

    def _between_sweeps(self):
        """データを読み終えた後（waitscan のときは本体が一時停止中なので、掃引を邪魔しない）に、
        頼まれていた設定の変更・マーカーの移動を行い、本体のマーカーを読み、画面を取り込む。"""
        self._send_commands()
        self._send_markers()
        self._read_markers()
        self._capture_if_requested()  # マーカーを動かした後の画面を取り込む

    def _read_markers(self):
        """本体のマーカー（M1〜M4）を読み、("markers", {番号: (点の番号, 周波数)}, 送り終えた通し番号) を返す。
        アプリの移動を送った後に読むので、一覧にはアプリで動かした分も入っている。本体で消したマーカーは一覧に無い。"""
        try:
            got = {m.id: (m.index, m.freq) for m in self.dev.get_markers() if 1 <= m.id <= MARKER_COUNT}
        except Exception:
            import traceback
            log("read markers\n" + traceback.format_exc())
            return
        self.q.put(("markers", got, self.marker_seq))

    def _send_commands(self):
        """頼まれた設定の命令（周波数・RBW・MAX HOLD）を送る。まとまりごとに ("sent", 失敗の説明) を返す。
        画面はこれより前に届いた波形（変更前の掃引）を捨て、"sent" の後に届いた波形から描く。"""
        with self.marker_lock:
            req, self.cmd_req = self.cmd_req, []
        for cmds in req:
            err = ""
            for c in cmds:
                try:
                    self.dev.cmd(c)
                except Exception as e:
                    import traceback
                    log(f"send {c}\n" + traceback.format_exc())
                    err = f"Could not send '{c}' to the tinySA: {e}"
            self.q.put(("sent", err))

    def _send_markers(self):
        """本体のマーカーを動かす。周波数ではなく点の番号で送る（実機では右端の周波数ちょうどだと動かなかった。
        点の番号なら両端でも動く。一時停止中に送っても MAX HOLD は消えない。2026-11-01 v1.4-266 で確認）。
        置いたマーカーは本体でも絶対値（周波数・dBm）で出す: 本体は新しいマーカーを M1 との差（Δ）で出すことがあるので
        delta off も送る（ユーザーの希望。ファームウェアの cmd_marker: marker {n} delta off）。"""
        with self.marker_lock:
            req, self.marker_req = self.marker_req, {}
        for n, (index, seq) in sorted(req.items()):
            self.marker_seq = max(self.marker_seq, seq)
            try:
                if index is None:
                    self.dev.cmd(f"marker {n} off")
                else:
                    self.dev.cmd(f"marker {n} {index}")
                    self.dev.cmd(f"marker {n} delta off")
            except Exception as e:
                import traceback
                log("marker\n" + traceback.format_exc())
                self.q.put(("note", f"Could not move marker M{n} on the tinySA: {e}"))

    def _capture_if_requested(self):
        """頼まれていれば本体の画面を取り込んで PNG に保存する。"""
        if not self.capture_ev.is_set():
            return
        self.capture_ev.clear()
        try:
            img = self.dev.capture_image()
            path = capture_path("tinySA_screen")
            img.save(path)
            self.q.put(("saved", path))
        except Exception as e:
            import traceback
            log("capture\n" + traceback.format_exc())
            self.q.put(("note", f"Could not capture the device screen: {e}"))

    def _run_waitscan(self):
        """1) waitscan で掃引を 1 回最後まで行う（終わると本体は一時停止する。一時停止中に送っても 1 回掃引する）
        2) 一時停止中にデータと設定を読む（掃引を邪魔しない）→ 1) に戻る。
        resume は送らない: resume を送ると本体の MAX HOLD が消えてしまう（2026-11-01 v1.4-266 の実機で確認）。
        resume は切断するときだけ送る。本体の掃引はほぼ休みなく続き、毎回最後まで終わる。"""
        while not self.stop_ev.is_set():
            if self._paused():
                continue
            try:
                self.dev.wait_scan(timeout=max(self.sweeptime * 4, 5.0) + 10)
                y = self.dev.get_data(2)
                first, changed = self._read_settings()
                self.errors = 0
                # 範囲が変わった直後の波形は古い範囲のものかもしれないので描かない
                if len(y) == len(self.freqs) and (first or not changed):
                    self.q.put(("data", self.freqs, y, self.ylim, self.sweeptime, None, self._info()))
            except Exception as e:
                self._error(e)
                continue
            self._between_sweeps()

    def _run_timed(self):
        """古いファームウェア用。命令は 1 回分をまとめて送り、掃引が終わるころまで待ってから次を読む。
        実際の掃引は本体の答える sweeptime より長い（実機で約 1.36 倍）ので、待つ倍率は読んだデータから自動で合わせる:
        前回読んだ後に新しくなった点が右端まで届いていなければ、掃引の途中で読んだと分かるので倍率を上げる。"""
        factor = START_FACTOR
        prev = None    # 前回読んだ波形
        t_last = None  # 前回の最後の命令を送り終えた時刻（そこから本体の掃引が始まる）
        while not self.stop_ev.is_set():
            if self._paused():  # 古いファームウェアでは表示だけ止まる（本体は掃引を続ける）
                prev = None
                continue
            try:
                t_cmd = time.time()
                y = self.dev.get_data(2)  # 前回の命令の後、本体が測り終えた掃引
                first, changed = self._read_settings()
                self.errors = 0
                if len(y) != len(self.freqs) or (changed and not first):
                    prev = None
                else:
                    if prev is not None and len(prev) == len(y) and t_last is not None:
                        factor = self._adjust(factor, prev, y, t_cmd - t_last, self.sweeptime)
                    prev = y
                    self.q.put(("data", self.freqs, y, self.ylim, self.sweeptime, factor, self._info()))
                # 波形を渡した後に設定を送る（"sent" より前の波形は変更前のものとして画面が捨てるため）
                self._between_sweeps()
                t_last = time.time()
                self.stop_ev.wait(max(self.sweeptime * factor, MIN_INTERVAL))
            except Exception as e:
                self._error(e)

    def _paused(self):
        """Pause 中なら、掃引は読まずにマーカー・設定・画面の取り込みだけ行って少し待ち、True を返す。
        resume 命令は送らない（送ると本体の MAX HOLD が消える）。"""
        if not self.pause_ev.is_set():
            return False
        try:
            self._between_sweeps()
            self.errors = 0
        except Exception as e:
            self._error(e)
        self.stop_ev.wait(0.3)
        return True

    def _error(self, e):
        import traceback
        log("worker\n" + traceback.format_exc())
        self.errors += 1
        msg = f"{type(e).__name__}: {e}"
        if self.errors >= 3:
            msg = ("The tinySA is not responding. Click Disconnect, unplug the USB cable, "
                   "turn the tinySA off and on, then connect again (" + msg + ")")
        self.q.put(("error", msg))
        self.stop_ev.wait(1.0)

    @staticmethod
    def _adjust(factor, prev, y, elapsed, sweeptime):
        """前回からの変化で、掃引が最後まで終わっていたかを見て、待つ倍率を直す。"""
        ch = np.nonzero(prev != y)[0]
        if ch.size == 0:  # 何も変わっていない（本体が一時停止中など）→ 判断しない
            return factor
        last = int(ch[-1])
        if last < len(y) - 1 - TAIL_TOL:  # 右端まで届く前に読んでしまった → 実際の掃引時間を見積もって延ばす
            need = elapsed * len(y) / (last + 1)
            return min(max(factor, need / max(sweeptime, 1e-3) * 1.1), MAX_FACTOR)
        return max(factor * 0.99, MIN_FACTOR)  # 間に合っていれば少しずつ縮める


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("tinySA Simple Viewer")
        self.geometry("1000x680")
        try:
            self.iconbitmap(os.path.join(RES_DIR, "icon.ico"))
        except tk.TclError:
            pass  # アイコンが無くても動かす
        self.dev = None
        self.worker = None
        self.q = queue.Queue()
        self.freqs = None
        self.y = None
        self.sweeptime = 0.5
        self.factor = START_FACTOR
        self.markers = [None] * MARKER_COUNT  # 各マーカーの周波数 Hz（None は未設置）
        self.active = 0                      # 操作中のマーカー（0 = M1）
        self.rbw = None          # 本体の実際の RBW [kHz]
        self.rbw_choice = None   # 設定ダイアログで最後に選んだ RBW の表示（"AUTO" など。本体からは AUTO か読めないため）
        self.freq_mode = "startstop"  # 周波数範囲の表し方（"center" = 中心+Span、"startstop" = Start+Stop）
        self.pending = 0         # 送った設定がまだ本体に届いていない数（その間に届いた波形は変更前なので描かない）
        self.mk_seq = 0          # アプリで動かしたマーカーの通し番号（本体へ送り終えるまで本体の一覧で上書きしない）
        self.atten = None        # 本体の内部アッテネーター [dB]
        self.atten_choice = None  # 設定ダイアログで最後に選んだ Attenuation（"AUTO" なら AUTO。本体からは AUTO か読めない）
        self.model = ""          # つないでいる機種（External gain を機種ごとに覚えるため）
        self.ext_gain = None     # この機種に最後に設定した External gain [dB]（None は未設定 = 本体の値を変えない）
        self.paused = False
        self.draws = 0           # 描いた回数（動作確認用）
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(30, self._poll)

    # ---------- 画面 ----------
    def _build(self):
        top = ttk.Frame(self, padding=4)
        top.pack(fill="x")
        ttk.Label(top, text="Port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(top, textvariable=self.port_var, width=40, state="readonly")
        self.port_cb.pack(side="left", padx=2)
        ttk.Button(top, text="Refresh", command=self._refresh_ports).pack(side="left", padx=2)
        self.conn_btn = ttk.Button(top, text="Connect", command=self._toggle_connect)
        self.conn_btn.pack(side="left", padx=8)
        ttk.Button(top, text="Save graph", command=self._save_graph).pack(side="right", padx=2)
        self.cap_btn = ttk.Button(top, text="Save device screen", command=self._request_capture, state="disabled")
        self.cap_btn.pack(side="right", padx=2)
        # つないでいる機器の型番・ハードの版・ファームウェアの版
        self.ident_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.ident_var, foreground="#06c").pack(side="left", padx=8)

        # 本体の設定（つないでいる間だけ使える）と、占有周波数帯幅の目安線
        ctl = ttk.Frame(self, padding=(4, 0, 4, 4))
        ctl.pack(fill="x")
        self.set_btn = ttk.Button(ctl, text="Settings...", command=self._open_settings, state="disabled")
        self.set_btn.pack(side="left", padx=2)
        self.pause_btn = ttk.Button(ctl, text="Pause", width=8, command=self._toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=2)
        self.maxhold_var = tk.BooleanVar(value=False)
        self.maxhold_cb = ttk.Checkbutton(ctl, text="Max hold", variable=self.maxhold_var,
                                          command=self._toggle_maxhold, state="disabled")
        self.maxhold_cb.pack(side="left", padx=(12, 2))
        ttk.Label(ctl, text="Bandwidth lines:").pack(side="left", padx=(16, 2))
        self.bw_var = tk.StringVar(value=BW_LINE_CHOICES[0])
        bw_cb = ttk.Combobox(ctl, textvariable=self.bw_var, values=BW_LINE_CHOICES, width=8, state="readonly")
        bw_cb.pack(side="left")
        bw_cb.bind("<<ComboboxSelected>>", lambda _e: self._update_bw_lines(redraw=True))
        ttk.Label(ctl, text="kHz (center ± width/2)", foreground="#555").pack(side="left", padx=2)

        mk = ttk.Frame(self, padding=(4, 0))
        mk.pack(fill="x")
        ttk.Label(mk, text="Markers:").pack(side="left")
        self.mk_btns = []
        for i in range(MARKER_COUNT):
            b = tk.Button(mk, text=f"M{i + 1}", width=4, command=lambda i=i: self._select(i))
            b.pack(side="left", padx=2)
            self.mk_btns.append(b)
        ttk.Button(mk, text="Clear selected marker", command=self._clear_marker).pack(side="left", padx=(12, 2))
        ttk.Label(mk, text="  Click: place  /  ← →: move 1 point (Shift: 10)  /  1-4: select  /  Delete: clear",
                  foreground="#555").pack(side="left")

        self.fig = Figure(figsize=(9, 5.5), dpi=100, facecolor="#1e1e1e")
        self.ax = self.fig.add_subplot(111, facecolor="black")
        self.ax.tick_params(colors="#ccc")
        self.ax.grid(True, color="#444")
        self.ax.set_xlabel("Frequency [MHz]", color="#ccc")
        self.ax.set_ylabel("Level [dBm]", color="#ccc")
        # 横軸はいつも周波数そのもの（MHz）で書く（matplotlib の「+1.44e2」のようなまとめ表示はしない）
        self.ax.xaxis.set_major_formatter(FuncFormatter(self._fmt_freq))
        self.line, = self.ax.plot([], [], color="#ffd400", lw=1)
        self.mk_dots = [self.ax.plot([], [], marker="v", ms=11, color=c, ls="none")[0] for c in MARKER_COLORS]
        self.mk_labels = [self.ax.text(0, 0, "", color=c, fontsize=10, ha="center", va="bottom",
                                       visible=False, clip_on=True) for c in MARKER_COLORS]
        # 占有周波数帯幅の目安線（縦の点線 2 本）
        self.bw_lines = [self.ax.axvline(0, color="#e0e0e0", ls=(0, (2, 3)), lw=1, visible=False) for _ in range(2)]
        # 右上の測定条件（周波数範囲・RBW・日時）。保存するグラフにもそのまま入る
        self.cond_text = self.ax.text(0.99, 0.98, "", transform=self.ax.transAxes, ha="right", va="top",
                                      multialignment="left", color="#ddd", fontsize=9, family="monospace", zorder=5,
                                      bbox=dict(facecolor="black", edgecolor="#555", alpha=0.75))
        self.fig.subplots_adjust(left=0.08, right=0.98, top=0.97, bottom=0.1)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("button_press_event", self._on_click)

        # マーカーの値の表示（1 行に 1 つ）
        mv = tk.Frame(self, bg="#1e1e1e")
        mv.pack(fill="x")
        self.mk_vals = []
        for i, c in enumerate(MARKER_COLORS):
            lb = tk.Label(mv, text="", fg=c, bg="#1e1e1e", font=("Consolas", 12), anchor="w")
            lb.grid(row=i // 2, column=i % 2, sticky="w", padx=10)
            self.mk_vals.append(lb)
        mv.columnconfigure(0, weight=1)
        mv.columnconfigure(1, weight=1)

        # 下の欄: 左は表示中の範囲など、右は保存の結果
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.status = tk.StringVar(value="Select the port and click Connect")
        ttk.Label(bar, textvariable=self.status, relief="sunken", anchor="w", padding=3).pack(
            side="left", fill="x", expand=True)
        self.note = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.note, relief="sunken", anchor="w", padding=3,
                  foreground="#06c").pack(side="right")

        for k in ("<Left>", "<Right>", "<Shift-Left>", "<Shift-Right>", "<Delete>"):
            self.bind_all(k, self._on_key)
        for i in range(MARKER_COUNT):
            self.bind_all(str(i + 1), lambda _e, i=i: None if self._typing() else self._select(i))
        self._refresh_ports()
        self._select(0)

    def _refresh_ports(self):
        ports = list_ports()
        self.port_cb["values"] = ports
        if self.port_var.get() not in ports:
            # tinySA らしいポートがあればそれを、無ければ先頭を選ぶ
            pick = next((p for p in ports if "tinysa" in p.lower() or "stm" in p.lower()), ports[0])
            self.port_var.set(pick)

    # ---------- 接続 ----------
    def _toggle_connect(self):
        if self.dev:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        sel = self.port_var.get()
        try:
            self.dev = SimulatedTinySA() if sel == DUMMY else TinySA(sel.split()[0])
        except Exception as e:
            self.dev = None
            messagebox.showerror("Connection error", f"Could not connect to {sel}.\n{e}")
            return
        self.dev.on_timeout = lambda c, n, t: log(f"合図なし: {c}  受信 {n} バイト  最後: {t!r}")
        self.worker = Worker(self.dev, self.q)
        self.worker.start()
        self.conn_btn.config(text="Disconnect")
        self.cap_btn.config(state="normal")
        self.set_btn.config(state="normal")
        self.pause_btn.config(state="normal", text="Pause")
        self.maxhold_cb.config(state="normal")
        self.pending = 0
        self.mk_seq = 0
        self.paused = False
        model, hw, fw = self.dev.get_identity()
        self.model = model
        self.ext_gain = load_settings().get("ext_gain", {}).get(model or "unknown")
        self.ident_var.set(f"Model: {model or 'unknown'}   HW: {hw or 'unknown'}   Firmware: {fw or 'unknown'}")
        self.status.set(f"Connected: {model or sel}")

    def _disconnect(self):
        if self.worker:
            self.worker.stop_ev.set()
            self.worker.join(timeout=3)
            self.worker = None
        if self.dev:
            self.dev.close()
            self.dev = None
        self.conn_btn.config(text="Connect")
        self.cap_btn.config(state="disabled")
        self.set_btn.config(state="disabled")
        self.pause_btn.config(state="disabled", text="Pause")
        self.paused = False
        self.maxhold_cb.config(state="disabled")
        self.ident_var.set("")
        self.status.set("Disconnected")

    def _on_close(self):
        self._disconnect()
        self.destroy()

    # ---------- 本体の設定 ----------
    def _rbw_choices(self):
        return RBW_CHOICES["ultra" if self.dev and self.dev.is_ultra else "basic"]

    def _send_settings(self, cmds, note):
        """設定の命令を Worker に頼み、グラフを消して変更後の掃引を待つ。"""
        if not self.worker:
            return
        if self.paused:  # 止めたままだとグラフが消えたままになるので、設定を送ったら再開する
            self._toggle_pause()
        self.pending += 1
        self.worker.send(cmds)
        self.y = None
        self.line.set_data([], [])
        self._update_markers()
        self.note.set(note)

    def _toggle_maxhold(self):
        """MAX HOLD を本体に設定する。オンのときは一度 off にしてから入れ、保持中の最大値を消してやり直す。"""
        on = self.maxhold_var.get()
        self._send_settings(["calc off", "calc maxh"] if on else ["calc off"],
                            "Max hold " + ("on (restarted)" if on else "off"))

    def _open_settings(self):
        if self.dev and self.freqs is not None:
            SettingsDialog(self)
        else:
            self.note.set("Wait for the first sweep before changing the settings")

    def _toggle_pause(self):
        """Pause: 次の waitscan を送らないので、本体とグラフは今の掃引が終わったところで止まる。
        Resume: また waitscan を送る。resume 命令は使わないので、どちらでも MAX HOLD は消えない。"""
        if not self.worker:
            return
        self.paused = not self.paused
        if self.paused:
            self.worker.pause_ev.set()
            self.pause_btn.config(text="Resume")
            self.note.set("Paused after the current sweep")
        else:
            self.worker.pause_ev.clear()
            self.pause_btn.config(text="Pause")
            self.note.set("Resumed")
        if self.freqs is not None:
            self._update_status()

    def _apply_settings(self, mode, a, b, rbw_label, atten="", ext_gain=None):
        """設定ダイアログの OK。mode="center" なら a=中心 MHz・b=Span kHz、"startstop" なら a=Start MHz・b=Stop MHz。
        rbw_label は RBW の選択肢（None なら変えない）、atten は "AUTO" か 0〜30 の整数の文字（"" なら変えない）、
        ext_gain は dB（None なら変えない）。"""
        if mode == "center":
            c, s = round(a * 1e6), round(b * 1e3)
            # Span を変えたとき本体が中心をずらすことがあるので、最後にもう一度中心を送る
            cmds = [f"sweep center {c}", f"sweep span {s}", f"sweep center {c}"]
        else:
            cmds = [f"sweep {round(a * 1e6)} {round(b * 1e6)}"]
        self.freq_mode = mode
        if rbw_label is not None:  # None なら RBW は変えない
            cmds.append(f"rbw {dict(self._rbw_choices())[rbw_label]}")
            self.rbw_choice = rbw_label
        if atten:
            cmds.append("attenuate auto" if atten == "AUTO" else f"attenuate {int(atten)}")
            self.atten_choice = atten
        if ext_gain is not None:
            cmds.append(f"ext_gain {ext_gain:g}")
            self.ext_gain = ext_gain
            data = load_settings()
            data.setdefault("ext_gain", {})[self.model or "unknown"] = ext_gain
            save_settings(data)
        self._send_settings(cmds, "Settings sent to the tinySA")

    # ---------- 保存 ----------
    def _request_capture(self):
        """本体の画面の取り込みを Worker に頼む（今の掃引が終わってから取り込むので、本体もアプリも止まらない）。"""
        if not self.worker:
            return
        self.worker.capture_ev.set()
        self.note.set("Capturing the device screen after the current sweep...")

    def _save_graph(self):
        """グラフに機種・日時・マーカーの値を書き足して PNG に保存する（すぐ終わるので画面は止まらない）。"""
        if self.y is None:
            self.note.set("No data to save yet")
            return
        extra = []
        size = self.fig.get_size_inches()
        self._update_conditions()  # 測定条件の日時を保存する時刻にする
        try:
            path = capture_path("tinySA_graph")
            # 保存するときだけ決まった大きさにする（forward=False で画面のグラフの大きさは変えない）
            self.fig.set_size_inches(GRAPH_SAVE_PX[0] / 100, GRAPH_SAVE_PX[1] / 100, forward=False)
            self.fig.subplots_adjust(bottom=0.24)
            head = f"{self.ident_var.get()}   {datetime.now():%Y-%m-%d %H:%M:%S}"
            extra.append(self.fig.text(0.01, 0.135, head.strip(), color="#ccc", fontsize=9))
            for i, c in enumerate(MARKER_COLORS):
                t = self.mk_vals[i].cget("text").replace("◀", " ")
                extra.append(self.fig.text(0.01 + 0.5 * (i % 2), 0.075 - 0.045 * (i // 2), t,
                                           color=c, fontsize=10, family="monospace"))
            self.fig.savefig(path, dpi=100, facecolor=self.fig.get_facecolor())
            self.note.set("Saved: " + path)
        except Exception as e:
            import traceback
            log("save graph\n" + traceback.format_exc())
            self.note.set(f"Could not save the graph: {e}")
        finally:
            for t in extra:
                t.remove()
            self.fig.set_size_inches(size, forward=False)
            self.fig.subplots_adjust(bottom=0.1)
            self.canvas.draw_idle()

    # ---------- 受信と描画 ----------
    def _poll(self):
        try:
            latest = None
            while True:
                try:
                    msg = self.q.get_nowait()
                except queue.Empty:
                    break
                if msg[0] == "data":
                    if self.pending == 0:  # 設定を送った後、本体に届くまでの波形は変更前のものなので描かない
                        latest = msg  # たまっていたら最新だけ描く
                elif msg[0] == "sent":
                    self.pending = max(self.pending - 1, 0)
                    latest = None
                    if msg[1]:
                        self.note.set(msg[1])
                elif msg[0] == "mode" and not msg[1]:
                    self.status.set("Old firmware: the update timing is estimated from the sweep time "
                                    "(update the firmware to follow the end of each sweep)")
                elif msg[0] == "error" and self.dev:
                    self.status.set("Communication error: " + msg[1])
                elif msg[0] == "markers":
                    self._apply_device_markers(msg[1], msg[2])
                elif msg[0] == "saved":
                    self.note.set("Saved: " + msg[1])
                elif msg[0] == "note":
                    self.note.set(msg[1])
            if latest:
                _k, f, y, ylim, self.sweeptime, self.factor, info = latest
                self.rbw, self.atten = info["rbw"], info["atten"]
                if info["calc"]:  # 本体で MAX HOLD を切り替えても、チェックボックスを本体に合わせる
                    self.maxhold_var.set(info["calc"] == "MAXH")
                self._draw(f, y, ylim)
        except Exception:
            import traceback
            log("draw\n" + traceback.format_exc())
        self.after(30, self._poll)

    def _draw(self, f, y, ylim):
        changed = self.freqs is None or len(self.freqs) != len(f) or self.freqs[0] != f[0] or self.freqs[-1] != f[-1]
        self.freqs, self.y = f, y
        self.draws += 1
        mhz = f / 1e6
        self.line.set_data(mhz, y)
        if changed:
            self.ax.set_xlim(mhz[0], mhz[-1] if mhz[-1] > mhz[0] else mhz[0] + 1e-6)
        if ylim:
            if self.ax.get_ylim() != ylim:
                self.ax.set_ylim(*ylim)
                # 横の目盛り線は本体の画面と同じく 1 目盛り（scale）ごと
                self.ax.set_yticks(np.linspace(ylim[0], ylim[1], GRID_DIVS + 1))
        elif np.isfinite(y).any():
            self.ax.set_ylim(np.nanmin(y) - 5, np.nanmax(y) + 10)
        self._update_bw_lines()
        self._update_conditions()
        self._update_markers()
        self.last_update = datetime.now()
        self._update_status()

    def _update_status(self):
        if not self.dev or self.freqs is None:
            return
        mhz = self.freqs / 1e6
        last = getattr(self, "last_update", None)
        self.status.set(f"{mhz[0]:.6f} - {mhz[-1]:.6f} MHz, {len(mhz)} points (tinySA settings)"
                        + ("   PAUSED (click Resume to continue)" if self.paused else
                           f"   Updated after every sweep (waitscan, about {self.sweeptime:.1f} s)"
                           if self.factor is None else
                           f"   Sweep {self.sweeptime:.1f} s, updated every {self.sweeptime * self.factor:.1f} s")
                        + (f"   (last {last:%H:%M:%S})" if last else ""))

    def _fmt_freq(self, x, _pos):
        """横軸の目盛り [MHz]。小数点以下の桁数は目盛りの間隔に合わせる（間隔 10 kHz なら 144.01 のように）。"""
        locs = self.ax.xaxis.get_majorticklocs()
        step = abs(locs[1] - locs[0]) if len(locs) > 1 else 1.0
        dec = 0
        while dec < 9 and abs(round(step, dec) - step) > step * 1e-6:
            dec += 1
        return f"{x:.{dec}f}"

    def _update_bw_lines(self, redraw=False):
        """占有周波数帯幅の目安線を、表示範囲の中心 ± 幅/2 に引く（0 なら消す）。"""
        try:
            width = float(self.bw_var.get().split()[0])
        except ValueError:
            width = 0.0
        show = width > 0 and self.freqs is not None
        if show:
            center = (self.freqs[0] + self.freqs[-1]) / 2
            for line, x in zip(self.bw_lines, (center - width * 500, center + width * 500)):
                line.set_xdata([x / 1e6, x / 1e6])
        for line in self.bw_lines:
            line.set_visible(show)
        if redraw:
            self.canvas.draw_idle()

    def _update_conditions(self):
        """右上の測定条件（周波数範囲は設定ダイアログで選んだ表し方、RBW、今の日時）を書き直す。"""
        if self.freqs is None:
            self.cond_text.set_text("")
            return
        start, stop = self.freqs[0], self.freqs[-1]
        if self.freq_mode == "center":
            rng = [f"Center {(start + stop) / 2e6:.6f} MHz", f"Span   {(stop - start) / 1e3:.3f} kHz"]
        else:
            rng = [f"Start  {start / 1e6:.6f} MHz", f"Stop   {stop / 1e6:.6f} MHz"]
        rbw = fmt_rbw(self.rbw)
        if self.rbw_choice == "AUTO" and self.rbw is not None:
            rbw = f"AUTO ({rbw})"
        att = "-" if self.atten is None else f"{self.atten:g} dB"
        if self.atten_choice == "AUTO" and self.atten is not None:
            att = f"AUTO ({att})"
        ext = "unknown" if self.ext_gain is None else f"{self.ext_gain:g} dB"  # 本体からは読めない
        self.cond_text.set_text("\n".join(rng + [f"RBW    {rbw}", f"Atten  {att}", f"ExtGain {ext}",
                                                 f"{datetime.now():%Y-%m-%d %H:%M}"]))

    # ---------- マーカー ----------
    def _index(self, hz):
        return int(np.argmin(np.abs(self.freqs - hz)))

    def _in_range(self, hz):
        return self.freqs is not None and self.freqs[0] <= hz <= self.freqs[-1]

    def _update_markers(self):
        for i in range(MARKER_COUNT):
            hz = self.markers[i]
            name = f"M{i + 1}" + ("◀" if i == self.active else "")
            if hz is None or self.y is None or not self._in_range(hz):
                self.mk_dots[i].set_data([], [])
                self.mk_labels[i].set_visible(False)
                txt = "(out of range)" if hz is not None else "-"
                self.mk_vals[i].config(text=f"{name:4} {txt}")
                continue
            j = self._index(hz)
            fx, fy = self.freqs[j] / 1e6, float(self.y[j])
            self.mk_dots[i].set_data([fx], [fy])
            self.mk_labels[i].set_position((fx, fy))
            self.mk_labels[i].set_text(f"M{i + 1}\n")
            self.mk_labels[i].set_visible(True)
            self.mk_vals[i].config(text=f"{name:4} {fx:12.6f} MHz   {fy:7.1f} dBm")
        self.canvas.draw_idle()

    def _select(self, i):
        self.active = i
        for k, b in enumerate(self.mk_btns):
            sel = k == i
            b.config(bg=MARKER_COLORS[k] if sel else "SystemButtonFace", relief="sunken" if sel else "raised")
        if self.freqs is not None:
            self._update_markers()
        else:
            for k in range(MARKER_COUNT):
                self.mk_vals[k].config(text=f"{'M' + str(k + 1) + ('◀' if k == i else ''):4} -")

    def _on_click(self, ev):
        if ev.inaxes is not self.ax or ev.xdata is None or self.freqs is None:
            return
        self.canvas.get_tk_widget().focus_set()  # 次の ← → がグラフに効くように
        self.markers[self.active] = self.freqs[self._index(ev.xdata * 1e6)]
        self._update_markers()
        self._sync_marker(self.active)

    def _typing(self):
        """ポート欄などを操作中はキーをそちらに任せる。"""
        w = self.focus_get()
        return isinstance(w, (ttk.Combobox, ttk.Entry, tk.Entry))

    def _on_key(self, ev):
        if self._typing() or self.freqs is None:
            return
        if ev.keysym == "Delete":
            self._clear_marker()
            return "break"
        hz = self.markers[self.active]
        if hz is None or not self._in_range(hz):  # まだ置いていなければ中央に置く
            j = len(self.freqs) // 2
        else:
            step = 10 if ev.state & 0x1 else 1  # Shift で 10 点ずつ
            j = self._index(hz) + (step if ev.keysym == "Right" else -step)
            j = min(max(j, 0), len(self.freqs) - 1)
        self.markers[self.active] = self.freqs[j]
        self._update_markers()
        self._sync_marker(self.active)
        return "break"

    def _clear_marker(self):
        self.markers[self.active] = None
        self._update_markers()
        self._sync_marker(self.active)

    def _sync_marker(self, i):
        """アプリのマーカーに本体のマーカー（同じ番号）を合わせる（アプリ → 本体）。"""
        if not self.worker:
            return
        hz = self.markers[i]
        self.mk_seq += 1
        self.worker.set_marker(i + 1, None if hz is None or self.freqs is None else self._index(hz), self.mk_seq)

    def _apply_device_markers(self, got, seq):
        """本体のマーカー M1〜M4 の一覧にアプリを合わせる（本体 → アプリ）。本体で動かす・増やす・消すと、アプリも同じにする。
        アプリで動かしたのにまだ本体へ送っていないもの（seq が古い）があるときは、本体の古い位置で上書きしない。"""
        if seq < self.mk_seq:
            return
        f = self.freqs
        tol = (f[1] - f[0]) / 2 if f is not None and len(f) > 1 else 1.0  # 同じ点とみなす幅
        changed = False
        for i in range(MARKER_COUNT):
            dev, cur = got.get(i + 1), self.markers[i]
            if dev is None:
                if cur is not None:
                    self.markers[i], changed = None, True
                continue
            idx, hz = dev
            if f is not None and idx < len(f) and abs(f[idx] - hz) <= tol:
                hz = f[idx]  # アプリの周波数の一覧の値にそろえる
            if cur is None or abs(cur - hz) > tol:
                self.markers[i], changed = hz, True
        if changed:
            self._update_markers()


class SettingsDialog(tk.Toplevel):
    """本体の周波数範囲と RBW を変えるダイアログ。開いたときは本体の今の設定を出す。
    OK で本体に送り、Cancel では何も変えない。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("tinySA settings")
        self.resizable(False, False)
        self.transient(app)
        start, stop = app.freqs[0], app.freqs[-1]
        self.mode = tk.StringVar(value=app.freq_mode)
        self.center = tk.StringVar(value=f"{(start + stop) / 2e6:.6f}")
        self.span = tk.StringVar(value=f"{(stop - start) / 1e3:.3f}")
        self.start = tk.StringVar(value=f"{start / 1e6:.6f}")
        self.stop = tk.StringVar(value=f"{stop / 1e6:.6f}")

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Radiobutton(f, text="Center / Span", variable=self.mode, value="center",
                        command=self._mode_changed).grid(row=0, column=0, columnspan=3, sticky="w")
        self.c_entries = self._row(f, 1, "Center", self.center, "MHz") + self._row(f, 2, "Span", self.span, "kHz")
        ttk.Radiobutton(f, text="Start / Stop", variable=self.mode, value="startstop",
                        command=self._mode_changed).grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.s_entries = self._row(f, 4, "Start", self.start, "MHz") + self._row(f, 5, "Stop", self.stop, "MHz")

        ttk.Label(f, text="RBW").grid(row=6, column=0, sticky="w", padx=(18, 6), pady=(12, 0))
        labels = [lb for lb, _v in app._rbw_choices()]
        self.rbw = tk.StringVar(value=self._current_rbw(labels))
        ttk.Combobox(f, textvariable=self.rbw, values=labels, width=8, state="readonly").grid(
            row=6, column=1, sticky="w", pady=(12, 0))
        ttk.Label(f, text="Hz", foreground="#555").grid(row=6, column=2, sticky="w", pady=(12, 0))

        # 内部アッテネーター: 一覧（AUTO と 5 dB 刻み）から選ぶか、0〜30 の整数を直接入れる
        ttk.Label(f, text="Attenuation").grid(row=7, column=0, sticky="w", padx=(18, 6), pady=(8, 0))
        self.atten = tk.StringVar(value=self._current_atten())
        ttk.Combobox(f, textvariable=self.atten, values=ATTEN_CHOICES, width=8).grid(
            row=7, column=1, sticky="w", pady=(8, 0))
        ttk.Label(f, text=f"dB (AUTO or 0-{ATTEN_MAX})", foreground="#555").grid(
            row=7, column=2, sticky="w", padx=4, pady=(8, 0))
        # External gain: 本体から読めないので、前にこのアプリで設定した値を出す（空欄なら本体の値を変えない）
        ttk.Label(f, text="External gain").grid(row=8, column=0, sticky="w", padx=(18, 6), pady=(4, 0))
        eg = app.ext_gain
        self.ext = tk.StringVar(value="" if eg is None else f"{eg:g}")
        ttk.Entry(f, textvariable=self.ext, width=10, justify="right").grid(row=8, column=1, sticky="w", pady=(4, 0))
        ttk.Label(f, text="dB (-100 to 100, blank = keep)", foreground="#555").grid(
            row=8, column=2, sticky="w", padx=4, pady=(4, 0))

        self.init_rbw, self.init_atten = self.rbw.get(), self.atten.get()

        btns = ttk.Frame(f)
        btns.grid(row=9, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="OK", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.cancel).pack(side="left")
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._mode_changed()
        self.grab_set()

    @staticmethod
    def _row(f, row, name, var, unit):
        ttk.Label(f, text=name).grid(row=row, column=0, sticky="w", padx=(18, 6), pady=2)
        e = ttk.Entry(f, textvariable=var, width=16, justify="right")
        e.grid(row=row, column=1, sticky="w", pady=2)
        ttk.Label(f, text=unit).grid(row=row, column=2, sticky="w", padx=4)
        return [e]

    def _current_rbw(self, labels):
        """本体の今の RBW に合う選択肢。本体からは AUTO かどうか読めないので、前にこのダイアログで
        AUTO を選んでいれば AUTO、そうでなければ実際の RBW と同じ値、無ければ AUTO にする。"""
        if self.app.rbw_choice == "AUTO":
            return "AUTO"
        khz = self.app.rbw
        for lb, v in self.app._rbw_choices():
            if v != "auto" and khz is not None and abs(float(v) - khz) < 1e-6:
                return lb
        return "AUTO"

    def _current_atten(self):
        """本体の今のアッテネーター。前にこのダイアログで AUTO を選んでいれば AUTO（本体からは AUTO か読めない）。"""
        if self.app.atten_choice == "AUTO" or self.app.atten is None:
            return "AUTO"
        return f"{self.app.atten:g}"

    def _mode_changed(self):
        center = self.mode.get() == "center"
        for e in self.c_entries:
            e.config(state="normal" if center else "disabled")
        for e in self.s_entries:
            e.config(state="disabled" if center else "normal")

    def _ok(self):
        mode = self.mode.get()
        try:
            if mode == "center":
                a, b = float(self.center.get()), float(self.span.get())
                if a <= 0 or b <= 0:
                    raise ValueError("Center and Span must be greater than 0.")
            else:
                a, b = float(self.start.get()), float(self.stop.get())
                if a < 0 or b <= a:
                    raise ValueError("Stop must be greater than Start.")
        except ValueError as e:
            msg = str(e) if "must" in str(e) else "Enter the frequencies as numbers."
            messagebox.showerror("Settings", msg, parent=self)
            return
        att = self.atten.get().strip().upper()
        if att != "AUTO" and not (att.isdigit() and 0 <= int(att) <= ATTEN_MAX):
            messagebox.showerror("Settings", f"Attenuation must be AUTO or a whole number from 0 to {ATTEN_MAX}.",
                                 parent=self)
            return
        ext = self.ext.get().strip()
        try:
            ext_gain = None if ext == "" else float(ext)
            if ext_gain is not None and not (EXT_GAIN_RANGE[0] <= ext_gain <= EXT_GAIN_RANGE[1]):
                raise ValueError
        except ValueError:
            messagebox.showerror("Settings", "External gain must be a number from -100 to 100 (or blank).",
                                 parent=self)
            return
        att = att if att == "AUTO" else str(int(att))
        # RBW とアッテネーターは変えたときだけ送る（本体が AUTO のとき、開いたときの実際の値を送ると AUTO が外れるため）
        rbw = self.rbw.get()
        self.app._apply_settings(mode, a, b, None if rbw == self.init_rbw else rbw,
                                 "" if att == self.init_atten else att, ext_gain)
        self.destroy()

    def cancel(self):
        self.destroy()


if __name__ == "__main__":
    app = App()
    if "--selftest" in sys.argv:  # 動作確認用: ダミーにつなぎ、マーカー（両方向）・保存・設定・MAX HOLD・目安線・Pause を試して終了
        import tempfile
        SETTINGS_FILE = os.path.join(tempfile.gettempdir(), "tinysa_simple_selftest_settings.json")  # 本物の設定を汚さない
        saved, res = [], {}

        def check():
            ok = app.freqs is not None and app.y is not None
            res["first data"] = ok
            if ok:
                app.markers[0] = app.freqs[100]
                app._update_markers()
                app._sync_marker(0)
                app._save_graph()
                saved.append(app.note.get())
                app._request_capture()
                SettingsDialog(app).cancel()  # ダイアログが作れるか
                app._apply_settings("center", 145.0, 1000.0, "10k", "10", -50.5)
                app.maxhold_var.set(True)
                app._toggle_maxhold()
                app.bw_var.set("6")
            app.after(3500, stage2)

        def stage2():
            d = app.dev
            cond = app.cond_text.get_text()
            lines = [ln.get_xdata()[0] for ln in app.bw_lines if ln.get_visible()]
            app.canvas.draw()
            ticks = [t.get_text() for t in app.ax.get_xticklabels() if t.get_text()]
            res.update({
                "marker app->dev": d.set_markers.get(1) == 100 and d.marker_delta.get(1) is False,
                "save graph": bool(saved) and saved[0].startswith("Saved:"),
                "sweep": (d.start, d.stop) == (144.5e6, 145.5e6),
                "rbw": d.rbw_khz == 10 and "RBW    10 kHz" in cond,
                "atten/ext": d.atten == 10 and d.ext_gain == -50.5 and "Atten  10 dB" in cond
                             and "ExtGain -50.5 dB" in cond and load_settings()["ext_gain"],
                "maxhold": d.calc == "maxh" and app.maxhold_var.get(),
                "drawn": app.pending == 0 and app.y is not None and "Center 145.000000 MHz" in cond,
                "bw lines": len(lines) == 2 and abs(lines[0] - 144.997) < 1e-9 and abs(lines[1] - 145.003) < 1e-9,
                "x axis": bool(ticks) and all(144.0 < float(t) < 146.0 for t in ticks)
                          and app.ax.xaxis.get_offset_text().get_text() == "",
            })
            # 本体で M1 を消し、M2 を 50 点目に置いたことにする。Pause 中でも本体のマーカーはアプリに届く
            d.set_markers[1], d.set_markers[2] = None, 50
            app._toggle_pause()
            app.after(2000, stage3)

        def stage3():
            res["marker dev->app"] = app.markers[0] is None and app.markers[1] == app.freqs[50]
            res["paused"] = app.paused and "PAUSED" in app.status.get()
            res["draws"] = app.draws
            app.after(1500, stage4)

        def stage4():
            res["no draw while paused"] = app.draws == res.pop("draws")
            res["draws"] = app.draws
            app._toggle_pause()
            app.after(2000, finish)

        def finish():
            res["drawn after resume"] = app.draws > res.pop("draws") and not app.paused
            files = [os.path.join(CAPTURE_DIR, n) for n in os.listdir(CAPTURE_DIR)] if os.path.isdir(CAPTURE_DIR) else []
            new = [p for p in files if os.path.getmtime(p) > t0]
            res["files"] = any("tinySA_graph" in p for p in new) and any("tinySA_screen" in p for p in new)
            bad = [k for k, v in res.items() if not v]
            if bad:
                print("selftest failed:", ", ".join(bad), "| cond:", app.cond_text.get_text().replace("\n", " / "),
                      flush=True)
            for p in new + [SETTINGS_FILE]:  # 動作確認で作ったファイルは消す
                if os.path.exists(p):
                    os.remove(p)
            app._on_close()
            os._exit(1 if bad else 0)

        t0 = time.time() - 1
        app.port_var.set(DUMMY)
        app._toggle_connect()
        app.after(2500, check)
    app.mainloop()
