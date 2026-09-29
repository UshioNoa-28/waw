# BtAimBridge — phone as a Bluetooth HID mouse for ValAim

This is the **no-hardware, no-driver, Vanguard-proof** input path:

```
ValAim (PC)  --TCP/LAN-->  BtAimBridge (phone)  --Bluetooth HID-->  game
```

* Verified working while a Vanguard-protected game is focused.
* No kernel driver. No EV certificate. No USB dongle. The phone is paired to
  Windows as a **standard Bluetooth mouse**, so the OS generates the input.
* Latency: LAN TCP ~1–3 ms + Bluetooth HID ~10–20 ms ≈ **15–25 ms**.

---

## Build the APK with GitHub Actions (no Android Studio)

You do not need to install anything. GitHub builds the APK for you.

### Step 1 — create a repository

1. Go to https://github.com/new
2. Name it anything (e.g. `valaim-bridge`), keep it **Private** if you like.
3. Click **Create repository**.

### Step 2 — upload the project

Easiest: on the new repo page click **"uploading an existing file"**, then drag
the **entire `android` folder and the `.github` folder** from this project into
the browser. Commit.

> The `.github/workflows/build-apk.yml` file is what triggers the build. Make
> sure it ends up in the repo (drag the hidden `.github` folder too).

### Step 3 — let it build

1. Open the **Actions** tab of your repo.
2. The "Build BtAimBridge APK" workflow starts automatically.
3. Wait ~3–5 minutes for the green check.

### Step 4 — download the APK

1. Click the completed workflow run.
2. Scroll to **Artifacts**, download **`BtAimBridge-debug-apk`**.
3. Unzip it — inside is **`app-debug.apk`**.

### Step 5 — install on the phone

Transfer `app-debug.apk` to your phone and tap it. Allow "install unknown
apps" for your file manager / browser when asked.

---

## Build the APK with Android Studio (alternative)

1. Install Android Studio: https://developer.android.com/studio
2. `File > Open` → select the `android/BtAimBridge` folder.
3. Let it sync (downloads Gradle + SDK).
4. `Build > Build Bundle(s)/APK(s) > Build APK(s)`.
5. APK at `app/build/outputs/apk/debug/app-debug.apk`.

---

## Use it

### Phone

1. Turn Bluetooth on.
2. Open **BtAimBridge**, note the **IP address** shown.
3. Optionally change the **TCP port** (default `47800`).
4. Tap **START**, grant Bluetooth + notification permissions.
5. Tap **Open battery settings** → set BtAimBridge to **Unrestricted**
   (HyperOS kills background apps aggressively).

### Windows pairing

1. **Settings > Bluetooth & other devices > Add device > Bluetooth**.
2. Select **BtAimBridge** (the app must be running).
3. Confirm: Device Manager > `Mice and other pointing devices` should now have
   an extra `HID-compliant mouse`. Move a finger in the app — the PC cursor
   should move.

### Run ValAim

```cmd
python -m valaim.main --input-backend bt --bt-host 192.168.x.x --bt-port 47800 --debug
```

Expected:

```
Input backend: bt
[input] BT bridge -> 192.168.x.x:47800 (phone must be paired as Bluetooth mouse)
```

Or set `BT_BRIDGE_HOST=192.168.x.x` and leave `--input-backend auto`.

---

## Protocol

ASCII lines over TCP:

| Line | Meaning |
|---|---|
| `M <dx> <dy>` | relative move (split into ±127 HID steps by the app) |
| `B <mask>` | buttons: bit0 left, bit1 right, bit2 middle |
| `W <ticks>` | wheel |
| `P` | keepalive |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Actions tab shows no workflow | `.github/workflows/build-apk.yml` missing from repo root |
| APK build fails in CI | open the failing step, copy the Gradle error |
| "Cannot get HID profile proxy" | grant Nearby-devices permission; toggle Bluetooth |
| Windows can't find "BtAimBridge" | app must be foreground with START pressed |
| Cursor stops after a while | set app battery to Unrestricted |
| Dotori + BtAimBridge conflict | unregister/uninstall one, or reboot phone |
| PC can't connect | same Wi-Fi? correct IP? 5 GHz preferred |

## Safety

* Test on a **secondary account**.
* Keep the app in the foreground; Android unregisters a background HID app.
