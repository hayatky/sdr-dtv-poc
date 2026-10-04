# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded single-epoch slot reconstruction parallel to the input pipeline.

The two native pre-RS observers and TMCC observer submit immutable evidence.
One worker retains complete layer frames, decodes RS without compaction, and
writes the fixed-author slot pattern. A failed worker stops its owning pipeline.
"""

import hashlib
import json
from pathlib import Path
import queue
import threading
import time

from multiplex_model import ch27_pattern
from reconstruct_multiplex import frame_packets


class LayerFrames:
    def __init__(self, count):
        self.count = count
        self.origin = self.offered = self.index = 0
        self.data = bytearray()
        self.boundaries = []
        self.tags = 0
        self.previous_tag = None

    def add(self, offset, data, events):
        if offset != self.offered or len(data) % 204:
            raise ValueError("noncontiguous pre-RS word input")
        if len(self.data) + len(data) > 16 * self.count * 204:
            raise ValueError("unframed word backlog exceeds 16 layer frames")
        self.data.extend(data)
        self.offered += len(data) // 204
        for event in events:
            if event["key"] != "frame_begin":
                continue
            word = event["word_offset"]
            if not offset <= word < self.offered:
                raise ValueError("frame tag lies outside its submitted word chunk")
            if self.previous_tag is not None and word - self.previous_tag != self.count:
                raise ValueError("discontinuous native layer frame tags")
            self.previous_tag = word
            self.tags += 1
            self.boundaries.append(word)
        if self.boundaries and self.origin < self.boundaries[0]:
            discard = self.boundaries[0] - self.origin
            del self.data[:discard * 204]
            self.origin += discard
        complete = []
        while len(self.boundaries) >= 2 and self.offered >= self.boundaries[1]:
            start, stop = self.boundaries[:2]
            if start != self.origin or stop - start != self.count:
                raise ValueError("layer buffer origin disagrees with frame tags")
            complete.append((self.index, bytes(self.data[:self.count * 204]), start))
            self.index += 1
            del self.data[:self.count * 204]
            self.origin = stop
            self.boundaries.pop(0)
        return complete


class LiveMultiplex:
    def __init__(self, directory, stop_pipeline):
        self.directory = Path(directory)
        self.directory.mkdir()
        self.stop_pipeline = stop_pipeline
        self.incoming = queue.Queue(maxsize=32)
        self.error = None
        self.abort = threading.Event()
        self.state_lock = threading.Lock()
        self.ending = False
        self.frames = self.packets = 0
        self.erasures = {"a": 0, "b": 0}
        self.pending_peak = 0
        self.result = None
        self.thread = threading.Thread(target=self._guarded_run, name="multiplex-writer", daemon=True)
        self.thread.start()

    def submit(self, item):
        while self.error is None:
            try:
                self.incoming.put(item, timeout=.25)
                return
            except queue.Full:
                pass

    def words(self, layer, offset, data, events):
        self.submit(("words", layer, offset, data, events))

    def tmcc(self, symbol, key, value=None):
        self.submit(("tmcc", symbol, key, value))

    def _fail(self, message):
        with self.state_lock:
            self.abort.set()
            if self.error is None:
                self.error = message
            # Timeout may race with the very last successful publication.
            (self.directory / "result.json").unlink(missing_ok=True)
            (self.directory / "error.json").write_text(json.dumps({"partial": True, "error": self.error}) + "\n")

    def finish(self, timeout=30):
        deadline = time.monotonic() + timeout
        try:
            while not self.ending and self.error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("multiplex worker did not finish within deadline")
                try:
                    self.incoming.put(("end",), timeout=min(.25, remaining))
                    self.ending = True
                except queue.Full:
                    pass
            self.thread.join(timeout=max(0, deadline - time.monotonic()))
            if self.thread.is_alive():
                raise TimeoutError("multiplex worker did not finish within deadline")
        except TimeoutError as exc:
            try:
                self._fail(str(exc))
            finally:
                self.stop_pipeline()
            raise RuntimeError(str(exc)) from exc
        if self.error is not None:
            raise RuntimeError("multiplex worker failed: " + self.error)
        return self.result

    def _guarded_run(self):
        try:
            self._run()
        except Exception as exc:
            try:
                self._fail(f"{type(exc).__name__}: {exc}")
            finally:
                self.stop_pipeline()

    def _run(self):
        from audit_rs_words import RS, decode_word
        # Import the same independent protected-TMCC checker as the receiver.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from tmcc import decode
        layers = {"a": LayerFrames(64), "b": LayerFrames(2592)}
        pending = {"a": {}, "b": {}}
        tmcc_symbols, valid_symbols, resync_symbols = [], set(), []
        next_frame = 3
        pattern, digest = ch27_pattern(), hashlib.sha256()
        rs = RS()
        try:
            with (self.directory / "multiplex.ts").open("xb") as ts, (self.directory / "frames.jsonl").open("x", buffering=1) as provenance:
                while True:
                    if self.abort.is_set():
                        raise RuntimeError("multiplex worker aborted")
                    try:
                        item = self.incoming.get(timeout=.25)
                    except queue.Empty:
                        continue
                    if item[0] == "end":
                        break
                    if item[0] == "words":
                        _, layer, offset, data, events = item
                        for index, words, start in layers[layer].add(offset, data, events):
                            if index >= (3 if layer == "a" else 2):
                                pending[layer][index] = (words, start)
                    else:
                        _, symbol, key, value = item
                        if key == "frame_begin":
                            if tmcc_symbols and symbol - tmcc_symbols[-1] != 204:
                                raise ValueError("TMCC frame gap")
                            tmcc_symbols.append(symbol)
                        elif key == "resync":
                            resync_symbols.append(symbol)
                            if len(resync_symbols) > 1:
                                raise ValueError("midstream resync cannot be assigned to the original epoch")
                        elif key == "tmcc_bits":
                            frame = decode(value, 3)
                            a, b, c = (frame["current_layers"][x] for x in "ABC")
                            if ((a["segments"], a["modulation"], a["code_rate"], a["interleave_length"],
                                 b["segments"], b["modulation"], b["code_rate"], b["interleave_length"])
                                    != (1, "QPSK", "2/3", 4, 12, "64QAM", "3/4", 2)
                                    or (c["modulation"], c["code_rate"], c["interleave_code"], c["segments_code"])
                                    != ("unused", "unused", 7, 15)):
                                raise ValueError("TMCC profile changed")
                            valid_symbols.add(symbol)
                    while (next_frame in pending["a"] and next_frame - 1 in pending["b"]
                           and len(tmcc_symbols) > next_frame + 1
                           and all(symbol in valid_symbols for symbol in tmcc_symbols[:next_frame + 2])
                           and resync_symbols == tmcc_symbols[:1]):
                        records, statuses, starts = {}, {}, {}
                        for layer, index in (("a", next_frame), ("b", next_frame - 1)):
                            words, starts[layer] = pending[layer].pop(index)
                            decoded = [decode_word(rs, words[i:i + 204]) for i in range(0, len(words), 204)]
                            records[layer] = b"".join(packet for _, packet in decoded)
                            statuses[layer] = bytes(status & 255 for status, _ in decoded)
                        output, gaps = frame_packets(pattern, records, statuses)
                        ts.write(output)
                        ts.flush()
                        digest.update(output)
                        provenance.write(json.dumps({"output_frame": self.frames,
                            "relative_source_layer_frame": next_frame - 3,
                            "a_tmcc_symbol_offset": tmcc_symbols[next_frame],
                            "b_tmcc_symbol_offset": tmcc_symbols[next_frame - 1],
                            "layer_start_words": starts, "rs_erasure_slots": gaps}) + "\n")
                        self.frames += 1
                        self.packets += 4608
                        for gap in gaps:
                            self.erasures[gap["layer"]] += 1
                        next_frame += 1
                    self.pending_peak = max(self.pending_peak, *(len(value) for value in pending.values()))
                    if self.pending_peak > 16:
                        raise ValueError("layer or TMCC backlog exceeds 16 complete frames")
                if (not self.frames or resync_symbols != tmcc_symbols[:1]
                        or valid_symbols != set(tmcc_symbols)
                        or any(layer.tags != len(tmcc_symbols) for layer in layers.values())
                        or self.frames != len(tmcc_symbols) - 4):
                    raise ValueError("final TMCC/layer frame accounting is incomplete")
        finally:
            rs.close()
        self.result = {"kind": "parallel_broadcast_slot_reconstruction_with_explicit_erasures",
            "multiplex_slot_order_reconstructed": True, "original_bytes_fully_recovered": False,
            "frames": self.frames, "packets": self.packets, "sha256": digest.hexdigest(),
            "rs_erasure_slots": self.erasures, "canonical_rf_omitted_nulls": self.frames * 1952,
            "pending_frames_peak": self.pending_peak, "partial": False,
            "limits": "single epoch only; native RS acceptance is not proof against miscorrection; RF null payload unavailable"}
        with self.state_lock:
            if self.abort.is_set():
                raise RuntimeError("multiplex worker aborted before successful publication")
            (self.directory / "result.json").write_text(json.dumps(self.result, indent=2) + "\n")
