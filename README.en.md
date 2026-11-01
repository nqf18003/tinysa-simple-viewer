# tinySA Simple Viewer

[日本語版はこちら (Japanese)](README.md)

### [⬇ Download the latest exe](https://github.com/nqf18003/tinysa-simple-viewer/releases/latest)
On that page, click `tinySA_Simple.exe` under **Assets**.

A minimal Windows viewer for the **tinySA Ultra / Ultra+** spectrum analyzer.
Connect the tinySA by USB and the PC shows the same sweep as the tinySA screen, with up to four markers.

- The frequency range and the vertical scale follow the tinySA settings. The PC never changes the frequency.
- Markers M1-M4 can be placed with the mouse and moved with the arrow keys. The tinySA markers move with them.
- The tinySA screen and the graph can be saved as PNG images.

> **Important: use the latest firmware. Without it, this viewer may not work.**
> Please update the tinySA to the latest firmware before use.
>
> **Tested with:** tinySA Ultra+ **ZS406** and **ZS407**, firmware v1.4-266. Other models have not been tested.
>
> This viewer works best with firmware **v1.4-216 or later**. Newer firmware has the
> `waitscan` command, which lets the viewer read each sweep after it has finished, so every sweep on the tinySA runs to the end.
> With older firmware the viewer has to guess the timing. Sweeps may then be cut short, and the tinySA may sometimes stop responding.
> Firmware and update instructions: https://tinysa.org/wiki/

## Requirements
- Windows 10 / 11
- tinySA Ultra or Ultra+ (tested with ZS406 and ZS407)
- A USB cable (data capable)

## Getting started
### Using the exe
1. Download `tinySA_Simple.exe` from the [latest release page](https://github.com/nqf18003/tinysa-simple-viewer/releases/latest).
2. Double-click it. No installation is needed.
   - The first time, Windows may show **"Windows protected your PC"** (SmartScreen), because the exe is not digitally signed.
     Click **More info**, then **Run anyway**.

### Using Python
1. Install Python 3.12 or later.
2. In this folder, run:
   ```
   python -m pip install -r requirements.txt
   python tinysa_simple.py
   ```

## How to use
1. Select the tinySA's COM port under **Port** and click **Connect**.
   - Click **Refresh** if the port is not in the list.
   - **Dummy (no device)** lets you try the viewer without a tinySA.
2. The graph shows the tinySA sweep and is updated after every sweep.
   - To change the range or the reference level, change it on the tinySA. The PC follows within about one sweep.
   - The top bar shows the model, hardware version and firmware version.
   - Slow sweep settings (wide span, narrow RBW) make the PC update slowly too.
3. **Markers (up to 4)**

   | Action | How |
   |---|---|
   | Select a marker | **M1-M4** buttons or keys **1-4** |
   | Place it | Click on the graph |
   | Move 1 point / 10 points | **← →** / **Shift + ← →** |
   | Clear it | **Delete** key or **Clear selected marker** |

   - The frequency and level of each marker are shown below the graph.
   - **The tinySA markers follow the viewer** (viewer → tinySA only). When you place, move or clear M1-M4 in the viewer,
     the tinySA marker with the same number does the same after the current sweep. Moving a marker on the tinySA does not
     move it in the viewer.
4. **Saving images.** Files go to the `captures` folder next to the exe (or next to `tinysa_simple.py`), named with the date and time.
   - **Save device screen** saves the tinySA screen (480 × 320). The screen is read between sweeps, so neither the tinySA nor the viewer stops.
   - **Save graph** saves the graph with the model, date/time and marker values. The image is always 1200 × 800 pixels, whatever the window size.
   - The bottom right of the window shows where the file was saved.
5. Click **Disconnect** or close the window when you are done.

## Notes
- **MAX HOLD on the tinySA works while connected.** Clicking **Disconnect** returns the tinySA to normal sweeping,
  and this clears MAX HOLD.
- While connected, the tinySA may show **PAUSED** between sweeps. This is normal; the sweep continues.
- The tinySA restarts its sweep whenever it receives a command. The viewer only sends commands between sweeps.
- If the viewer says **"The tinySA is not responding"**: click Disconnect, unplug the USB cable, turn the tinySA off and on, then connect again.

## Data and privacy
- The viewer talks to the tinySA over USB only. Nothing is sent to the internet.
- Saved images stay in the `captures` folder on your PC.
- If a communication problem happens, a short record is written to `error_log.txt` next to the exe, for troubleshooting.

## Disclaimer
This is an unofficial, personal project. It is not affiliated with or endorsed by the makers of the tinySA.
"tinySA" is used only to describe which device this viewer works with. Use it at your own risk.

## License
MIT License. See [LICENSE](LICENSE).

## Files
- `tinysa_simple.py` — the window (graph, markers, saving)
- `tinysa_device.py` — communication with the tinySA (USB serial)
- `requirements.txt` — Python packages needed to run from source
- `icon.ico` — the app icon (`make_icon.py` recreates it)
