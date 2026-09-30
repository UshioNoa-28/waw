"""PC-side helper: purge stale BtAimBridge device nodes from Windows.

Stale pairing state on the Windows side is a common cause of "pairs then
instantly disconnects". Removing every PnP node that references BtAimBridge
before re-pairing guarantees Windows stores fresh keys. (The phone side must
be unpaired from its own Bluetooth settings - Android forbids third-party
apps from removing bonds.)

Run as Administrator:  ValAim.exe --cli --bt-clean
"""

from __future__ import annotations

import subprocess
import sys

_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$targets = @(Get-PnpDevice | Where-Object { $_.FriendlyName -match 'BtAimBridge' -or $_.InstanceId -match 'BtAimBridge' })
if ($targets.Count -eq 0) { Write-Output 'NONE' }
foreach ($d in $targets) {
    Write-Output ('REMOVING: ' + $d.FriendlyName + '  [' + $d.Class + ']')
    Remove-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -Uninstall
}
"""


def run() -> int:
    print("清理 Windows 侧 BtAimBridge 残留设备(需要管理员权限)...")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"powershell 调用失败: {exc}")
        return 1
    out = (proc.stdout or "").strip()
    if not out or out == "NONE":
        print("Windows 侧没有 BtAimBridge 残留,干净。")
    else:
        for line in out.splitlines():
            print("  " + line)
    if "Access is denied" in (proc.stderr or ""):
        print("[!] 权限不足:请用【管理员身份】运行:ValAim.exe --cli --bt-clean")
        return 1
    print()
    print("完成。接下来:")
    print("  1. 手机:蓝牙设置 → 你的电脑 → 取消配对(手机端唯一要做的)")
    print("  2. 重启手机(推荐,清蓝牙栈)")
    print("  3. App START → Make discoverable → Windows 重新配对")
    return 0


if __name__ == "__main__":
    sys.exit(run())
