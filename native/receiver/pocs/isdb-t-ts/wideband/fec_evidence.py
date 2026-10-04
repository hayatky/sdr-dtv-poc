# SPDX-License-Identifier: GPL-3.0-or-later
"""File-only pre-RS evidence; offsets are native word positions, not mux slots."""

import json
import numpy as np
import pmt
from gnuradio import gr


class WordEvidence(gr.sync_block):
    """Retain every consumed 204-byte word and its native frame/resync tags."""

    def __init__(self, directory, layer, on_words=None):
        gr.sync_block.__init__(self, name=f"layer_{layer}_word_evidence",
                               in_sig=[(np.uint8, 204)], out_sig=[(np.uint8, 204)])
        self.words_path = directory / f"layer_{layer}.rs204"
        self.tags_path = directory / f"layer_{layer}.tags.jsonl"
        self.words = self.words_path.open("xb")
        self.tags = self.tags_path.open("x", buffering=1)
        self.count = 0
        self.layer, self.on_words = layer, on_words

    def work(self, inputs, outputs):
        count = len(inputs[0])
        outputs[0][:count] = inputs[0]
        data = inputs[0].tobytes()
        self.words.write(data)
        events = []
        for tag in self.get_tags_in_window(0, 0, count):
            key = pmt.symbol_to_string(tag.key)
            if key in ("frame_begin", "resync"):
                events.append({"word_offset": int(tag.offset), "key": key})
                self.tags.write(json.dumps({"word_offset": int(tag.offset), "key": key,
                                           "native_value": pmt.write_string(tag.value),
                                           "original_frame_id": None}) + "\n")
        if self.on_words is not None:
            self.on_words(self.layer, self.count, data, events)
        self.count += count
        return count

    def close(self):
        self.words.close()
        self.tags.close()
