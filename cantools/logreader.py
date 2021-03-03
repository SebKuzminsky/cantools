import re
import dataclasses
import enum
import binascii
import datetime


class TimestampFormat(enum.Enum):
    ABSOLUTE = 1
    RELATIVE = 2
    MISSING = 3


@dataclasses.dataclass
class DataFrame:
    channel: str
    frame_id: int
    data: bytes
    timestamp: datetime.datetime
    timestamp_format: TimestampFormat


class BasePattern:
    @classmethod
    def match(clz, line):
        mo = clz.pattern.match(line)
        if mo:
            return clz.unpack(mo)


class CandumpDefaultPattern(BasePattern):
    # vcan0  1F0   [8]  00 00 00 00 00 00 1B C1
    pattern = re.compile(
        r'^\s*?(?P<channel>[a-zA-Z0-9]+)\s+(?P<can_id>[0-9A-F]+)\s+\[\d+\]\s*(?P<can_data>[0-9A-F ]*)$')

    @staticmethod
    def unpack(match_object):
        channel = match_object.group('channel')
        frame_id = int(match_object.group('can_id'), 16)
        data = match_object.group('can_data')
        data = data.replace(' ', '')
        data = binascii.unhexlify(data)
        timestamp = None
        timestamp_format = TimestampFormat.MISSING

        return DataFrame(channel=channel, frame_id=frame_id, data=data, timestamp=timestamp, timestamp_format=timestamp_format)


class CandumpTimestampedPattern(BasePattern):
    # (000.000000)  vcan0  0C8   [8]  F0 00 00 00 00 00 00 00
    pattern = re.compile(
        r'^\s*?\((?P<timestamp>[\d.]+)\)\s+(?P<channel>[a-zA-Z0-9]+)\s+(?P<can_id>[0-9A-F]+)\s+\[\d+\]\s*(?P<can_data>[0-9A-F ]*)$')

    @staticmethod
    def unpack(match_object):
        channel = match_object.group('channel')
        frame_id = int(match_object.group('can_id'), 16)
        data = match_object.group('can_data')
        data = data.replace(' ', '')
        data = binascii.unhexlify(data)

        seconds = float(match_object.group('timestamp'))
        if seconds < 662688000:  # 1991-01-01 00:00:00, "Released in 1991, the Mercedes-Benz W140 was the first production vehicle to feature a CAN-based multiplex wiring system."
            timestamp = datetime.timedelta(seconds=seconds)
            timestamp_format = TimestampFormat.RELATIVE
        else:
            timestamp = datetime.datetime.utcfromtimestamp(seconds)
            timestamp_format = TimestampFormat.ABSOLUTE

        return DataFrame(channel=channel, frame_id=frame_id, data=data, timestamp=timestamp, timestamp_format=timestamp_format)


class CandumpDefaultLogPattern(BasePattern):
    # (1579857014.345944) can2 486#82967A6B006B07F8
    # (1613656104.501098) can2 14C##16A0FFE00606E022400000000000000A0FFFF00FFFF25000600000000000000FE
    pattern = re.compile(
        r'^\s*?\((?P<timestamp>[\d.]+)\)\s+(?P<channel>[a-zA-Z0-9]+)\s+(?P<can_id>[0-9A-F]+)#(#[0-9A-F])?(?P<can_data>[0-9A-F]*)$')

    @staticmethod
    def unpack(match_object):
        channel = match_object.group('channel')
        frame_id = int(match_object.group('can_id'), 16)
        data = match_object.group('can_data')
        data = data.replace(' ', '')
        data = binascii.unhexlify(data)
        timestamp = datetime.datetime.utcfromtimestamp(float(match_object.group('timestamp')))
        timestamp_format = TimestampFormat.ABSOLUTE

        return DataFrame(channel=channel, frame_id=frame_id, data=data, timestamp=timestamp, timestamp_format=timestamp_format)


class CandumpAbsoluteLogPattern(BasePattern):
    # (2020-12-19 12:04:45.485261)  vcan0  0C8   [8]  F0 00 00 00 00 00 00 00
    pattern = re.compile(
        r'^\s*?\((?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\)\s+(?P<channel>[a-zA-Z0-9]+)\s+(?P<can_id>[0-9A-F]+)\s+\[\d+\]\s*(?P<can_data>[0-9A-F ]*)$')

    @staticmethod
    def unpack(match_object):
        channel = match_object.group('channel')
        frame_id = int(match_object.group('can_id'), 16)
        data = match_object.group('can_data')
        data = data.replace(' ', '')
        data = binascii.unhexlify(data)
        timestamp = datetime.datetime.strptime(match_object.group('timestamp'), "%Y-%m-%d %H:%M:%S.%f")
        timestamp_format = TimestampFormat.ABSOLUTE

        return DataFrame(channel=channel, frame_id=frame_id, data=data, timestamp=timestamp, timestamp_format=timestamp_format)


class Parser:
    def __init__(self, stream=None):
        self.stream = stream
        self.pattern = None

    @staticmethod
    def detect_pattern(line):
        for p in [CandumpDefaultPattern, CandumpTimestampedPattern, CandumpDefaultLogPattern, CandumpAbsoluteLogPattern]:
            mo = p.pattern.match(line)
            if mo:
                return p

    def parse(self, line):
        if self.pattern is None:
            self.pattern = self.detect_pattern(line)
        if self.pattern is None:
            return None
        return self.pattern.match(line)

    def iterlines(self, keep_unknowns=False):
        if self.stream is None:
            return
        while True:
            nl = self.stream.readline()
            if nl == '':
                return
            nl = nl.strip('\r\n')
            frame = self.parse(nl)
            if frame:
                yield nl, frame
            elif keep_unknowns:
                yield nl, None
            else:
                continue

    def __iter__(self):
        for _, frame in self.iterlines():
            yield frame
