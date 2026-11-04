# PSVR2-DoS

PoC for a DoS vulnerability in Sony PlayStation VR2.

A USB HID `SET_REPORT` control transfer (report ID `0xF0`, sub-ID
`0x02`, `wLength > 64`) reaches the headset's `sieusb` driver and triggers an
improper locking bug. The headset kernel panics and the device crashes, with enough crashes the headset boots into recovery mode,
which then can be used to install older firmware.

## Affected versions
- **6.10 through 7.00**

## Root cause

`usb_auth_set_response_data()` and `usb_auth_set_auth1_data()` in
`sieusb/authentication.c` check `req->length` against `sizeof(auth1_data)`
before acquiring `auth->lock`. On a length mismatch they branch to their
`cleanup` path, which calls `spin_unlock_irqrestore(&auth->lock, flags)` on a
lock that was never held, using uninitialized `flags`. This corrupts the
kernel's interrupt and preemption state and panics the headset.

## CVE

CVE ID requested from MITRE CNA-LR; Sony is not a CNA for this product.
Status: pending. This section will be updated with the assigned ID.

## Disclaimer

Tested only on hardware I own. For security research and disclosure. Do not
use on devices you do not own.
