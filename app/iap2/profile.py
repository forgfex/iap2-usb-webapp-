"""Accessory identity (0x1D01) + location message (0xFFFB).

Based on accessory-location src/accessory_location/profile.py (GPL-3.0),
extended so the identity fields are configurable (iap2loc
AccessoryProfile: name/model/manufacturer/serial/firmware/hardware)
and the NMEA sentences carry altitude/speed/bearing/accuracy/quality/
satellites from the shared LocationTarget instead of fixed values.

Identity fields 0-9, languages 12/13, transport group 16 (USB) or 17
(Bluetooth, unused here -- this app is USB-first), location group 22
declaring GGA (17) / RMC (18). See upstream docs/protocol.md.
"""
import struct

from ..nmea import location_sentences
from .wire import Message, parameter, text_parameter


def identity(name="iAP2 Loc", model="Loc1,1", manufacturer="iap2-usb-web",
             serial="LOC000000001", firmware="0.1.0", hardware="1.0",
             transport="usb", bluetooth_address=None):
    fields = [
        text_parameter(i, v)
        for i, v in enumerate((name, model, manufacturer, serial, firmware, hardware))
    ]
    fields += [
        parameter(6, struct.pack(">H", 0xFFFB)),
        parameter(7, struct.pack(">HH", 0xFFFA, 0xFFFC)),
        parameter(8, b"\0"),
        parameter(9, b"\0\0"),
        text_parameter(12, "en"),
        text_parameter(13, "en"),
    ]
    component = parameter(0, b"\0\0") + text_parameter(1, "Location Transport") + parameter(2)
    if transport == "usb":
        fields.append(parameter(16, component))
    elif transport == "bluetooth":
        if bluetooth_address is None or len(bluetooth_address) != 6:
            raise ValueError("Bluetooth identity needs a six-byte local controller address")
        fields.append(parameter(17, component + parameter(3, bluetooth_address)))
    else:
        raise ValueError("Unknown transport")
    location = parameter(0, b"\0\0") + text_parameter(1, "GPS")
    fields.append(parameter(22, location + parameter(17) + parameter(18)))
    return Message(0x1D01, b"".join(fields))


def location_message(fix, sentences_by_id=None):
    """0xFFFB carrying the GGA/RMC sentences for one Fix.

    `sentences_by_id` is the subset iOS subscribed to in 0xFFFA
    (1=GGA, 2=RMC); None sends both.
    """
    sentences = location_sentences(
        fix.latitude, fix.longitude,
        altitude=fix.altitude, speed_mps=fix.speed_mps,
        bearing_deg=fix.bearing_deg, accuracy_m=fix.accuracy_m,
        quality=fix.quality, satellites=fix.satellites,
    )
    wanted = sentences_by_id or {1, 2}
    return Message(0xFFFB, b"".join(
        text_parameter(0, sentences[i]) for i in sorted(wanted) if i in sentences))
