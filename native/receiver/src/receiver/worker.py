# SPDX-License-Identifier: GPL-3.0-or-later
"""One RX-only worker. No automatic reconnect after protocol/IO errors."""
import hashlib
from contextlib import nullcontext
import json
import os
from pathlib import Path
import resource
import signal
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'pocs/iq-lab'))
import rx_capture as rx

PROFILE = {'frequency_hz': 557142857, 'sample_rate_hz': 2500000,
           'rf_bandwidth_hz': 1500000, 'gain_db': 20, 'samples': 262144}
PROFILE_ID = 'ch27-pilot-v1'
PROFILES = {PROFILE_ID: PROFILE,
            'ch27-sync20-v1': {**PROFILE, 'samples': 2097152},
            'ch27-sync40-v1': {**PROFILE, 'samples': 2097152, 'gain_db': 40},
            'ch27-sync60-v1': {**PROFILE, 'samples': 2097152, 'gain_db': 60},
            'ch27-stream60-v1': {**PROFILE, 'samples': 2097152, 'gain_db': 60, 'readbuf_samples': 262144}}
PROFILES['ch27-diagnostic60-v1'] = {**PROFILES['ch27-stream60-v1'], 'rx_diagnostics': True}
PROFILES['ch27-stream20-2s-v1'] = {**PROFILES['ch27-stream60-v1'], 'samples': 5000000, 'gain_db': 20}
PROFILES['ch27-stream20-5s-v1'] = {**PROFILES['ch27-stream20-2s-v1'], 'samples': 12500000}
PROFILES['ch27-stream20-8s-v1'] = {**PROFILES['ch27-stream20-2s-v1'], 'samples': 20000000}
PROFILES['ch27-stream20-15s-v1'] = {**PROFILES['ch27-stream20-2s-v1'],
    'samples': 37500000, 'capture_limit_seconds': 25, 'job_limit_seconds': 35,
    'ui_exposed': False}
PROFILES['ch27-stream20-60s-v1'] = {**PROFILES['ch27-stream20-2s-v1'],
    'samples': 150000000, 'capture_limit_seconds': 75, 'job_limit_seconds': 85,
    'ui_exposed': False}
PROFILES['ch27-live20-70s-v1'] = {**PROFILES['ch27-stream20-2s-v1'],
    'samples': 175000000, 'capture_limit_seconds': 85, 'job_limit_seconds': 95,
    'ui_exposed': False}
# Fixed, staged wideband candidates. The board's acceptance and actual throughput
# are measured in #35; these values are requested settings, not observations.
WIDEBAND = {**PROFILE, 'sample_rate_hz': 8000000, 'rf_bandwidth_hz': 6000000,
            'readbuf_samples': 262144, 'ui_exposed': False}
PROFILES['ch27-wideband20-0p5s-v1'] = {**WIDEBAND, 'samples': 4000000,
    'capture_limit_seconds': 20, 'job_limit_seconds': 30}
# Two 2M-sample READBUFs test whether fewer command seams improve CP phase.
# Each 8,000,000-byte response stays below Session.status's 8 MiB limit.
PROFILES['ch27-wideband20-chunk2m0p5s-v1'] = {**WIDEBAND, 'samples': 4000000,
    'readbuf_samples': 2000000, 'capture_limit_seconds': 20, 'job_limit_seconds': 30}
PROFILES['ch27-wideband20-chunk2m2s-v1'] = {**WIDEBAND, 'samples': 16000000,
    'readbuf_samples': 2000000, 'capture_limit_seconds': 35, 'job_limit_seconds': 45}
PROFILES['ch27-wideband20-chunk2m10s-v1'] = {**WIDEBAND, 'samples': 80000000,
    'readbuf_samples': 2000000, 'capture_limit_seconds': 75, 'job_limit_seconds': 85}
# 6.4 MS/s still spans the 5.57 MHz active ISDB-T carriers while reducing
# ci16 transport demand from 32 to 25.6 MB/s. Treat RF edge quality as unproven.
WIDEBAND_6M4 = {**WIDEBAND, 'sample_rate_hz': 6400000, 'readbuf_samples': 2000000}
PROFILES['ch27-wideband6m4-0p5s-v1'] = {**WIDEBAND_6M4, 'samples': 3200000,
    'capture_limit_seconds': 20, 'job_limit_seconds': 30}
PROFILES['ch27-wideband6m4-2s-v1'] = {**WIDEBAND_6M4, 'samples': 12800000,
    'capture_limit_seconds': 35, 'job_limit_seconds': 45}
PROFILES['ch27-wideband6m4-10s-v1'] = {**WIDEBAND_6M4, 'samples': 64000000,
    'capture_limit_seconds': 75, 'job_limit_seconds': 85}
PROFILES['ch27-wideband6m4-30s-v1'] = {**WIDEBAND_6M4, 'samples': 192000000,
    'capture_limit_seconds': 50, 'job_limit_seconds': 60}
# Diagnostic: change only READBUF response size after intermittent 8 MB stalls.
PROFILES['ch27-wideband6m4-chunk1m30s-v1'] = {**PROFILES['ch27-wideband6m4-30s-v1'],
    'readbuf_samples': 1000000}
PROFILES['ch27-wideband6m4-90s-v1'] = {**WIDEBAND_6M4, 'samples': 576000000,
    'capture_limit_seconds': 120, 'job_limit_seconds': 130}
# #38 staged sustained RX: 4 MB responses, finite acquisition and restoration.
for seconds in (120, 630):
    PROFILES[f'ch27-wideband6m4-chunk1m{seconds}s-v1'] = {
        **WIDEBAND_6M4, 'samples': 6400000 * seconds, 'readbuf_samples': 1000000,
        'capture_limit_seconds': seconds + 30, 'job_limit_seconds': seconds + 40,
        'progress_interval_seconds': 1.0}
PROFILES['ch27-wideband20-2s-v1'] = {**WIDEBAND, 'samples': 16000000,
    'capture_limit_seconds': 35, 'job_limit_seconds': 45}
PROFILES['ch27-wideband20-10s-v1'] = {**WIDEBAND, 'samples': 80000000,
    'capture_limit_seconds': 75, 'job_limit_seconds': 85}
PROFILES['ch27-stream60-2s-v1'] = {**PROFILES['ch27-stream60-v1'], 'samples': 5000000}
PROFILES['ch27-stream71-2s-v1'] = {**PROFILES['ch27-stream60-2s-v1'], 'gain_db': 71}
PROFILES['ch27-rfbw750k-stream71-2s-v1'] = {**PROFILES['ch27-stream71-2s-v1'],
    'rf_bandwidth_hz': 750000, 'ui_exposed': False}
PROFILES['ch27-gain71-v1'] = {**PROFILES['ch27-stream60-v1'], 'gain_db': 71}
if os.environ.get('HLFEC_LIBIIO_READDEV'):
    PROFILES['ch27-libiio-stream60-v1'] = {**PROFILES['ch27-stream60-v1'], 'rx_backend': 'libiio'}
    PROFILES['ch27-wideband20-libiio0p5s-v1'] = {**PROFILES['ch27-wideband20-chunk2m0p5s-v1'], 'rx_backend': 'libiio'}
    PROFILES['ch27-wideband20-libiio2s-v1'] = {**PROFILES['ch27-wideband20-chunk2m2s-v1'], 'rx_backend': 'libiio'}
    PROFILES['ch27-wideband20-libiio10s-v1'] = {**PROFILES['ch27-wideband20-chunk2m10s-v1'], 'rx_backend': 'libiio'}
PROFILES['ch27-sync71-v1'] = {**PROFILES['ch27-sync60-v1'], 'gain_db': 71}
PROFILES['ch27-offset100k-sync60-v1'] = {**PROFILES['ch27-sync60-v1'],
    'frequency_hz': 557242857, 'frequency_rounding_hz': 4}
PROFILES['ch27-offset300k-stream60-v1'] = {**PROFILES['ch27-stream60-v1'],
    'frequency_hz': 557442857, 'frequency_rounding_hz': 4, 'ui_exposed': False}
PROFILES['ch27-offset300k-stream60-2s-v1'] = {**PROFILES['ch27-offset300k-stream60-v1'],
    'samples': 5000000}
PROFILES['ch27-offset300k-stream71-2s-v1'] = {**PROFILES['ch27-offset300k-stream60-2s-v1'],
    'gain_db': 71}
PROFILES['ch27-555mhz-diagnostic-v1'] = {**PROFILES['ch27-diagnostic60-v1'], 'frequency_hz': 555000000}
# CATV survey: overlapping short RX windows across 325..770 MHz. These are
# observation frequencies, not J:COM channel allocations or QAM claims.
for index in range(89):
    PROFILES[f'catv-survey-{index:02d}-v1'] = {
        'frequency_hz': 328000000 + index * 5000000,
        'sample_rate_hz': 8000000, 'rf_bandwidth_hz': 8000000,
        'gain_db': 20, 'samples': 512000, 'readbuf_samples': 256000,
        'frequency_rounding_hz': 4, 'capture_limit_seconds': 20,
        'job_limit_seconds': 30, 'ui_exposed': False, 'catv': True}
CATV_CANDIDATE = {**PROFILES['catv-survey-77-v1'], 'frequency_hz': 713000000,
                  'readbuf_samples': 256000}
PROFILES['catv-713mhz-0p5s-v1'] = {**CATV_CANDIDATE, 'samples': 4000000,
                                  'capture_limit_seconds': 30, 'job_limit_seconds': 40}
PROFILES['catv-713mhz-2s-v1'] = {**CATV_CANDIDATE, 'samples': 16000000,
                                'capture_limit_seconds': 50, 'job_limit_seconds': 60}
PROFILES['catv-743mhz-0p5s-v1'] = {**CATV_CANDIDATE, 'frequency_hz': 743000000,
                                   'samples': 4000000, 'capture_limit_seconds': 30,
                                   'job_limit_seconds': 40}
PROFILES['catv-743mhz-2s-v1'] = {**PROFILES['catv-743mhz-0p5s-v1'],
                                 'samples': 16000000, 'capture_limit_seconds': 50,
                                 'job_limit_seconds': 60}
# Bounded CATV candidate/transport comparisons, recorded in EXP-20260929-005.
PROFILES['catv-333mhz-0p5s-v1'] = {**PROFILES['catv-743mhz-0p5s-v1'],
                                   'frequency_hz': 333000000}
# Additional saved-IQ candidates with recovered TS fragments.
for mhz in (599, 623, 629, 647, 653):
    PROFILES[f'catv-{mhz}mhz-0p5s-v1'] = {
        **PROFILES['catv-333mhz-0p5s-v1'], 'frequency_hz': mhz * 1000000}
# Compare transport margin at a known QAM carrier; bandwidth remains 8 MHz.
PROFILES['catv-653mhz-chunk1m-0p5s-v1'] = {**PROFILES['catv-653mhz-0p5s-v1'],
    'readbuf_samples': 1000000}
PROFILES['catv-653mhz-chunk1m-2s-v1'] = {**PROFILES['catv-653mhz-chunk1m-0p5s-v1'],
    'samples': 16000000, 'capture_limit_seconds': 50, 'job_limit_seconds': 60}
for mhz in (635, 641, 659, 665, 671, 677, 689, 695, 707, 719, 737):
    PROFILES[f'catv-{mhz}mhz-chunk1m-0p5s-v1'] = {
        **PROFILES['catv-653mhz-chunk1m-0p5s-v1'], 'frequency_hz': mhz * 1000000}
# 5.274 Mbaud/0.13-rolloff fits 7 MS/s; compare sustained transport margin.
PROFILES['catv-641mhz-7m-chunk1m-0p5s-v1'] = {
    **PROFILES['catv-641mhz-chunk1m-0p5s-v1'], 'sample_rate_hz': 7000000,
    'samples': 3500000}
PROFILES['catv-641mhz-7m-rfbw6-chunk1m-0p5s-v1'] = {
    **PROFILES['catv-641mhz-7m-chunk1m-0p5s-v1'], 'rf_bandwidth_hz': 6000000}
PROFILES['catv-641mhz-7m-chunk1m-2s-v1'] = {
    **PROFILES['catv-641mhz-7m-chunk1m-0p5s-v1'], 'samples': 14000000,
    'capture_limit_seconds': 50, 'job_limit_seconds': 60}
PROFILES['catv-641mhz-7m-chunk1m-8s-v1'] = {
    **PROFILES['catv-641mhz-7m-chunk1m-0p5s-v1'], 'samples': 56000000,
    'capture_limit_seconds': 75, 'job_limit_seconds': 85}
PROFILES['catv-653mhz-6m4-0p5s-v1'] = {**PROFILES['catv-653mhz-0p5s-v1'],
    'sample_rate_hz': 6400000, 'samples': 3200000}
for channel in (16, 21, 22, 23, 24, 25, 26):
    PROFILES[f'ch{channel}-survey60-v1'] = {**PROFILES['ch27-stream60-v1'],
        'frequency_hz': 473142857 + (channel-13)*6000000,
        'physical_channel': channel, 'frequency_rounding_hz': 4}
    PROFILES[f'ch{channel}-scan20-2s-v1'] = {**PROFILES['ch27-stream20-2s-v1'],
        'frequency_hz': 473142857 + (channel-13)*6000000,
        'physical_channel': channel, 'frequency_rounding_hz': 4, 'ui_exposed': False}
TERMINAL = {'completed', 'cancelled', 'failed'}

# Other-channel prerequisites: center/GI/TMCC must be checked independently.
for seconds in (0.5, 10):
    label = '0p5' if seconds == 0.5 else '10'
    PROFILES[f'ch21-wideband6m4-{label}s-v1'] = {
        **PROFILES[f'ch27-wideband6m4-{label}s-v1'],
        'frequency_hz': 521142857, 'frequency_rounding_hz': 4,
        'physical_channel': 21, 'readbuf_samples': 1000000}


def read_rx_diagnostics(session, xml):
    """Read only advertised RX attributes; never infer or write RF port routing."""
    import xml.etree.ElementTree as ET
    context = ET.fromstring(xml)
    result = {}
    targets = [(session.phy, None, None, 'rx_path_rates'),
               (session.phy, 'input', 'voltage0', 'rf_port_select'),
               (session.phy, 'input', 'voltage0', 'filter_fir_en'),
               (session.phy, 'input', 'voltage0', 'quadrature_tracking_en'),
               (session.phy, 'input', 'voltage0', 'rf_dc_offset_tracking_en'),
               (session.phy, 'input', 'voltage0', 'bb_dc_offset_tracking_en'),
               (session.phy, 'output', 'altvoltage0', 'external'),
               (session.phy, 'output', 'altvoltage0', 'powerdown'),
               (session.rx, 'input', 'voltage0', 'sampling_frequency')]
    for device, direction, channel, attr in targets:
        element = next(d for d in context.findall('device') if d.get('id') == device)
        if direction:
            element = next(c for c in element.findall('channel') if c.get('id') == channel and c.get('type') == direction)
        key = '.'.join(filter(None, [device, direction, channel, attr]))
        if attr not in [a.get('name') for a in element.findall('attribute')]:
            result[key] = {'status': 'not_advertised'}
            continue
        command = f'READ {device} ' + (f'{direction.upper()} {channel} ' if direction else '') + attr
        session.send(command)
        length = session.status()
        if length > 1024:
            raise ValueError('RX diagnostic attribute too long')
        raw = rx.probe.read_exact(session.stream, length + 1)
        if not raw.endswith(b'\n'):
            raise ValueError('RX diagnostic terminator')
        result[key] = {'status': 'ok', 'value': raw[:-1].decode('ascii').strip(' \t\r\n\x00')}
    return result


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


class Cancelled(Exception):
    pass


class Mismatch(Exception):
    pass


def matches(attr, actual, expected):
    if attr == 'gain_control_mode':
        return actual == expected
    # Exact numeric comparison only; no unknown LO tolerance.
    from decimal import Decimal
    return Decimal(actual.split()[0]) == Decimal(expected.split()[0])


def capture(folder, job, cancelled, publish, connect=None, check_route=None, defer_completion=False,
            iq_output=None):
    """Test-injectable transport; production calls use fixed board endpoint."""
    connect = connect or (lambda: socket.create_connection((rx.HOST, 30431), timeout=2))
    check_route = check_route or rx.check_route
    profile = PROFILES[job.get('profile', PROFILE_ID)]
    if profile['sample_rate_hz'] not in (2500000, 6400000, 7000000, 8000000):
        raise ValueError('RX sample rate is outside the fixed allowlist')
    session = sock = None
    healthy, changed, opened = True, False, False
    draining = False
    baseline = job['baseline']
    started = time.monotonic()
    partial = folder / 'capture.iq.partial'
    error = None
    was_cancelled = False

    def checkpoint():
        if cancelled():
            raise Cancelled('停止要求を受け付けました')
        if time.monotonic() - started >= profile.get('capture_limit_seconds', 12):
            raise Cancelled('取得処理の時間上限に達しました')

    def call(fn, *args):
        nonlocal healthy
        try:
            return fn(*args)
        except BaseException:
            healthy = False
            raise

    def state(value):
        job['state'] = value
        publish(job)

    def read_settings():
        return {a: call(session.read, d, c, a) for d, c, a, _ in rx.SETTINGS}

    try:
        checkpoint()
        if (profile.get('rx_backend') != 'libiio' and
                min(profile['samples'], profile.get('readbuf_samples', profile['samples'])) * 4 > 8 * 1024 * 1024):
            raise ValueError('READBUF exceeds the audited IIOD response limit')
        check_route()
        state('configuring')
        sock = connect()
        sock.settimeout(2)
        stream = sock.makefile('rb')
        try:
            length, xml = call(rx.probe.fetch_payload, sock, stream, 'PRINT', rx.probe.MAX_XML_BYTES)
            if length < 0:
                raise OSError('IIOD PRINT failed')
            phy, device, formats = rx.validate_context(xml)
        finally:
            stream.close()
        session = rx.Session(sock, phy, device, job['commands'])
        baseline.update(read_settings())
        if profile.get('rx_diagnostics'):
            job['rx_diagnostics_before'] = call(read_rx_diagnostics, session, xml)
        # Validate every baseline before any write. No arbitrary saved command values.
        for attr in ('sampling_frequency', 'rf_bandwidth', 'frequency', 'hardwaregain'):
            value = float(baseline[attr].split()[0])
            if not __import__('math').isfinite(value):
                raise ValueError('nonfinite baseline')
        if baseline['gain_control_mode'] not in ('manual', 'slow_attack', 'fast_attack', 'hybrid'):
            raise ValueError('unknown baseline gain mode')
        job['scan_elements'] = formats
        settings = [('INPUT', 'voltage0', 'sampling_frequency', str(profile['sample_rate_hz'])),
                    ('INPUT', 'voltage0', 'rf_bandwidth', str(profile['rf_bandwidth_hz'])),
                    ('OUTPUT', 'altvoltage0', 'frequency', str(profile['frequency_hz'])),
                    ('INPUT', 'voltage0', 'gain_control_mode', 'manual'),
                    ('INPUT', 'voltage0', 'hardwaregain', str(profile['gain_db']))]
        for d, c, a, value in settings:
            checkpoint()
            changed = True
            job['restoration']['state'] = 'pending'
            publish(job)  # Persist uncertainty BEFORE writing.
            call(session.write, d, c, a, value)
        job['readback'] = read_settings()
        publish(job)
        for _, _, a, value in settings:
            known_ch27_pair = a == 'frequency' and value == '557142857' and job['readback'][a] == '557142854'
            if known_ch27_pair:
                job['frequency_readback_basis'] = 'EXP-20260927-013: observed exact pair; driver integer conversion; not RF calibration'
            bounded_rounding = False
            if a == 'frequency' and profile.get('frequency_rounding_hz') == 4:
                actual = job['readback'][a]
                bounded_rounding = actual.isdecimal() and abs(int(actual)-int(value)) <= 4
                if bounded_rounding:
                    job['frequency_readback_basis'] = 'EXP-019/027: fixed-profile <=4Hz driver integer/RFPLL rounding allowance; not RF calibration'
            if not known_ch27_pair and not bounded_rounding and not matches(a, job['readback'][a], value):
                raise Mismatch(f'RX readback mismatch: {a}; requested={value}, actual={job["readback"][a]}')
        checkpoint()
        if profile.get('rx_diagnostics'):
            job['rx_diagnostics_configured'] = call(read_rx_diagnostics, session, xml)
        state('capturing')
        count = profile['samples']
        buffer_count = min(count, profile.get('readbuf_samples', count))
        job['readbuf_chunks'] = []
        if profile.get('rx_backend') == 'libiio':
            from receiver import libiio_capture
            # The independent libiio context is confined to RX scan channels.
            # The original IIOD session remains idle and available for restore.
            libiio_capture.run(folder, partial, job, device, checkpoint, publish, rx.utc,
                               count, buffer_count)
        else:
            opened = True
            call(session.send, f'OPEN {device} {buffer_count} 00000003')
            if call(session.status) != 0:
                healthy = False
                raise ValueError('unexpected OPEN status')
            checkpoint()
            job['readbuf_start_utc'] = rx.utc()
            # An external supervisor may supply a bounded stream sink. It owns
            # the sink lifetime and must drain the current READBUF on consumer
            # failure, then return true from cancelled before another request.
            with (partial.open('xb') if iq_output is None else nullcontext(iq_output)) as output:
                # The partial file is written as bytes arrive. JSON progress is a
                # separate, bounded-rate observation, not the IQ durability gate.
                last_progress_bytes = 0
                last_progress_time = time.monotonic()
                progress_interval = profile.get('progress_interval_seconds')
                job['progress_publishes'] = 0
                for sample_offset in range(0, count, buffer_count):
                    checkpoint()  # Stop before issuing the next READBUF.
                    requested = min(buffer_count, count-sample_offset)
                    chunk_record = {'sample_offset': sample_offset, 'requested_samples': requested,
                                    'start_utc': rx.utc(), 'start_elapsed_seconds': time.monotonic()-started,
                                    'complete': False}
                    job['readbuf_chunks'].append(chunk_record)
                    draining = True
                    call(session.send, f'READBUF {device} {requested * 4}')
                    remaining, first = requested * 4, True
                    while remaining:
                        # Finish this bounded READBUF before graceful cancel/CLOSE.
                        length = call(session.status)
                        if not 0 < length <= remaining or length % 4:
                            healthy = False
                            raise ValueError('malformed RX length')
                        if first:
                            mask = call(rx.probe.read_exact, session.stream, 9)
                            if mask != b'00000003\n':
                                healthy = False
                                raise ValueError('unexpected RX mask')
                            first = False
                        block_remaining = length
                        while block_remaining:
                            # read1 preserves already received bytes on a later EOF.
                            chunk = call(session.stream.read1, min(block_remaining, 65536))
                            if not chunk:
                                healthy = False
                                raise EOFError('IIOD closed during IQ')
                            output.write(chunk)
                            output.flush()
                            block_remaining -= len(chunk)
                            job['received_bytes'] += len(chunk)
                            now = time.monotonic()
                            if (last_progress_bytes == 0 or
                                    (progress_interval is not None and now-last_progress_time >= progress_interval) or
                                    (progress_interval is None and
                                     (job['received_bytes'] - last_progress_bytes >= 1048576 or
                                      now - last_progress_time >= 0.25))):
                                job['progress_publishes'] += 1
                                publish(job)
                                last_progress_bytes = job['received_bytes']
                                last_progress_time = now
                        remaining -= length
                    draining = False
                    chunk_record.update(complete=True, end_utc=rx.utc(), end_elapsed_seconds=time.monotonic()-started)
                    job['readbuf_end_utc'] = chunk_record['end_utc']
                    usage = resource.getrusage(resource.RUSAGE_SELF)
                    job['worker_cpu_seconds'] = usage.ru_utime + usage.ru_stime
                    job['worker_max_rss_kib'] = usage.ru_maxrss
                    if progress_interval is None or time.monotonic()-last_progress_time >= progress_interval:
                        job['progress_publishes'] += 1
                        publish(job)
                        last_progress_bytes = job['received_bytes']
                        last_progress_time = time.monotonic()
                if iq_output is None:
                    os.fsync(output.fileno())
        checkpoint()
    except Cancelled as exc:
        was_cancelled, error = True, str(exc)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    finally:
        if draining:
            healthy = False
        try:
            if opened and healthy:
                call(session.send, f'CLOSE {session.rx}')
                if call(session.status) != 0:
                    healthy = False
                    raise ValueError('unexpected CLOSE status')
            if changed and healthy:
                state('restoring')
                for d, c, a, _ in rx.SETTINGS:
                    if a == 'gain_control_mode' or (a == 'hardwaregain' and baseline['gain_control_mode'] != 'manual'):
                        continue
                    call(session.write, d, c, a, baseline[a].split()[0])
                call(session.write, 'INPUT', 'voltage0', 'gain_control_mode', baseline['gain_control_mode'])
                restored = read_settings()
                job['restoration']['readback'] = restored
                for a, value in baseline.items():
                    if a == 'hardwaregain' and baseline['gain_control_mode'] != 'manual':
                        continue
                    if not matches(a, restored[a], value):
                        raise Mismatch(f'RX restore mismatch: {a}')
                job['restoration']['state'] = 'restored'
            elif changed:
                job['restoration']['state'] = 'unknown'
        except Exception as exc:
            job['restoration']['state'] = 'failed' if healthy else 'unknown'
            error = (error + '; ' if error else '') + f'restore: {exc}'
        finally:
            if session:
                session.stream.close()
            if sock:
                sock.close()
        if cancelled():
            was_cancelled = True
        job['error'] = error
        job['ended_utc'] = rx.utc()
        job['wall_seconds'] = time.monotonic() - started
        usage = resource.getrusage(resource.RUSAGE_SELF)
        job['worker_cpu_seconds'] = usage.ru_utime + usage.ru_stime
        job['worker_max_rss_kib'] = usage.ru_maxrss
        if job['restoration']['state'] in ('pending', 'failed', 'unknown'):
            job['state'] = 'failed'
        elif was_cancelled:
            job['state'] = 'cancelled'
        elif error:
            job['state'] = 'failed'
        else:
            job['state'] = 'completed'
        if partial.exists():
            length = partial.stat().st_size
            if job['state'] == 'completed' and not defer_completion:
                partial.rename(folder / 'capture.iq')
            filename = 'capture.iq' if job['state'] == 'completed' and not defer_completion else partial.name
            hash_state = hashlib.sha256()
            with (folder / filename).open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    hash_state.update(block)
            digest = hash_state.hexdigest()
            job['artifacts'].append({'kind': 'iq', 'name': filename, 'bytes': length, 'sha256': digest})
            metadata = {'schema_version': 1, 'source': 'hardware', 'format': 'ci16_le',
                        'iq_order': 'IQ', 'valid_bits': 12, 'storage_shift': 0,
                        'sample_rate_hz': profile['sample_rate_hz'],
                        'sample_rate_readback_hz': int(job['readback']['sampling_frequency']),
                        'center_frequency_hz': profile['frequency_hz'],
                        'center_frequency_readback_hz': int(job['readback']['frequency']),
                        'gain_db': profile['gain_db'], 'rf_bandwidth_hz': profile['rf_bandwidth_hz'],
                        'rf_bandwidth_readback_hz': int(job['readback']['rf_bandwidth']),
                        'start_utc': job['readbuf_start_utc'], 'sample_count': length // 4,
                        'sha256': digest, 'markers': [], 'partial': defer_completion or job['state'] != 'completed',
                        'trailing_bytes': length % 4,
                        'timestamp_scope': ('host libiio subprocess window; no hardware timestamp'
                                            if profile.get('rx_backend') == 'libiio' else
                                            'host READBUF command; no hardware timestamp')}
            save(folder / 'capture.json', metadata)
            input_metadata = {
                'schema_version': 1, 'source': 'hardware', 'provenance': job['id'],
                'usage': 'local experiment; raw IQ is not committed or redistributed',
                'format': 'ci16_le', 'byte_count': length, 'sample_count': length // 4,
                'sha256': digest,
                'requested': {'lo_hz': profile['frequency_hz'],
                              'sample_rate_hz': profile['sample_rate_hz'],
                              'rf_bandwidth_hz': profile['rf_bandwidth_hz'],
                              'gain_db': profile['gain_db']},
                'readback': {'lo_hz': int(job['readback']['frequency']),
                             'sample_rate_hz': int(job['readback']['sampling_frequency']),
                             'rf_bandwidth_hz': int(job['readback']['rf_bandwidth']),
                             'gain_db': float(job['readback']['hardwaregain'].split()[0])},
                'evaluation_rate': (None if profile.get('catv') else
                                    {'numerator': 512000000, 'denominator': 63}),
                'conversion': {'command': None, 'frequency_shift_hz': 0,
                               'resample_ratio': None, 'scale_divisor': None,
                               'transient_samples': None},
                'physical_channel': job.get('physical_channel'),
                'mode': None, 'guard_interval': None, 'tmcc_layers': None,
                'loss_observation': {'hardware_counter': None,
                                     'continuous_samples_proven': False},
                'partial': defer_completion or job['state'] != 'completed'}
            if profile.get('catv'):
                input_metadata['catv'] = {'symbol_rate_hz': None, 'qam_order': None,
                                          'rolloff': None, 'frequency_evidence':
                                          'survey center only; service unverified'}
            save(folder / 'input.json', input_metadata)
        if defer_completion:
            job['worker_result'] = job['state']
            job['state'] = 'finalizing'
        publish(job)
    return job


def main():
    os.umask(0o077)
    folder = Path(sys.argv[1])
    job = json.loads((folder / 'job.json').read_text())
    stopped = False
    hard_limit = PROFILES[job['profile']].get('job_limit_seconds', 30) - 1
    deadline = time.monotonic() + hard_limit
    # Independent process deadline survives controller crashes and slow-drip IO.
    # The worker is a session leader; the alarm must stop child RX readers too.
    signal.signal(signal.SIGALRM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
    signal.setitimer(signal.ITIMER_REAL, hard_limit)
    def stop(*_):
        nonlocal stopped
        stopped = True
        signal.setitimer(signal.ITIMER_REAL, max(0.001, min(14, deadline - time.monotonic())))
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    capture(folder, job, lambda: stopped or (folder / 'stop').exists(),
            lambda value: save(folder / 'job.json', value), defer_completion=True)


if __name__ == '__main__':
    main()
