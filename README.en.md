# tinySA Simple Viewer

[日本語版はこちら (Japanese)](README.md)

### [⬇ Download the latest exe](https://github.com/nqf18003/tinysa-simple-viewer/releases/latest)
On that page, click `tinySA_Simple.exe` under **Assets**.

A minimal Windows viewer for the **tinySA Ultra / Ultra+** spectrum analyzer.
Connect the tinySA by USB and the PC shows the same sweep as the tinySA screen, with up to four markers.

- The frequency range and the vertical scale follow the tinySA settings.
- The frequency range, RBW, attenuation, external gain and MAX HOLD can also be changed from the PC.
- The top right of the graph shows the measurement conditions (frequency range, RBW, attenuation, external gain, date and time).
- Dotted guide lines for the occupied bandwidth can be drawn.
- **Pause** stops the tinySA and the graph after the current sweep.
- Markers M1-M4 can be placed with the mouse and moved with the arrow keys. The viewer and tinySA markers stay in sync both ways.
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
   - Changes made on the tinySA (range, reference level) also appear on the PC within about one sweep.
   - The top bar shows the model, hardware version and firmware version.
   - Slow sweep settings (wide span, narrow RBW) make the PC update slowly too.
   - The frequency axis always shows the actual frequency in MHz. Narrow spans get more decimal places.
   - The top right of the graph shows the frequency range, the RBW, the attenuation (Atten), the external gain (ExtGain)
     and the current date and time (to the minute).
     The range is shown in the form chosen in **Settings...** (Center + Span or Start + Stop). Start + Stop is the default.
     The tinySA cannot report its external gain, so ExtGain shows the value set from this viewer ("unknown" until you set one).
3. **tinySA settings** (only while connected)
   - **Settings...** opens a window filled in with the current tinySA settings.
     - Enter the frequency range as **Center / Span** (center in MHz, span in kHz) or **Start / Stop** (MHz).
     - Choose the **RBW** from the same values as the tinySA menu. The list depends on the model
       (Ultra: AUTO / 200 / 1k / 3k / 10k / 30k / 100k / 300k / 600k / 850k).
     - **Attenuation** (internal attenuator): choose from the list (AUTO / 0 / 5 / ... / 30 dB) or type a whole number from 0 to 30.
     - **External gain** (correction for an external attenuator, etc.): type a number from -100 to 100 dB. The tinySA cannot
       report its current value, so the field shows the value last set from this viewer (remembered per model in
       `tinysa_simple_settings.json` next to the exe). Leave it blank to keep the tinySA's value. A self test or calibration
       on the tinySA resets it to 0; set it again afterwards.
     - **OK** sends the settings to the tinySA. RBW and Attenuation are sent only when you change them, so AUTO on the tinySA
       is not switched off by accident. **Cancel** changes nothing.
   - **Max hold** turns MAX HOLD on the tinySA on (checked) or off (unchecked).
     Each time it changes, the graph and the held maximum are cleared and the measurement starts again.
     If you switch MAX HOLD on the tinySA itself, the check box follows it.
   - **Pause** stops the tinySA and the graph after the current sweep (the button changes to **Resume**). **Resume** starts
     them again. MAX HOLD is kept either way. While paused, markers, marker sync and saving still work.
     Changing a setting while paused resumes automatically.
   - After a settings change, the graph is cleared and comes back when the first sweep with the new settings finishes.
4. **Occupied bandwidth guide lines**
   - Choose a width under **Bandwidth lines** (0 = no lines, 0.5 / 3 / 6 / 30 / 40 kHz). Two vertical dotted lines are drawn at
     the center of the range ± half the width. Example: 6 kHz draws lines at center ± 3 kHz.
5. **Markers (up to 4)**

   | Action | How |
   |---|---|
   | Select a marker | **M1-M4** buttons or keys **1-4** |
   | Place it | Click on the graph |
   | Move 1 point / 10 points | **← →** / **Shift + ← →** |
   | Clear it | **Delete** key or **Clear selected marker** |

   - The frequency and level of each marker are shown below the graph.
   - **Viewer and tinySA markers stay in sync both ways.** Placing, moving or clearing M1-M4 in the viewer does the same to
     the tinySA marker with the same number, and moving, adding or clearing a marker on the tinySA does the same in the viewer,
     between sweeps. Peak-tracking markers on the tinySA (such as 1T) move in the viewer after every sweep too.
     Markers M5-M8 on the tinySA are not shown.
6. **Saving images.** Files go to the `captures` folder next to the exe (or next to `tinysa_simple.py`), named with the date and time.
   - **Save device screen** saves the tinySA screen (480 × 320). The screen is read between sweeps, so neither the tinySA nor the viewer stops.
   - **Save graph** saves the graph with the measurement conditions, guide lines, model, date/time and marker values. The image is always 1200 × 800 pixels, whatever the window size.
   - The bottom right of the window shows where the file was saved.
7. Click **Disconnect** or close the window when you are done.

## Notes
- **MAX HOLD on the tinySA works while connected** (switch it on the tinySA or with the **Max hold** check box). Clicking **Disconnect** returns the tinySA to normal sweeping,
  and this clears MAX HOLD.
- While connected, the tinySA may show **PAUSED** between sweeps. This is normal; the sweep continues.
- The tinySA restarts its sweep whenever it receives a command. The viewer only sends commands between sweeps.
- If the viewer says **"The tinySA is not responding"**: click Disconnect, unplug the USB cable, turn the tinySA off and on, then connect again.

## Data and privacy
- The viewer talks to the tinySA over USB only. Nothing is sent to the internet.
- Saved images stay in the `captures` folder on your PC.
- The external gain value is remembered only in `tinysa_simple_settings.json` next to the exe on your PC.
- If a communication problem happens, a short record is written to `error_log.txt` next to the exe, for troubleshooting.

## Disclaimer
This is an unofficial, personal project. It is not affiliated with or endorsed by the makers of the tinySA.
"tinySA" is used only to describe which device this viewer works with. Use it at your own risk.

## License
MIT License. See [LICENSE](LICENSE).

## Files
- `tinysa_simple.py` — the window (graph, markers, settings, saving)
- `tinysa_device.py` — communication with the tinySA (USB serial)
- `requirements.txt` — Python packages needed to run from source
- `icon.ico` — the app icon (`make_icon.py` recreates it)
