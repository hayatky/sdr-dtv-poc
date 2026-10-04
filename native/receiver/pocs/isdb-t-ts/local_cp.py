# SPDX-License-Identifier: GPL-3.0-or-later
"""Windowed CP phase evidence; no OFDM lock, clock or packet-loss claim."""
import argparse
import json
from pathlib import Path

import numpy as np


RATE = 64000000 / 63
GUARDS = (.25, .125, .0625, .03125)


def phase_step(previous, current, period):
    return (current - previous + period / 2) % period - period / 2


def probe_array(samples, window_samples=65536, stride_samples=65536, modes=(1, 2, 3)):
    if window_samples < 8192 or stride_samples < 8192:
        raise ValueError('window and stride must be >= 8192')
    x = np.asarray(samples, dtype=np.complex128)
    if not np.isfinite(x).all():
        raise ValueError('nonfinite IQ')
    if len(x) < window_samples:
        raise ValueError('input shorter than diagnostic window')
    x = x - np.mean(x)
    groups = []
    for mode in modes:
        fft = 128 * 2**mode
        for gi in GUARDS:
            cp = int(fft * gi)
            period = fft + cp
            windows = []
            previous = None
            for start in range(0, len(x) - window_samples + 1, stride_samples):
                y = x[start:start + window_samples]
                a, b = y[:-fft], y[fft:]

                def rolling(v):
                    cumulative = np.concatenate(([0], np.cumsum(v)))
                    return cumulative[cp:] - cumulative[:-cp]

                cross = rolling(a * np.conj(b))
                power = np.sqrt(np.maximum(rolling(abs(a)**2) * rolling(abs(b)**2), 0))
                rho = abs(cross) / np.maximum(power, 1e-30)
                count = len(rho) // period
                folded = np.mean(rho[:count*period].reshape(count, period), axis=0)
                index = int(np.argmax(folded))
                phase = (start + index) % period
                peak = float(folded[index])
                median = float(np.median(folded))
                row = {'start_sample': start, 'start_seconds': start / RATE,
                       'folded_symbols': count, 'phase_samples': phase,
                       'peak': peak, 'median': median, 'contrast': peak - median,
                       'phase_step_samples': None if previous is None else phase_step(previous, phase, period)}
                windows.append(row)
                previous = phase
            groups.append({'mode': mode, 'gi': gi, 'fft': fft, 'cp': cp,
                           'period': period, 'windows': windows})
    return {'sample_rate': '64000000/63', 'window_samples': window_samples,
            'stride_samples': stride_samples, 'groups': groups,
            'limits': 'Windowed magnitude correlation; narrowband tones can raise baseline. Phase movement may reflect SFO or channel changes; jumps may reflect dropped samples or other causes. No RF source identification or lock claim.'}


def probe_file(path, window_samples=65536, stride_samples=65536):
    size = path.stat().st_size
    if size % 8 or not 65536 <= size <= 64 * 1024 * 1024:
        raise ValueError('cf32 input must be aligned and 64KiB..64MiB')
    x = np.fromfile(path, dtype='<c8')
    return probe_array(x, window_samples, stride_samples)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--window-samples', type=int, default=65536)
    parser.add_argument('--stride-samples', type=int, default=65536)
    args = parser.parse_args()
    result = probe_file(args.input, args.window_samples, args.stride_samples)
    with args.output.open('x') as f:
        json.dump(result, f, indent=2)
        f.write('\n')
