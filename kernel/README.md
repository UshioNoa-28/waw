# VHF Virtual Mouse Driver

A KMDF driver that presents a **virtual HID mouse** through the Windows
Virtual HID Framework (VHF). Reports submitted by the user-mode client
(`valaim/hid_mouse.py`) travel the real input stack:

```
user mode  --ring buffer-->  vhidmouse.sys  --VhfReadReportSubmit-->  hidclass.sys
                                                                          |
                                                                          v
                                                                      mouclass.sys
                                                                          |
                                                                          v
                                                                       win32k.sys
```

Because the event is authored by a HID device driver and not by
`user32!SendInput`, it carries no `LLMHF_INJECTED` flag and is not
classified as synthetic input. Vanguard's input filter sees a mouse move.

Nothing hooks SSDT/IRP handlers and the running kernel image is not patched,
so PatchGuard/KPP are untouched.

## Layout

```
kernel/
  shared/vhid_proto.h        shared structs: ring layout, report, IOCTLs
  vhidmouse/
    vhidmouse.c              the driver
    vhidmouse.vcxproj        VS2022 + WDK project
    vhidmouse.inf            root-enumerated device install
    build.cmd                one-shot build
```

## 1. Build

Install **Visual Studio 2022** (Desktop development with C++) and the
**WDK 10.0.26100.0** (which also pulls the WDF/VHF headers and `VhfKm.lib`).

From *x64 Native Tools Command Prompt for VS 2022*:

```
cd kernel\vhidmouse
build.cmd
```

Produces `build\vhidmouse\vhidmouse.sys`.

## 2. Load

Pick one of the three routes below.

### Route A - attestation signing (legitimate)

Requires an EV code-signing certificate bound to a Partner Center account.
Submit the CAB for attestation signing, then install:

```
pnputil /add-driver vhidmouse.inf /install
```

The device appears as *Vhid Virtual Mouse* under Mice.

### Route B - test signing (dev only, blocked by Vanguard)

One-click from the project root, **as Administrator**:

```
install_driver.bat
```

It builds the driver, creates and trusts a self-signed certificate, signs the
`.sys`, enables test signing (reboots if it was off - run it again after the
reboot), then installs and starts the device. To remove everything:

```
uninstall_driver.bat
```

Remember to `bcdedit /set testsigning off` when you are done, or Vanguard
refuses to launch. This route is only for verifying the aim pipeline against
non-Vanguard targets.

### Route C - BYOVD mapping (unsigned, no certificate)

Load the `.sys` in kernel mode by abusing a *signed but vulnerable* driver
(e.g. the classic `kdmapper`-style path through an intel/rt-core memory
mapping primitive). Requirements:

1. The carrier driver must **not** be on Vanguard's blocklist (the list is
   hash-based and lags behind; pick a recent, obscure one).
2. Map, then **immediately unload** the carrier. Do not leave it resident.
3. The mapped `vhidmouse.sys` must get `DriverEntry` called and create the
   device + section. After that the WDK object model takes over.

Once the driver is live, both handlers below exist:

```
\\.\Global\VhidMouse                 control device
\BaseNamedObjects\Global\VhidReportRing    shared ring
```

## 3. Verify

```
# driver present?
sc query vhidmouse
# device present?
powershell "Get-PnpDevice -FriendlyName '*Vhid*'"
```

Then run the aim tool with the virtual-HID backend:

```
python -m valaim.main --input-backend vhid --debug
```

Expected startup line:

```
Input backend: vhid
```

If the driver is missing, `auto` silently falls back to `sendinput` and
prints a warning.

## Detection-surface notes

* **Device identity.** The INF as written reports `Root\VhidMouse`. Before
  using it against Vanguard, change the hardware ID and the USB string
  descriptors in `vhidmouse.c` / `vhidmouse.inf` to clone a real mouse
  (Logitech/Razer VID+PID). A generic root device is an obvious flag.
* **Timing.** Reports are drained on a 1 ms timer. Real mice are interrupt
  driven; if behavioural timing analysis matters, jitter the drain interval
  instead of using a fixed period.
* **Ring usage.** `Dropped` in the section header counts packets lost to a
  full ring. On a healthy run it stays 0.
* **Do not test on your main account.**

## Protocol summary (`shared/vhid_proto.h`)

| Item | Value |
|---|---|
| Protocol version | 1 |
| Report | `buttons:u8, dx:s8, dy:s8, wheel:s8` (4 bytes) |
| Ring | 256 slots, SPSC, `Head` bumped by user mode, `Tail` by the driver |
| Section | `\BaseNamedObjects\Global\VhidReportRing` |
| Device | `\Device\VhidMouse` -> `\DosDevices\Global\VhidMouse` |
| IOCTL_ATTACH | `0x800` - create/attach the VHF device |
| IOCTL_DETACH | `0x801` - tear the VHF device down |
| IOCTL_QUERY | `0x802` - version, attached, dropped, pending |
