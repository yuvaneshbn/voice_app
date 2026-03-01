import argparse
import ctypes
import subprocess
import sys


AUDIO_POLICY_NAME = "Audio_Data"
CONTROL_POLICY_NAME = "Audio_Control"
AUDIO_DSCP = 46
CONTROL_DSCP = 24
AUDIO_PORT = 50002
CONTROL_PORT = 50001


def _is_windows():
    return sys.platform.startswith("win")


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_elevated():
    args = [arg for arg in sys.argv[1:] if arg != "--elevated"]
    args.append("--elevated")
    params = subprocess.list2cmdline(args)
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        params,
        None,
        1,
    )
    return result > 32


def _run_powershell(script):
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _qos_script(remove_only):
    remove_value = "$true" if remove_only else "$false"
    return f"""
$ErrorActionPreference = 'Stop'
$removeOnly = {remove_value}

if (-not (Get-Command New-NetQosPolicy -ErrorAction SilentlyContinue)) {{
  throw "New-NetQosPolicy cmdlet not found. Install NetQoS features or run on Windows 10/11 with QoS policy support."
}}

$policyNames = @('{AUDIO_POLICY_NAME}', '{CONTROL_POLICY_NAME}')
$stores = @('ActiveStore', 'PersistentStore')
foreach ($name in $policyNames) {{
  foreach ($store in $stores) {{
    $existing = Get-NetQosPolicy -Name $name -PolicyStore $store -ErrorAction SilentlyContinue
    if ($existing) {{
      Remove-NetQosPolicy -Name $name -PolicyStore $store -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
    }}
  }}
}}

if (-not $removeOnly) {{
  New-NetQosPolicy -Name '{AUDIO_POLICY_NAME}' `
    -NetworkProfile All `
    -IPProtocolMatchCondition UDP `
    -IPDstPortStartMatchCondition {AUDIO_PORT} `
    -IPDstPortEndMatchCondition {AUDIO_PORT} `
    -DSCPAction {AUDIO_DSCP} `
    -PolicyStore PersistentStore | Out-Null

  New-NetQosPolicy -Name '{CONTROL_POLICY_NAME}' `
    -NetworkProfile All `
    -IPProtocolMatchCondition TCP `
    -IPDstPortStartMatchCondition {CONTROL_PORT} `
    -IPDstPortEndMatchCondition {CONTROL_PORT} `
    -DSCPAction {CONTROL_DSCP} `
    -PolicyStore PersistentStore | Out-Null
}}

gpupdate /target:computer /force | Out-Null
Write-Output "OK"
"""


def main():
    parser = argparse.ArgumentParser(description="Install or remove local Windows QoS policies for voice app traffic.")
    parser.add_argument("--remove", action="store_true", help="Remove the QoS policies instead of creating them.")
    parser.add_argument("--elevated", action="store_true", help="Internal flag used after elevation.")
    args = parser.parse_args()

    if not _is_windows():
        print("This installer works only on Windows.")
        return 1

    if not _is_admin():
        if args.elevated:
            print("Administrator privileges are required.")
            return 1
        print("Requesting administrator privileges...")
        if not _relaunch_elevated():
            print("Elevation failed or was cancelled.")
            return 1
        return 0

    code, out, err = _run_powershell(_qos_script(remove_only=args.remove))
    if code != 0:
        print("Failed to apply QoS policies.")
        if out:
            print(out)
        if err:
            print(err)
        return code

    if args.remove:
        print("QoS policies removed successfully.")
    else:
        print("QoS policies applied successfully.")
        print(f"- {AUDIO_POLICY_NAME}: UDP/{AUDIO_PORT} DSCP {AUDIO_DSCP} (EF)")
        print(f"- {CONTROL_POLICY_NAME}: TCP/{CONTROL_PORT} DSCP {CONTROL_DSCP} (CS3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
