"""PC-side helper: purge stale BtAimBridge device nodes from Windows.

Windows keeps half-deleted device nodes after repeated pair/unpair cycles;
those zombies report driver errors and cause "pairs then instantly
disconnects". This tool:
  1. lists every BtAimBridge node INCLUDING hidden ones, with its problem code
  2. removes/uninstalls them all
  3. reports the Bluetooth adapter's own health
  4. restarts the Bluetooth Support Service

Run as Administrator:  ValAim.exe --bt-clean
"""

from __future__ import annotations

import subprocess
import sys

_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
Write-Output '=== BtAimBridge device nodes (incl. hidden) ==='
$targets = @(Get-PnpDevice -PresentOnly:$false | Where-Object { $_.FriendlyName -match 'BtAimBridge' -or $_.Name -match 'BtAimBridge' })
if ($targets.Count -eq 0) { Write-Output '  (none found)' }
foreach ($d in $targets) {
    $prob = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_ProblemCode').Data
    Write-Output ('  [{0}] {1}  status={2} problem={3}' -f $d.Class, $d.InstanceId, $d.Status, $prob)
}
Write-Output '=== Removing them ==='
foreach ($d in $targets) {
    Remove-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -Uninstall | Out-Null
    Write-Output ('  removed: ' + $d.InstanceId)
}
Write-Output '=== Bluetooth adapter health ==='
Get-PnpDevice -Class Bluetooth -PresentOnly | ForEach-Object {
    Write-Output ('  {0}  status={1}  problem={2}' -f $_.FriendlyName, $_.Status, (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_ProblemCode').Data)
}
Write-Output '=== Restarting Bluetooth service ==='
Restart-Service bthserv -Force
Write-Output '  bthserv restarted'
Write-Output '=== Cycling Bluetooth adapter (disable/enable) ==='
$adapters = @(Get-PnpDevice -Class Bluetooth -PresentOnly | Where-Object { $_.Status -eq 'OK' -or $_.FriendlyName -match 'Intel|Realtek|MediaTek|Qualcomm|Wireless|Bluetooth|RT[0-9]|MT[0-9]' })
foreach ($a in $adapters) {
    if ($a.FriendlyName -match 'BtAimBridge') { continue }
    Write-Output ('  cycling: ' + $a.FriendlyName)
    Disable-PnpDevice -InstanceId $a.InstanceId -Confirm:$false | Out-Null
    Start-Sleep -Seconds 3
    Enable-PnpDevice -InstanceId $a.InstanceId -Confirm:$false | Out-Null
}
Write-Output '  adapter cycled'
Write-Output 'DONE'
"""


def run() -> int:
    print("扫描并清除 Windows 侧 BtAimBridge 残留设备节点(含隐藏)...")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"powershell 调用失败: {exc}")
        return 1
    print(proc.stdout or "")
    if "Access is denied" in (proc.stderr or "") or "拒绝访问" in (proc.stderr or ""):
        print("[!] 权限不足:请用【管理员身份】运行:ValAim.exe --bt-clean")
        return 1
    print("完成后:")
    print("  1. 蓝牙开关 关→等5秒→开(或直接重启电脑,更稳)")
    print("  2. 手机 App 打开 → Make discoverable → Windows 重新配对")
    return 0


if __name__ == "__main__":
    sys.exit(run())
