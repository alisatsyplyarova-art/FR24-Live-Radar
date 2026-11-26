# FR24 Live Radar

A lightweight desktop radar for displaying the nearest live aircraft around a selected airport or manually entered coordinates.

## Features

- PySide6 desktop interface with aircraft list and gray radar display.
- Aircraft shown as heading-oriented triangles.
- Airport presets and manual latitude/longitude.
- Radius: 6, 10, 20, 50, 100, 200, 400, 600 or 1000 km.
- Configurable aircraft limit (default 15).
- Refreshes the display every 5 seconds.
- Removes aircraft not seen for 120 seconds.
- Reads data from a local HTTP endpoint exposed by `live_collector.py`; the GUI does not make requests to FlightRadar24.

## Project files

- `live_collector.py` — FR24 LiveFeed collector with a local read-only API at `http://127.0.0.1:8765/aircraft`.
- `fr24_radar.py` — desktop radar GUI.
- `requirements.txt` — Python package dependencies.

## Important: existing collector modules

The collector integrates with the decoder/export modules from the existing `C:\\FR24_Collector` project (for example `grpc_web.py`, `feed_decoder.py`, `analysis_context.py`, `model.py`, and `track_export.py`). Those modules and the HAR request template are not duplicated here.

To use these two files in the existing project, copy them into `C:\\FR24_Collector`, preserving the other project files and your existing `www.flightradar24.com.har` and `radar_config.json`.

## Install

Open PowerShell in the project directory:

```powershell
py -3.14 -m venv venv
.\\venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run

Start the collector in the first PowerShell window:

```powershell
python live_collector.py
```

Start the GUI in a second PowerShell window:

```powershell
python fr24_radar.py
```

The collector must remain running. The GUI reads only from localhost and does not initiate another FR24 session.

## Local API

`GET http://127.0.0.1:8765/aircraft`

Returns JSON containing `generated_at` and the current aircraft snapshot.

## Notes

- The repository is public. Do not commit HAR files, cookies, authorization headers, personal configuration, captures, exports, or API credentials.
- FR24 field interpretation (heading, altitude and speed) is based on analysis of decoded LiveFeed samples and should be treated as provisional until independently validated.
- Use the service in accordance with FlightRadar24 terms and applicable laws.
