"""tinySA シンプル ビューア（画面の文字は英語。英語圏の人にも使ってもらうため）

起動:  python tinysa_simple.py
  - COM ポートを選んで「Connect」→ tinySA の掃引をそのままパソコンに表示する
    （表示する範囲は本体の設定に合わせる。パソコンからは周波数を変えない）
  - マーカー M1〜M4: ボタンかキー 1〜4 で選び、グラフをクリックで置く、← → で 1 点ずつ動かす
  - 「Save device screen」で本体の画面を、「Save graph」でグラフを PNG に保存する（captures フォルダ）
  - ポート一覧の「Dummy (no device)」で、実機なしでも動作確認できる
"""

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

from tinysa_device import SimulatedTinySA, TinySA

matplotlib.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

DUMMY = "Dummy (no device)"
GRID_DIVS = 8          # 本体画面の縦の目盛り数（基準レベルから下へ scale×8）
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
        self.freqs = None
        self.ylim = None
        self.sweeptime = 0.5
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
        return first, changed

    def set_marker(self, n, index):
        """画面のスレッドから呼ぶ。本体のマーカー n を index 点目へ（None なら消す）。次の掃引の合間に送る。"""
        with self.marker_lock:
            self.marker_req[n] = index

    def _between_sweeps(self):
        """データを読み終えた後（waitscan のときは本体が一時停止中なので、掃引を邪魔しない）に、
        頼まれていたマーカーの移動と画面の取り込みを行う。"""
        self._send_markers()
        self._capture_if_requested()  # マーカーを動かした後の画面を取り込む

    def _send_markers(self):
        """本体のマーカーを動かす。周波数ではなく点の番号で送る（実機では右端の周波数ちょうどだと動かなかった。
        点の番号なら両端でも動く。一時停止中に送っても MAX HOLD は消えない。2026-11-01 v1.4-266 で確認）。"""
        with self.marker_lock:
            req, self.marker_req = self.marker_req, {}
        for n, index in sorted(req.items()):
            try:
                self.dev.cmd(f"marker {n} off" if index is None else f"marker {n} {index}")
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
            try:
                self.dev.wait_scan(timeout=max(self.sweeptime * 4, 5.0) + 10)
                y = self.dev.get_data(2)
                first, changed = self._read_settings()
                self.errors = 0
                # 範囲が変わった直後の波形は古い範囲のものかもしれないので描かない
                if len(y) == len(self.freqs) and (first or not changed):
                    self.q.put(("data", self.freqs, y, self.ylim, self.sweeptime, None))
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
            try:
                t_cmd = time.time()
                y = self.dev.get_data(2)  # 前回の命令の後、本体が測り終えた掃引
                first, changed = self._read_settings()
                self._between_sweeps()
                t_done = time.time()
                self.errors = 0
                if len(y) != len(self.freqs) or (changed and not first):
                    prev = None
                else:
                    if prev is not None and len(prev) == len(y) and t_last is not None:
                        factor = self._adjust(factor, prev, y, t_cmd - t_last, self.sweeptime)
                    prev = y
                    self.q.put(("data", self.freqs, y, self.ylim, self.sweeptime, factor))
                t_last = t_done
                self.stop_ev.wait(max(self.sweeptime * factor, MIN_INTERVAL))
            except Exception as e:
                self._error(e)

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
        self.line, = self.ax.plot([], [], color="#ffd400", lw=1)
        self.mk_dots = [self.ax.plot([], [], marker="v", ms=11, color=c, ls="none")[0] for c in MARKER_COLORS]
        self.mk_labels = [self.ax.text(0, 0, "", color=c, fontsize=10, ha="center", va="bottom",
                                       visible=False, clip_on=True) for c in MARKER_COLORS]
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
        model, hw, fw = self.dev.get_identity()
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
        self.ident_var.set("")
        self.status.set("Disconnected")

    def _on_close(self):
        self._disconnect()
        self.destroy()

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
                    latest = msg  # たまっていたら最新だけ描く
                elif msg[0] == "mode" and not msg[1]:
                    self.status.set("Old firmware: the update timing is estimated from the sweep time "
                                    "(update the firmware to follow the end of each sweep)")
                elif msg[0] == "error" and self.dev:
                    self.status.set("Communication error: " + msg[1])
                elif msg[0] == "saved":
                    self.note.set("Saved: " + msg[1])
                elif msg[0] == "note":
                    self.note.set(msg[1])
            if latest:
                _k, f, y, ylim, self.sweeptime, self.factor = latest
                self._draw(f, y, ylim)
        except Exception:
            import traceback
            log("draw\n" + traceback.format_exc())
        self.after(30, self._poll)

    def _draw(self, f, y, ylim):
        changed = self.freqs is None or len(self.freqs) != len(f) or self.freqs[0] != f[0] or self.freqs[-1] != f[-1]
        self.freqs, self.y = f, y
        mhz = f / 1e6
        self.line.set_data(mhz, y)
        if changed:
            self.ax.set_xlim(mhz[0], mhz[-1] if mhz[-1] > mhz[0] else mhz[0] + 1e-6)
        if ylim:
            if self.ax.get_ylim() != ylim:
                self.ax.set_ylim(*ylim)
        elif np.isfinite(y).any():
            self.ax.set_ylim(np.nanmin(y) - 5, np.nanmax(y) + 10)
        self._update_markers()
        if self.dev:
            self.status.set(f"{mhz[0]:.6f} - {mhz[-1]:.6f} MHz, {len(f)} points (tinySA settings)"
                            + (f"   Updated after every sweep (waitscan, about {self.sweeptime:.1f} s)"
                               if self.factor is None else
                               f"   Sweep {self.sweeptime:.1f} s, updated every {self.sweeptime * self.factor:.1f} s")
                            + f"   (last {datetime.now():%H:%M:%S})")

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
        """アプリのマーカーに本体のマーカー（同じ番号）を合わせる（アプリ → 本体の一方向）。"""
        if not self.worker:
            return
        hz = self.markers[i]
        self.worker.set_marker(i + 1, None if hz is None or self.freqs is None else self._index(hz))


if __name__ == "__main__":
    app = App()
    if "--selftest" in sys.argv:  # 動作確認用: ダミーにつなぎ、マーカーを置き、2 種類の保存を試して終了
        saved = []

        def check():
            ok = app.freqs is not None and app.y is not None
            if ok:
                app.markers[0] = app.freqs[100]
                app._update_markers()
                app._sync_marker(0)
                ok = len(app.mk_dots[0].get_xdata()) == 1
                app._save_graph()
                saved.append(app.note.get())
                app._request_capture()
            app.after(2500, lambda: finish(ok))

        def finish(ok):
            files = [os.path.join(CAPTURE_DIR, n) for n in os.listdir(CAPTURE_DIR)] if os.path.isdir(CAPTURE_DIR) else []
            new = [p for p in files if os.path.getmtime(p) > t0]
            ok = ok and app.dev.set_markers.get(1) == 100 \
                and saved and saved[0].startswith("Saved:") \
                and any("tinySA_graph" in p for p in new) and any("tinySA_screen" in p for p in new)
            for p in new:  # 動作確認で作ったファイルは消す
                os.remove(p)
            app._on_close()
            os._exit(0 if ok else 1)

        t0 = time.time() - 1
        app.port_var.set(DUMMY)
        app._toggle_connect()
        app.after(2500, check)
    app.mainloop()
