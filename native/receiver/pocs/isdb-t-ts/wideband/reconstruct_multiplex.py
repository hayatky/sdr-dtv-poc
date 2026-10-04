# SPDX-License-Identifier: GPL-3.0-or-later
"""Recover fixed-profile broadcast slot order from audited indexed A/B records.

Successful packets are byte-exact. RF-omitted null payloads are canonical;
uncorrectable RS slots use TEI-marked null placeholders with separate provenance.
Only a single uninterrupted TMCC epoch is supported. Never compact erasures.
"""

import argparse
import hashlib
import json
from pathlib import Path

from multiplex_model import ch27_pattern

NULL = bytes([0x47, 0x1f, 0xff, 0x10]) + bytes([0xff]) * 184
ERASURE = bytes([0x47, 0x9f, 0xff, 0x10]) + bytes([0xff]) * 184


def frame_packets(pattern, records, statuses):
    output, gaps = [], []
    for slot, item in enumerate(pattern):
        if item is None:
            output.append(NULL)
            continue
        layer, ordinal = item
        if statuses[layer][ordinal] == 255:
            output.append(ERASURE)
            gaps.append({"slot": slot, "layer": layer, "ordinal": ordinal})
        else:
            packet = records[layer][ordinal * 188:(ordinal + 1) * 188]
            if len(packet) != 188 or packet[0] != 0x47:
                raise ValueError("accepted indexed record has invalid sync/size")
            output.append(packet)
    return b"".join(output), gaps


def boundaries(path, count):
    tags = [json.loads(line) for line in path.read_text().splitlines()]
    offsets = [row["word_offset"] for row in tags if row["key"] == "frame_begin"]
    if not offsets or any(y - x != count for x, y in zip(offsets, offsets[1:])):
        raise ValueError("layer frame boundaries are incomplete or discontinuous")
    return offsets


def reconstruct(receiver_dir, audits, output):
    report = json.loads((receiver_dir / "result.json").read_text())
    tmcc = report["tmcc"]
    if report["status"] != "completed" or report["layers_requested"] != "ab" or not tmcc["configured_layers_match"]:
        raise ValueError("expected completed matching A/B receiver evidence")
    parameters = report["parameters"]
    keys = ("mode", "gi", "segments_a", "segments_b", "modulation_a", "modulation_b", "interleave_a", "interleave_b", "rate_b")
    if tuple(parameters[key] for key in keys) != (3, .125, 1, 12, 4, 64, 4, 2, 2):
        raise ValueError("slot reconstruction supports only the verified ch27 profile")
    if tmcc["independent_parity_failures"] or tmcc["valid_frames"] != tmcc["native_frames"]:
        raise ValueError("TMCC frame validation is incomplete")
    frame_symbols = [row["symbol_offset"] for row in tmcc["native_events"] if row["key"] == "frame_begin"]
    resync_symbols = [row["symbol_offset"] for row in tmcc["native_events"] if row["key"] == "resync"]
    if (len(frame_symbols) != tmcc["native_frames"]
            or any(y - x != 204 for x, y in zip(frame_symbols, frame_symbols[1:]))
            or resync_symbols != frame_symbols[:1]):
        raise ValueError("only an uninterrupted epoch with one initial resync is supported")
    for layer in ("a", "b"):
        with (receiver_dir / f"layer_{layer}.tags.jsonl").open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != report["layer_outputs"][layer]["pre_rs"]["tags_sha256"]:
                raise ValueError("layer frame tags hash does not match receiver evidence")
    offsets = {layer: boundaries(receiver_dir / f"layer_{layer}.tags.jsonl", count)
               for layer, count in (("a", 64), ("b", 2592))}
    if any(len(value) != len(frame_symbols) for value in offsets.values()):
        raise ValueError("native layer frame tags do not account for every TMCC frame")
    for layer in ("a", "b"):
        audit = json.loads((audits[layer] / "result.json").read_text())
        native = report["layer_outputs"][layer]
        if (not audit["native_ts_matches"] or audit["native_ts_sha256"] != native["sha256"]
                or audit["input_sha256"] != native["pre_rs"]["sha256"]
                or audit["consumed_words"] != native["rs_input_words_consumed"]):
            raise ValueError("indexed audit does not belong to this receiver output")
        for name, width, hash_key in (("indexed188.bin", 188, "indexed_sha256"), ("rs-status.i8", 1, "status_sha256")):
            path = audits[layer] / name
            with path.open("rb") as stream:
                if (path.stat().st_size != audit["consumed_words"] * width
                        or hashlib.file_digest(stream, "sha256").hexdigest() != audit[hash_key]):
                    raise ValueError("indexed evidence hash/length mismatch")
    if output.exists():
        raise ValueError("output already exists")
    output.mkdir()
    pattern = ch27_pattern()
    handles = {layer: ((audits[layer] / "indexed188.bin").open("rb"), (audits[layer] / "rs-status.i8").open("rb")) for layer in ("a", "b")}
    digest, count, missing = hashlib.sha256(), 0, {"a": 0, "b": 0}
    try:
        with (output / "multiplex.ts").open("xb") as ts, (output / "frames.jsonl").open("x") as provenance:
            # A has a three-layer-frame delay, B two: A boundary i pairs with
            # B boundary i-1. Trim the first three received boundaries and the
            # incomplete final frame; do not invent initialization payloads.
            for index in range(3, len(frame_symbols) - 1):
                records, statuses = {}, {}
                starts = {"a": offsets["a"][index], "b": offsets["b"][index - 1]}
                for layer, words in (("a", 64), ("b", 2592)):
                    packets, status = handles[layer]
                    packets.seek(starts[layer] * 188)
                    status.seek(starts[layer])
                    records[layer], statuses[layer] = packets.read(words * 188), status.read(words)
                    if len(records[layer]) != words * 188 or len(statuses[layer]) != words:
                        raise ValueError("complete boundary exceeds consumed evidence")
                data, gaps = frame_packets(pattern, records, statuses)
                ts.write(data)
                digest.update(data)
                count += 1
                for gap in gaps:
                    missing[gap["layer"]] += 1
                provenance.write(json.dumps({"output_frame": count - 1,
                                            "relative_source_layer_frame": index - 3,
                                            "a_tmcc_symbol_offset": frame_symbols[index],
                                            "b_tmcc_symbol_offset": frame_symbols[index - 1],
                                            "layer_start_words": starts, "rs_erasure_slots": gaps}) + "\n")
        if not count:
            raise ValueError("no complete steady frames")
    finally:
        for pair in handles.values():
            for stream in pair:
                stream.close()
    result = {"kind": "broadcast_multiplex_slot_reconstruction_with_explicit_erasures",
              "multiplex_slot_order_reconstructed": True, "original_bytes_fully_recovered": False,
              "frames": count, "packets": count * 4608, "sha256": digest.hexdigest(),
              "rs_erasure_slots": missing, "canonical_rf_omitted_nulls": count * 1952,
              "alignment_basis": "fixed-author slot oracle + known TMCC/time/bit/Viterbi/byte/PRBS frame IDs",
              "limits": "single epoch only; OFDM synchronizer not part of known-input probe; null payload unavailable; RS miscorrection possible"}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receiver", type=Path)
    parser.add_argument("audit_a", type=Path)
    parser.add_argument("audit_b", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(reconstruct(args.receiver, {"a": args.audit_a, "b": args.audit_b}, args.output), indent=2))
