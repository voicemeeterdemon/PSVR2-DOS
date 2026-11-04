import sys
import time
import threading

import usb.core
import usb.util
import libusb_package
from usb.backend import libusb1

VID = 0x054c
PID = 0x0cde

HID_REQ_GET_REPORT = 0x01
HID_REQ_SET_REPORT = 0x09
HID_FEATURE_REPORT = 0x02 << 8
REPORT_ID_SET_AUTH1_DATA = 0xF0
REPORT_ID_GET_AUTH2_DATA = 0xF1
REPORT_ID_GET_AUTH_STATUS = 0xF2

UAC_SET_CUR = 0x01
AUDIO_WVALUE = 0x0200
AUDIO_WINDEX = 0x0201

EP0_BUFSIZ = 1024
GUARD_SIZES = [65, 256, 512, 1024]
GUARD_SUB_IDS = [0x02]

BACKEND = libusb1.get_backend(find_library=libusb_package.find_library)
if BACKEND is None:
    sys.exit("libusb backend failed to load")

try:
    _orig_claim = usb.core.Device.managed_claim_interface
    def _no_claim(self, intf):
        return None
    usb.core.Device.managed_claim_interface = _no_claim
except Exception:
    pass

if sys.platform == "win32":
    try:
        def _backend_no_claim(handle, intf):
            return 0
        BACKEND.claim_interface = _backend_no_claim
        BACKEND.release_interface = lambda handle, intf: 0
    except Exception:
        pass


def bm_class(direction):
    return usb.util.build_request_type(direction, usb.util.CTRL_TYPE_CLASS,
                                       usb.util.CTRL_RECIPIENT_INTERFACE)


def _find_thread(res):
    try:
        res.append(usb.core.find(backend=BACKEND, idVendor=VID, idProduct=PID))
    except Exception:
        res.append(None)


_busy_until = 0.0


def find_dev(timeout=3.0):
    global _busy_until
    if time.time() < _busy_until:
        return None
    res = []
    t = threading.Thread(target=_find_thread, args=(res,), daemon=True)
    t.start()
    t.join(timeout)
    if not res:
        _busy_until = time.time() + 1.0
        return None
    return res[0]


def detach(dev, iface):
    try:
        if dev.is_kernel_driver_active(iface):
            dev.detach_kernel_driver(iface)
    except (usb.core.USBError, NotImplementedError):
        pass


def auth_payload(n, sub_id=0x02):
    return b"\xf0" + bytes([sub_id]) + b"\x41" * (n - 2)


def audio_payload(n):
    return b"\xff" * n


def try_size(dev, bm, req, w_value, w_index, payload, timeout=300):
    try:
        dev.ctrl_transfer(bm, req, w_value, w_index, payload, timeout=timeout)
        return "accepted"
    except usb.core.USBTimeoutError:
        return "hang"
    except NotImplementedError:
        return "no-device"
    except usb.core.USBError as e:
        errno = getattr(e, "errno", None)
        if errno == 32:
            return "stall"
        if errno == 19:
            return "no-device"
        return "host-blocked"


def guard_hammer(dev, max_hits=100000):
    payloads = [auth_payload(n, s) for n in GUARD_SIZES for s in GUARD_SUB_IDS]
    step = 7919 % len(payloads)
    hits = 0
    i = 0
    stall_bm = bm_class(usb.util.CTRL_OUT)
    get_bm = bm_class(usb.util.CTRL_IN)
    audio_bm = usb.util.build_request_type(usb.util.CTRL_OUT,
                                           usb.util.CTRL_TYPE_CLASS,
                                           usb.util.CTRL_RECIPIENT_INTERFACE)
    cmd_bm = usb.util.build_request_type(usb.util.CTRL_OUT,
                                         usb.util.CTRL_TYPE_VENDOR,
                                         usb.util.CTRL_RECIPIENT_ENDPOINT)
    churn = [
        (get_bm, HID_REQ_GET_REPORT,
         HID_FEATURE_REPORT | REPORT_ID_GET_AUTH_STATUS, 0, 64, None),
        (get_bm, HID_REQ_GET_REPORT,
         HID_FEATURE_REPORT | REPORT_ID_GET_AUTH2_DATA, 0, 64, None),
        (audio_bm, UAC_SET_CUR, AUDIO_WVALUE, AUDIO_WINDEX, 128,
         audio_payload(128)),
        (cmd_bm, 0x09, 0x0000, 0x0000, 64, b"\x7f" + b"\x00" * 63),
    ]
    wv = HID_FEATURE_REPORT | REPORT_ID_SET_AUTH1_DATA
    while hits < max_hits:
        if find_dev() is None:
            return True, hits
        payload = payloads[(i * step) % len(payloads)]
        i += 1
        outcome = try_size(dev, stall_bm, HID_REQ_SET_REPORT, wv, 0, payload,
                           timeout=150)
        if outcome == "accepted":
            hits += 1
        elif outcome in ("no-device", "hang"):
            return True, hits + 1
        elif outcome == "host-blocked":
            time.sleep(0.5)
        else:
            time.sleep(0.02)
        if hits and hits % 2 == 0:
            idx = (hits // 2) % len(churn)
            bm, req, wv_, wi_, n, payload = churn[idx]
            try:
                if payload is None:
                    dev.ctrl_transfer(bm, req, wv_, wi_, n, timeout=300)
                else:
                    dev.ctrl_transfer(bm, req, wv_, wi_, payload, timeout=300)
            except (usb.core.USBError, usb.core.USBTimeoutError, NotImplementedError):
                pass
        if hits and hits % 16 == 0:
            try_size(dev, stall_bm, HID_REQ_SET_REPORT, wv, 0,
                     auth_payload(EP0_BUFSIZ + 1), timeout=300)
    return False, hits


def main():
    print("attempting recovery mode")
    print("run while turning on headset (playstation logo must be visible),"
          " when crashed unplug the headset and replug; rinse and repeat")
    try:
        while True:
            dev = find_dev()
            if dev is None:
                time.sleep(0.2)
                continue
            detach(dev, 0)
            detach(dev, 1)
            died, hits = guard_hammer(dev)
            if died:
                print("headset crashed at %s (%d hits)"
                      % (time.strftime("%H:%M:%S"), hits))
                if hits <= 2:
                    print("recovery mode is active")
                    break
                t0 = time.time()
                while find_dev() is not None and time.time() - t0 < 15:
                    time.sleep(0.2)
            else:
                print("attack did not kill it, retrying")
                time.sleep(2.0)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
