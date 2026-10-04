# SPDX-License-Identifier: GPL-3.0-or-later
"""Finite, file-only gr-isdbt wideband receiver. No device or RF blocks."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import stat
import sys
import threading
import time

import numpy as np
import pmt
from gnuradio import blocks, filter, gr
from gnuradio.fft import window
from gnuradio.filter import firdes
import hlfecwideband as isdbt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tmcc import decode as decode_tmcc  # noqa: E402
from fec_evidence import WordEvidence
from live_multiplex import LiveMultiplex

RATE = 512000000 / 63


class TagCounter(gr.sync_block):
    def __init__(self, vlen, multiplex=None):
        gr.sync_block.__init__(self, name="tmcc_tag_counter", in_sig=[(np.complex64, vlen)], out_sig=[])
        self.frames = 0
        self.resyncs = 0
        self.bits = []
        self.symbols = 0
        self.events = []
        self.multiplex = multiplex

    def work(self, input_items, output_items):
        count = len(input_items[0])
        for tag in self.get_tags_in_window(0, 0, count):
            key = pmt.symbol_to_string(tag.key)
            if self.multiplex is not None and key in ("frame_begin", "resync", "tmcc_bits"):
                self.multiplex.tmcc(int(tag.offset), key, pmt.symbol_to_string(tag.value) if key == "tmcc_bits" else None)
            if key in ("frame_begin", "resync"):
                self.events.append({"symbol_offset": int(tag.offset), "key": key})
            if key == "frame_begin":
                self.frames += 1
            elif key == "resync":
                self.resyncs += 1
            elif key == "tmcc_bits":
                self.bits.append(pmt.symbol_to_string(tag.value))
        self.symbols += count
        return count


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--stage", choices=("tmcc", "ts"), default="tmcc")
    parser.add_argument("--layers", choices=("a", "b", "ab"), default="b",
                        help="ab writes independent A/B TS; it does not reconstruct the multiplex")
    parser.add_argument("--optional-a", action="store_true",
                        help="With --layers ab, keep B service detection if A SI has no packets")
    parser.add_argument("--fec-evidence", action="store_true",
                        help="retain pre-RS words and native boundary tags for offline slot alignment")
    parser.add_argument("--multiplex", action="store_true",
                        help="parallel fixed-slot TS with canonical nulls and explicit TEI erasures")
    parser.add_argument("--stream-input", action="store_true",
                        help="read one bounded producer FIFO until EOF; provenance is supplied by producer")
    parser.add_argument("--timing-interpolate", action="store_true",
                        help="enable upstream OFDM sampling-clock interpolation")
    parser.add_argument("--skip-fir", action="store_true",
                        help="compare direct resampler output with the receive FIR path")
    parser.add_argument("--mode", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--gi", type=float, choices=(.25, .125, .0625, .03125), default=.0625)
    parser.add_argument("--segments-a", type=int, default=1)
    parser.add_argument("--segments-b", type=int, default=12)
    parser.add_argument("--modulation-a", type=int, default=4)
    parser.add_argument("--modulation-b", type=int, default=64)
    parser.add_argument("--interleave-a", type=int, default=4)
    parser.add_argument("--interleave-b", type=int, default=2)
    parser.add_argument("--rate-a", type=int, choices=range(5), default=1)
    parser.add_argument("--partial-reception", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--rate-b", type=int, default=2, help="0=1/2, 1=2/3, 2=3/4, 3=5/6, 4=7/8")
    args = parser.parse_args()
    if args.optional_a and (args.layers != "ab" or args.multiplex):
        parser.error("--optional-a requires --layers ab without --multiplex")
    if args.multiplex:
        if args.stage != "ts" or args.layers != "ab" or not args.fec_evidence:
            parser.error("--multiplex requires --stage ts --layers ab --fec-evidence")
        if not getattr(isdbt, "initial_frame_tag_alignment", False):
            parser.error("--multiplex requires the rebuilt issue39-mux library")
    if args.layers == "ab":
        if not getattr(isdbt, "viterbi_instance_storage", False):
            parser.error("A/B requires the rebuilt issue39 library with instance-local Viterbi storage")
        if args.multiplex and (args.mode, args.gi, args.segments_a, args.segments_b, args.interleave_a,
                args.interleave_b, args.rate_b) != (3, .125, 1, 12, 4, 2, 2):
            parser.error("Multiplex reconstruction supports only the observed Mode3 GI1/8 1+12 segment profile")
    if args.stream_input:
        if not stat.S_ISFIFO(args.input.stat().st_mode):
            parser.error("stream input must be a FIFO")
        size = None
    else:
        if not args.input.is_file():
            parser.error("input file is missing")
        size = args.input.stat().st_size
        if size == 0 or size % 8:
            parser.error("input must be nonempty cf32_le complex samples")
    if args.layers == "a":
        if (args.segments_a, args.segments_b) != (13, 0) or args.modulation_a not in (4, 16, 64):
            parser.error("A-only requires 13 segments and QPSK/16QAM/64QAM")
    else:
        if args.segments_a + args.segments_b != 13 or args.segments_a < 1 or args.segments_b < 1:
            parser.error("A+B must occupy exactly 13 segments")
        if args.modulation_a != 4 or args.modulation_b != 64 or args.rate_a != 1 or args.rate_b not in range(5):
            parser.error("A+B supports A=QPSK2/3 and B=64QAM with a valid code-rate index")
    if args.output_dir.exists():
        parser.error("output directory already exists")

    args.output_dir.mkdir(parents=True)
    started = time.monotonic()
    cpu_started = time.process_time()
    report = {"schema_version": 1, "source": "file", "stage": args.stage,
              "implementation": {"upstream_commit": "56b2556c14ecc5d710070f969fda7a2deae65d8b",
                                 "image": os.environ.get("HLFEC_WIDEBAND_IMAGE"),
                                 "image_id": os.environ.get("HLFEC_WIDEBAND_IMAGE_ID"),
                                 "script_sha256": sha256(Path(__file__))},
              "input": {"sha256": None if args.stream_input else sha256(args.input),
                        "bytes": size, "samples": None if size is None else size // 8,
                        "kind": "fifo" if args.stream_input else "file",
                        "format": "cf32_le", "sample_rate": {"numerator": 512000000, "denominator": 63}},
              "parameters": {"mode": args.mode, "gi": args.gi,
                             "timing_interpolate": args.timing_interpolate,
                             "skip_fir": args.skip_fir,
                             "segments_a": args.segments_a,
                             "segments_b": args.segments_b, "modulation_a": args.modulation_a,
                             "modulation_b": args.modulation_b, "interleave_a": args.interleave_a,
                             "interleave_b": args.interleave_b, "rate_b": args.rate_b,
                             "rate_a": args.rate_a, "partial_reception": args.partial_reception},
              "layers_requested": args.layers, "multiplex_slot_order_reconstructed": False,
              "original_bytes_fully_recovered": False,
              "status": "running"}
    ts_path = args.output_dir / ("layer_a.ts" if args.layers == "a" else "layer_b.ts")
    chains = {}
    evidence = {}
    sinks = []
    multiplex = None
    multiplex_finished = False
    try:
        top = gr.top_block("wideband_file_receiver")
        if args.multiplex:
            multiplex = LiveMultiplex(args.output_dir / "multiplex", top.stop)
        source = blocks.file_source(gr.sizeof_gr_complex, str(args.input), False)
        taps = firdes.low_pass(1.0, RATE, 2.9e6, .5e6, window.WIN_HAMMING)
        fir = filter.fir_filter_ccf(1, taps)
        sync = isdbt.ofdm_synchronization(args.mode, args.gi, args.timing_interpolate)
        tmcc = isdbt.tmcc_decoder(args.mode, False)
        data_carriers = 13 * 96 * 2 ** (args.mode - 1)
        tags = TagCounter(data_carriers, multiplex)
        if args.skip_fir:
            top.connect(source, sync, tmcc)
        else:
            top.connect(source, fir, sync, tmcc)
        top.connect(tmcc, tags)
        if args.stage == "ts":
            freq = isdbt.frequency_deinterleaver(args.partial_reception, args.mode)
            timed = isdbt.time_deinterleaver(args.mode, args.segments_a, args.interleave_a,
                                             args.segments_b, args.interleave_b, 0, 0)
            demap = isdbt.symbol_demapper(args.mode, args.segments_a, args.modulation_a,
                                          args.segments_b, args.modulation_b, 0, 64)
            top.connect(tmcc, freq, timed, demap)
            if args.layers == "b":
                top.connect((demap, 0), blocks.null_sink(args.segments_a * 96 * 2 ** (args.mode - 1)))
            if args.layers == "a":
                # Native work() reads output_items[1] even when B is unused.
                # Its signature reserves one segment for an unused output.
                top.connect((demap, 1), blocks.null_sink(96 * 2 ** (args.mode - 1)))
            for layer, port, segments, modulation, rate in (
                    ("a", 0, args.segments_a, args.modulation_a, args.rate_a),
                    ("b", 1, args.segments_b, args.modulation_b, args.rate_b)):
                if layer not in args.layers or segments == 0:
                    continue
                bit = isdbt.bit_deinterleaver(args.mode, segments, modulation)
                vit = isdbt.viterbi_decoder(modulation, rate)
                byte = isdbt.byte_deinterleaver()
                energy = isdbt.energy_descrambler()
                decoder = isdbt.reed_solomon_dec_isdbt()
                stream = blocks.vector_to_stream(gr.sizeof_char, 188)
                sink = blocks.file_sink(gr.sizeof_char, str(args.output_dir / f"layer_{layer}.ts"), False)
                if layer == "a" and args.segments_a == 1:
                    # A has only 64 words/frame in this profile. The fixed
                    # native blocks inspect the first frame tag per work call.
                    vit.set_max_noutput_items(32 * 204)
                    byte.set_max_noutput_items(32)
                    energy.set_max_noutput_items(32)
                top.connect((demap, port), bit, vit, byte, energy)
                if args.fec_evidence:
                    observer = WordEvidence(args.output_dir, layer, multiplex.words if multiplex is not None else None)
                    evidence[layer] = observer
                    top.connect(energy, observer, decoder)
                else:
                    top.connect(energy, decoder)
                top.connect((vit, 1), blocks.null_sink(gr.sizeof_float))
                top.connect((decoder, 1), blocks.null_sink(gr.sizeof_float))
                top.connect((decoder, 0), stream, sink)
                chains[layer] = decoder
                sinks.append(sink)
            rs = chains["a" if args.layers == "a" else "b"]
        timeline = []
        monitor_done = threading.Event()
        monitor_errors = []
        if args.stream_input:
            def sample_progress():
                # Flush every sample so native crashes retain preceding diagnostics.
                previous = None
                with (args.output_dir / "timeline.jsonl").open("w", buffering=1) as progress:
                    while not monitor_done.wait(1):
                        row = {"utc": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.monotonic() - started,
                                     "input_samples": source.nitems_written(0),
                                     "tmcc_frames": tags.frames, "resync_tags": tags.resyncs,
                                     "rs_input_words": rs.nitems_read(0) if args.stage == "ts" else None,
                                     "ts_packets": rs.nitems_written(0) if args.stage == "ts" else None,
                                     "ts_bytes": ts_path.stat().st_size if ts_path.exists() else 0,
                                     "layer_rs": {layer: {"input_words": decoder.nitems_read(0),
                                                          "output_packets": decoder.nitems_written(0)}
                                                  for layer, decoder in chains.items()},
                                     "multiplex": None if multiplex is None else {
                                         "frames": multiplex.frames, "packets": multiplex.packets,
                                         "rs_erasure_slots": dict(multiplex.erasures),
                                         "pending_frames_peak": multiplex.pending_peak},
                                     "cpu_seconds": time.process_time() - cpu_started,
                                     "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
                        row["input_bytes"] = row["input_samples"] * 8
                        row["rs_difference_note"] = "live estimate from non-atomic consumed/output snapshots; excludes unconsumed pipeline tail"
                        row["rs_omitted_words"] = (row["rs_input_words"] - row["ts_packets"]) if args.stage == "ts" else None
                        row["rss_kib"] = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024
                        if previous is not None:
                            window_seconds = row["elapsed_seconds"] - previous["elapsed_seconds"]
                            row["window_seconds"] = window_seconds
                            row["cpu_percent"] = 100 * (row["cpu_seconds"] - previous["cpu_seconds"]) / window_seconds
                            for key in ("input_bytes", "ts_bytes", "tmcc_frames", "resync_tags"):
                                row[key + "_window"] = row[key] - previous[key]
                            if args.stage == "ts":
                                row["rs_input_words_window"] = row["rs_input_words"] - previous["rs_input_words"]
                                row["rs_omitted_words_window"] = row["rs_omitted_words"] - previous["rs_omitted_words"]
                        previous = row
                        timeline.append(row)
                        progress.write(json.dumps(row) + "\n")
            def guarded_progress():
                try:
                    sample_progress()
                except Exception as exc:
                    monitor_errors.append(f"{type(exc).__name__}: {exc}")
                    try:
                        (args.output_dir / "monitor-error.json").write_text(json.dumps({
                            "utc": datetime.now(timezone.utc).isoformat(),
                            "error": monitor_errors[-1]}) + "\n")
                    finally:
                        # Wake top.run(), then propagate through its normal report path.
                        top.stop()
            monitor = threading.Thread(target=guarded_progress, daemon=True)
            monitor.start()
        try:
            top.run()
        finally:
            if args.stream_input:
                monitor_done.set()
                monitor.join(timeout=2)
                report["timeline"] = timeline
        if monitor_errors:
            raise RuntimeError("native progress monitor failed: " + monitor_errors[0])
        if multiplex is not None:
            report["multiplex"] = multiplex.finish()
            report["multiplex_slot_order_reconstructed"] = True
            multiplex_finished = True
        decoded = []
        failures = 0
        for bits in tags.bits:
            try:
                decoded.append(decode_tmcc(bits, args.mode))
            except ValueError:
                failures += 1
        report["tmcc"] = {"native_frames": tags.frames, "valid_frames": len(decoded),
                          "independent_parity_failures": failures, "frames": decoded,
                          "resync_tags": tags.resyncs, "output_symbols": tags.symbols,
                          "native_events": tags.events}
        rates = ("1/2", "2/3", "3/4", "5/6", "7/8")
        def unused(layer):
            return (layer["modulation"] == "unused" and layer["code_rate"] == "unused"
                    and layer["segments_code"] == 15)
        def configured(frame):
            a, b, c = (frame["current_layers"][key] for key in "ABC")
            return (frame["partial_reception"] == args.partial_reception
                and a["segments"] == args.segments_a
                and a["modulation"] == {4: "QPSK", 16: "16QAM", 64: "64QAM"}[args.modulation_a]
                and a["code_rate"] == rates[args.rate_a]
                and a["interleave_length"] == args.interleave_a
                and (unused(b) if args.layers == "a" else
                     b["segments"] == args.segments_b and b["modulation"] == "64QAM"
                     and b["interleave_length"] == args.interleave_b and b["code_rate"] == rates[args.rate_b])
                and unused(c))
        report["tmcc"]["configured_layers_match"] = all(map(configured, decoded)) if decoded else None
        if args.stage == "ts":
            for sink in sinks:
                sink.close()
            for observer in evidence.values():
                observer.close()
            report["layer_outputs"] = {}
            for layer, decoder in chains.items():
                path = args.output_dir / f"layer_{layer}.ts"
                item = {"rs_input_words_consumed": decoder.nitems_read(0),
                        "output_packets": decoder.nitems_written(0),
                        "uncorrectable_words": decoder.nitems_read(0) - decoder.nitems_written(0),
                        "bytes": path.stat().st_size, "sha256": sha256(path),
                        "original_frame_alignment": "unverified"}
                if layer in evidence:
                    observer = evidence[layer]
                    item["pre_rs"] = {"words": observer.count,
                                      "sha256": sha256(observer.words_path),
                                      "tags_sha256": sha256(observer.tags_path),
                                      "note": "includes words offered but possibly not consumed at EOF; native frame tags are not original frame IDs"}
                report["layer_outputs"][layer] = item
            report["rs"] = {"input_words_consumed": rs.nitems_read(0),
                            "output_packets": rs.nitems_written(0),
                            "omitted_words": rs.nitems_read(0) - rs.nitems_written(0),
                            "uncorrectable_words": rs.nitems_read(0) - rs.nitems_written(0),
                            "uncorrectable_words_method": "final consumed/output difference: fixed native decoder consumes rejected words without output",
                            "omission_note": "final difference counts rejected consumed RS words; excludes unconsumed pipeline tail",
                            "unconsumed_tail_words": None}
            report["output"] = {"bytes": ts_path.stat().st_size, "sha256": sha256(ts_path)}
        if not decoded:
            report["status"] = "unverified_no_valid_tmcc"
        elif report["tmcc"]["configured_layers_match"] is False:
            report["status"] = "failed_tmcc_configuration_mismatch"
        elif args.stage == "ts" and any(
                decoder.nitems_written(0) == 0 for layer, decoder in chains.items()
                if not (args.optional_a and layer == "a")):
            report["status"] = "unverified_no_ts_packets"
        else:
            report["status"] = "completed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if multiplex is not None and not multiplex_finished:
            try:
                multiplex.finish()
            except Exception as exc:
                report["multiplex_error"] = f"{type(exc).__name__}: {exc}"
        for observer in evidence.values():
            observer.close()
        report["wall_seconds"] = time.monotonic() - started
        report["cpu_seconds"] = time.process_time() - cpu_started
        report["max_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        (args.output_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
