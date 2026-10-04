# SPDX-License-Identifier: GPL-3.0-or-later
# Clock model references gr-isdbt utils/multiplex_frame_pattern.py by
# Pablo Flores Guridi (2017). See native/UPSTREAM-LICENSE and native/README.md.
"""STD-B31 3.2.2 slot model for the observed fixed 27ch profile.

This produces slot labels, never a broadcast TS. Clock phase matches the fixed
upstream author's utils/multiplex_frame_pattern.py; native frame alignment still
needs known-input verification. Mode 3, GI 1/8, A QPSK 2/3 x1, B 64QAM 3/4 x12.
"""

from collections import Counter, deque
import json


def ch27_pattern():
    # Two reproduction buffers. S3 changes every 51 symbols; S4 follows
    # three 408-clock packet periods later. Only the selected S4 buffer pops.
    symbol_clocks, quarter_clocks = 9216, 51 * 9216
    frame_clocks = 204 * symbol_clocks
    buffers = (deque(), deque())
    bits = {"a": 0, "b": 0}
    carriers = {"a": 0, "b": 0}
    ordinals = {"a": 0, "b": 0}
    slots = []
    for clock in range(frame_clocks):
        if clock < frame_clocks:
            carrier = clock % symbol_clocks
            layer = "a" if carrier < 384 else ("b" if carrier < 4992 else None)
            if layer is not None:
                # Integer arithmetic: B_X,k = 2*(floor(k*S*R)-floor((k-1)*S*R)).
                numerator, denominator = (4, 3) if layer == "a" else (18, 4)
                k = carriers[layer] + 1
                bits[layer] += 2 * ((k * numerator // denominator)
                                    - ((k - 1) * numerator // denominator))
                carriers[layer] = k
                if bits[layer] >= 3264:
                    buffers[(clock // quarter_clocks) % 2].append((layer, ordinals[layer]))
                    ordinals[layer] += 1
                    bits[layer] -= 3264
        # Fixed author's reference uses the end of each 408-clock period,
        # including the two initial reads from the empty previous bank.
        if clock % 408 == 407:
            selected = ((clock - (3 * 408 - 1)) // quarter_clocks) % 2
            slots.append(buffers[selected].popleft() if buffers[selected] else None)
    if any(buffers) or any(bits.values()):
        raise ValueError("model did not drain a complete frame")
    return slots


def known_frame(slots, records):
    """Label known input records without replacing erasures with legal nulls.

    Return (kind, packet) pairs. A model-null has kind='null'; an RS erasure has
    kind='erasure'. Never alter successful packet PCR, CC, scrambling, or payload.
    No frame mapping is inferred from real input by this helper.
    """
    expected = Counter(slot[0] for slot in slots if slot is not None)
    if set(records) != set(expected) or any(len(records[layer]) != count for layer, count in expected.items()):
        raise ValueError("known frame requires exact layer word counts including erasures")
    output = []
    for slot in slots:
        if slot is None:
            output.append(("null", None))
            continue
        layer, index = slot
        packet = records[layer][index]
        if packet is None:
            output.append(("erasure", None))
        elif len(packet) != 188 or packet[0] != 0x47:
            raise ValueError("invalid known packet")
        else:
            output.append((layer, packet))
    return output


if __name__ == "__main__":
    slots = ch27_pattern()
    print(json.dumps({"kind": "reference_matched_slot_model_not_broadcast_ts",
                      "counts": dict(Counter("null" if x is None else x[0] for x in slots)),
                      "carrier_clock_origin": 0, "first_output_clock": 407,
                      "first_new_buffer_read_clock": 1223,
                      "original_multiplex_restored": False,
                      "slots": slots}, indent=2))
