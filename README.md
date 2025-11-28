# rhodium

Rhodium is a simple task management with a focus on physical report generation. Daily todos are tracked and produced with a thermal printer.

## Prerequisites

python3 -m pip install -r requirements.txt

## UUID and Init

UUID is structured as follows:

```
<56-bit monotonic millisecond timestamp><16 LSb truncated SHA256 hash of mac address><56-bit persisted counter preseeded with random value>
```
