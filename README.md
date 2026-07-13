# ICR2 Animator

ICR2 Animator is a Windows Python tool for animating objects in *IndyCar Racing II* / *CART Racing* running under DOSBox or Windows (i.e. Windy). It finds objects in DOSBox/Windy memory by their current in-game coordinates (which is presumed to be unique to that specific object; it is deemed highly unlikely that the 3 coordinate values would match anything else in the game's memory), then updates their position and rotation values every animation frame so trackside objects can move, shuttle, loop, or spin while the game is running.

The project includes a Tkinter launcher for editing animation JSON files and controlling animation runs.

## What you need

- Windows, because the memory layer uses Win32 window and process APIs.
- A supported ICR2 executable/version running in DOSBox or the matching Windows title:
  - `REND32A` (default)
  - `DOS`
  - `WINDY`
- Python 3.10+ recommended.
- Python packages used by the memory layer:
  - `pymem`
  - `pywin32`

Install dependencies with your preferred environment manager, for example:

```powershell
python -m pip install pymem pywin32
```

## Quick start

1. Start ICR2/CART Racing in DOSBox and load the track/session that contains the object you want to animate.
2. Start the launcher:

   ```powershell
   python icr2_animator_launcher.py
   ```

3. Choose the matching game version in the launcher.
4. Confirm the **Window keywords** field matches words in the DOSBox/game window title. The animator uses these keywords to find the correct process.
5. Load an animation JSON file, or create objects in the launcher.
6. For each object, set `search_coords` to the object's current in-game `x`, `y`, and `z` coordinates. These are how the animator finds the object in memory.
7. Pick an animation mode and configure waypoints or spin rate.
8. Click **Save** to write the JSON config.
9. Click **Start animation**. The launcher validates the config, attaches to DOSBox, finds each object, and starts animation threads.
10. Click **Stop animation** before changing configs or closing the launcher. Stop restores found objects to the values captured when animation started.

## Launcher settings

The launcher stores application preferences in an INI file next to the launcher executable/script. The included `icr2_animator_launcher.ini` shows the format:

```ini
[launcher]
version = WINDY
config_path = firebird_east_animation.json
fps = 60
tooltips_enabled = no

[window_keywords]
rend32a = cart racing
dos = dosbox, indycar
windy = cart racing
```

Important settings:

- `version`: one of `REND32A`, `DOS`, or `WINDY`.
- `config_path`: the JSON animation file to load automatically.
- `fps`: how often the animator writes object values to memory. Higher values are smoother but use more CPU.
- `tooltips_enabled`: whether hover tooltips appear in the launcher.
- `[window_keywords]`: per-version comma-separated title keywords. All keywords for the selected version must appear in the target window title.

## Animation config format

Animation files are JSON files with a top-level `objects` list:

```json
{
  "objects": [
    {
      "name": "pace_truck",
      "search_coords": [100000, 2500, -50000],
      "mode": "ping_pong_path",
      "start_delay_seconds": 0,
      "start_position": {
        "x": 100000,
        "y": 2500,
        "z": -50000,
        "rot_x": 0,
        "rot_y": 90,
        "rot_z": 0
      },
      "waypoints": [
        {
          "x": 130000,
          "y": 2500,
          "z": -50000,
          "speed_mph": 25,
          "rot_x": 0,
          "rot_y": 90,
          "rot_z": 0
        }
      ],
      "spin_rate_deg_per_sec": [0, 0, 45]
    }
  ]
}
```

### Object fields

| Field | Required? | Meaning |
| --- | --- | --- |
| `name` | Yes | Friendly name shown in the launcher and logs. |
| `search_coords` | Yes | Current object coordinates in memory. Must be three integers: `[x, y, z]`. |
| `mode` | Yes | One of `ping_pong_path`, `return_to_start`, `reset_loop`, or `rotate_in_place`. |
| `start_delay_seconds` | No | Non-negative delay after clicking Start before this object begins animating. Useful for staggering multiple objects. |
| `start_position` | No | Optional start override. If present, the object is teleported here before animation begins. `x`, `y`, and `z` are required; `rot_x`, `rot_y`, and `rot_z` are optional degrees. |
| `waypoints` | Required for path modes | Non-empty list of destinations. Each waypoint needs `x`, `y`, and `z`; optional rotations are degrees; optional `speed_mph` controls travel speed to that waypoint. |
| `spin_rate_deg_per_sec` | Required for `rotate_in_place` | Three numeric rotation speeds in degrees per second: `[rot_x, rot_y, rot_z]`. |

Coordinates are stored as ICR2 integer units, where the code treats 500 units as one inch. Rotation values in JSON are degrees; the animator converts them to the signed 32-bit rotation units used by the game.

## Animation modes

### `ping_pong_path`

Moves from the starting position through each waypoint, then reverses through the same path and repeats. Use this for shuttles, gates, lifts, or other objects that should retrace their route smoothly.

### `return_to_start`

Moves from the starting position through each waypoint, then travels directly back to the starting position and repeats. Use this when the return leg should be a single straight segment.

### `reset_loop`

Moves from the starting position through each waypoint, then instantly snaps back to the starting position and repeats. Use this for one-way loop effects where the reset can happen off camera or be hidden.

### `rotate_in_place`

Leaves the object at its starting coordinates and continuously updates rotation using `spin_rate_deg_per_sec`. Waypoints are ignored for this mode.

## Under the hood

ICR2 Animator works by editing the running game's memory:

1. **Window/process discovery**: the selected version maps to default window-title keywords, a byte signature, and a signature offset. The memory layer finds a visible window whose title contains all selected keywords, then opens that process.
2. **EXE base detection**: after attaching, the memory layer scans committed readable memory regions for a known ICR2 signature. It subtracts the version-specific offset from the signature address to compute the game's executable base address.
3. **Object discovery**: when animation starts, each configured object's `search_coords` are scanned in process memory. The match gives a relative address for the object's six-value record.
4. **Object record reads/writes**: every object record is treated as six little-endian 32-bit integers: `x`, `y`, `z`, `rotX`, `rotY`, and `rotZ`. Reads and writes are performed at `exe_base + relative_address`.
5. **Path interpolation**: for movement modes, the animator computes distance in inches, converts waypoint `speed_mph` to inches per second, derives a duration, and linearly interpolates position and rotation for each frame.
6. **Rotation conversion**: JSON/config rotations are in degrees. Internally, a full 360-degree rotation maps across the 32-bit integer range (`4294967296 / 360` units per degree).
7. **Parallel animation**: each object runs in its own daemon thread. A shared stop event lets the service ask all threads to exit cooperatively.
8. **Cleanup/restore**: before animating, the service captures each found object's original six values. On Stop, it writes those values back and closes the process handle.

## Safety notes

ICR2 Animator includes several safeguards to reduce the risk of accidental or incorrect memory writes:

- **Version-specific discovery**: the selected game version controls the expected window keywords, executable byte signature, and signature offset, so the animator can verify it has found a compatible process before calculating object addresses.
- **Readable-memory scanning only**: object and executable-signature searches are limited to committed readable memory regions exposed by the target process.
- **Coordinate-based object lookup**: objects are found by their configured `search_coords`, and the launcher validates that these coordinates are three integers before animation starts.
- **Relative object addressing**: after discovery, writes are made to the object's six-value record at `exe_base + relative_address`, rather than to arbitrary user-entered absolute addresses.
- **Six-field record writes**: object updates are constrained to the expected `x`, `y`, `z`, `rotX`, `rotY`, and `rotZ` 32-bit integer fields.
- **Original-value restore**: before animating, the service captures each found object's original six values. Clicking **Stop animation** asks all animation threads to exit and writes those captured values back.
- **Cooperative thread stopping**: animation threads share a stop event so the launcher can stop active writes cleanly before configs are edited or the app exits.

Even with these safeguards, live process-memory editing is inherently risky. Use the correct version and window keywords; wrong version settings can produce wrong offsets and unsafe memory interpretation. Keep `search_coords` specific because the animator starts with the first matching object it finds for those coordinates. Stop animation before editing a config or closing the launcher. Save your game/session state and use the tool on test installs first.

By using ICR2 Animator, you acknowledge that you are responsible for any damage, data loss, crashes, corrupted saves, system instability, or other consequences caused by using the tool or by editing live process memory.

## Development

Run the current automated tests with:

```bash
python -m unittest
```

The test suite currently covers launcher settings defaults, sanitization, and keyword handling. Live memory animation requires a supported game process and is not covered by these unit tests.
