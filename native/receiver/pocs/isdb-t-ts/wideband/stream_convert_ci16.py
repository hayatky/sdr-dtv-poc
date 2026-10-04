# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert a bounded 6.4 MS/s ci16_le stream to 512/63 MS/s cf32_le.

The producer closes input at EOF. A 63-sample lookahead keeps the FIR state
continuous across chunks. Capture provenance belongs to the producer job.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import resource
import sys
import time

import numpy as np
from scipy.signal import resample_poly

UP, DOWN, GUARD = 80, 63, 63


def convert(input_path, output_path, chunk_samples, max_samples, async_fifo=False, progress_path=None):
    if chunk_samples <= 0 or chunk_samples % DOWN or max_samples <= 0:
        raise ValueError("chunk must be positive and divisible by 63; max_samples must be positive")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input and output must differ")
    if async_fifo and not output_path.is_fifo():
        raise ValueError("async output requires a FIFO")
    pending = bytearray()
    left = b""
    input_hash, output_hash = hashlib.sha256(), hashlib.sha256()
    input_bytes = output_bytes = 0
    started = time.monotonic()
    cpu_started = time.process_time()
    pending_write = None
    last_progress = started

    def write_all(block):
        view = memoryview(block)
        while view:
            written = target.write(view)
            if not written:
                raise OSError("FIFO writer made no progress")
            view = view[written:]

    def emit(body, right=b""):
        nonlocal left, output_bytes, pending_write, last_progress
        extended = np.frombuffer(left + body + right, dtype="<i2").reshape(-1, 2)
        iq = (extended[:, 0].astype(np.float32) + 1j * extended[:, 1].astype(np.float32)) / 2048.0
        converted = resample_poly(iq.astype(np.complex64), UP, DOWN)
        skip = len(left) // 4 * UP // DOWN
        count = (len(body) // 4 * UP + DOWN - 1) // DOWN
        block = converted[skip:skip + count].astype("<c8", copy=False).tobytes()
        if executor is None:
            write_all(block)
        else:
            # Let the previous FIFO write run while the next block is converted.
            # One outstanding write bounds in-memory backlog to one block.
            if pending_write is not None:
                pending_write.result()
            pending_write = executor.submit(write_all, block)
        output_hash.update(block)
        output_bytes += len(block)
        left = body[-GUARD * 4:]
        now = time.monotonic()
        if progress_path is not None and now - last_progress >= 1:
            row = {"elapsed_seconds": now-started, "input_bytes": input_bytes,
                   "output_bytes": output_bytes, "cpu_seconds": time.process_time()-cpu_started,
                   "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   "output_note": "includes at most one outstanding asynchronous write"}
            temporary = progress_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(row) + '\n')
            temporary.replace(progress_path)
            last_progress = now

    output_mode = "wb" if output_path.is_fifo() else "xb"
    with input_path.open("rb", buffering=0) as source, output_path.open(output_mode, buffering=0) as target:
        executor = ThreadPoolExecutor(max_workers=1) if async_fifo else None
        try:
            while block := source.read(chunk_samples * 4):
                input_hash.update(block)
                input_bytes += len(block)
                if input_bytes > max_samples * 4:
                    raise ValueError("input exceeds sample limit")
                pending.extend(block)
                while len(pending) >= (chunk_samples + GUARD) * 4:
                    emit(bytes(pending[:chunk_samples * 4]), bytes(pending[chunk_samples * 4:(chunk_samples + GUARD) * 4]))
                    del pending[:chunk_samples * 4]
            if input_bytes == 0 or len(pending) % 4:
                raise ValueError("input must contain complete nonempty ci16_le samples")
            emit(bytes(pending))
            if pending_write is not None:
                pending_write.result()
        finally:
            if executor is not None:
                executor.shutdown(wait=True)
    return {"input_bytes": input_bytes, "input_sha256": input_hash.hexdigest(),
            "output_bytes": output_bytes, "output_sha256": output_hash.hexdigest(),
            "chunk_samples": chunk_samples, "guard_samples": GUARD,
            "async_fifo": async_fifo,
            "max_samples": max_samples, "wall_seconds": time.monotonic() - started,
            "cpu_seconds": time.process_time() - cpu_started,
            "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--chunk-samples", type=int, default=630_000)
    parser.add_argument("--max-samples", type=int, default=64_000_000)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--async-fifo", action="store_true")
    args = parser.parse_args()
    print(json.dumps(convert(args.input, args.output, args.chunk_samples, args.max_samples,
                             args.async_fifo, args.progress)))
