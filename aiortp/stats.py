from collections.abc import Sequence
import datetime
import itertools
import math

import numpy as np

from .dtmf import DTMF_MAP  # noqa


RTP_MAX_SEQ = 65535
RTP_PAYLOADS = {0: 'PCMU', 3: 'GSM', 4: 'G723', 8: 'PCMA', 9: 'G722',
                10: 'L16', 11: 'L16', 13: 'CN', 18: 'G729'}


def _calc_jitter(deltas):
    # J_n = J_{n-1} + \frac{|D| - J_{n-1}}{16}
    #
    # Where: J is jitter, D is the difference between the packet time
    # delta and the RTP timestamp delta n is the current packet in the
    # sequence
    jitter = np.empty(deltas.size)
    last = 0

    for idx, diff in enumerate(deltas):
        jitter[idx] = last = last + (diff - last) / 16

    return jitter


class JitterBuffer(Sequence):
    def __init__(self, packets):
        # Initialize with the first expected sequence number
        stream = [i.packet.seq for i in packets]
        expected_seq = first = stream[0]
        lost_packets = 0
        duplicates = 0
        duplicate_mask = []

        def lookahead(gap, position, window=10):
            '''Look ahead in the stream to spot any late packets'''
            loss = 0
            lookahead_buf = stream[position:position + window]
            for i in gap:
                if i not in lookahead_buf:
                    loss += 1
            return loss

        for position, current_seq in enumerate(stream):
            if current_seq == expected_seq:
                # This packet is not a duplicate and should be
                # included in the non-duplicated stream
                duplicate_mask.append(True)
                # set the next expected sequence number
                expected_seq += 1
            elif current_seq == expected_seq - 1:
                # Current seqence number is the same as the previous, therefore
                # duplicated
                duplicates += 1  # count the duplicate
                duplicate_mask.append(False)  # add it to the filter
            elif current_seq > expected_seq:
                # missing sequence numbers detected; Check ahead before
                # counting as loss
                gap = range(expected_seq, current_seq)
                lost_packets += lookahead(gap, position)
                expected_seq = current_seq + 1
                duplicate_mask.append(True)
            elif current_seq <= first:
                # sequence number is less than the current we must have
                # rolled over during loss
                gap = list(range(expected_seq, RTP_MAX_SEQ + 1))\
                    + list(range(0, current_seq))
                lost_packets += lookahead(gap, position)
                expected_seq = current_seq + 1
                duplicate_mask.append(True)

            if expected_seq > RTP_MAX_SEQ:
                # If we're about to roll over, reset to zero
                expected_seq = 0

        self.duplicates = duplicates / len(packets)
        self.lost = lost_packets
        self.loss = lost_packets / len(packets)
        self._data = list(itertools.compress(packets, duplicate_mask))

    def __getitem__(self, index):
        return self._data[index]

    def __len__(self):
        return len(self._data)


class StreamStats:
    def __init__(self, packets):
        self.packets = JitterBuffer(packets)

        codecs = set(pkt.packet.p_type for pkt in self.packets)
        self.codecs = [RTP_PAYLOADS.get(codec, str(codec)) for codec in codecs]

        timestamps = np.fromiter(
            (pkt.packet.timestamp for pkt in self.packets),
            np.float
        )

        frametimes = np.fromiter(
            (pkt.frametime for pkt in self.packets),
            np.float
        )

        timedelta = frametimes[-1] - frametimes[0]
        self.deltas = np.diff(frametimes) * 1000
        self.duration = datetime.timedelta(seconds=timedelta)
        self.sample_rate = 8000

        period = 1 / self.sample_rate
        rtpdeltas = np.diff(timestamps) * period * 1000
        deltas = np.abs(self.deltas - rtpdeltas)

        self.jitter = _calc_jitter(deltas)

        raw_audio = b''.join(x.packet.payload for x in self.packets)
        self.audio = np.fromstring(raw_audio, dtype=np.int8).astype(float)

        rms = np.linalg.norm(self.audio) / np.sqrt(self.audio.size)
        self.rms = math.log10(rms) * 20

        # self.rtpevents = list(iter_rtpevents(self.packets))
        # if self.rtpevents:
        #     self.digits = list(iter_dtmf(self.rtpevents))

    @property
    def has_rfc2833(self):
        return bool(self.rtpevents)

    @property
    def duplicates(self):
        return self.packets.duplicates

    @property
    def loss(self):
        return self.packets.loss
