# QoS Policy for Low-Latency Voice

This project now uses a hybrid UDP/TCP QoS policy:

- `UDP audio` (`port 50002`) -> `DSCP 46 (EF)`
- `TCP control` (`port 50001`) -> `DSCP 24 (CS3)`

## What is implemented in code

- Client marks real-time audio sockets as EF.
- Client marks control TCP sockets as CS3.
- Server marks audio forwarding sockets as EF.
- Server marks control sockets and discovery broadcast as CS3.

## Windows one-time policy installer (EXE)

Built artifact:

- `dist/VoiceQoSSetup.exe`

Source:

- `tools/qos_policy_installer.py`

Build script:

- `tools/build_qos_installer.ps1`

### Install policies

Run as Administrator:

```powershell
.\dist\VoiceQoSSetup.exe
```

### Remove policies

```powershell
.\dist\VoiceQoSSetup.exe --remove
```

The installer creates:

- `Audio_Data`: UDP dst port `50002`, DSCP `46`
- `Audio_Control`: TCP dst port `50001`, DSCP `24`

It also runs:

```powershell
gpupdate /target:computer /force
```

## Network-side queueing/scheduling (required on switches/routers)

Marking alone is not enough. Network devices must trust DSCP and queue correctly:

1. Enable DSCP trust on access ports.
2. Map `DSCP 46 (EF)` to strict-priority queue (SPQ/LLQ).
3. Map `DSCP 24 (CS3)` to high but non-strict queue.
4. Reserve bandwidth for voice:
   - baseline: `80-100 Kbps` per active G.711-quality stream
   - include overhead and concurrency headroom
5. Keep EF queue policed to avoid starvation of other classes.

## Verification checklist

1. Capture packets in Wireshark:
   - audio UDP should show DSCP `46`
   - control TCP should show DSCP `24`
2. Saturate background traffic and verify:
   - audio latency/jitter remains stable
   - control commands still respond quickly
