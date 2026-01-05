import sys
from escpos.printer import Usb

try:
	p = Usb(idVendor=0x04b8, idProduct=0x0202, timeout=0, in_ep=0x82, out_ep=0x01, profile="TM-T88III")

	p.text("Receipt Test\n")
	p.cut()
except Exception as e:
	print(f"Error: {e}")
	sys.exit(1)

if p.is_online():
	print("Printer is Online")
else:
	print("Printer is Offline")