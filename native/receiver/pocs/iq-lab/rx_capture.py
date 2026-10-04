# SPDX-License-Identifier: GPL-3.0-or-later
"""One bounded RX capture through the board's existing IIOD/RNDIS path.

Only explicitly validated RX devices/channels are used. No TX/DDS buffer API.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('probe', ROOT/'scripts/iio-readonly-probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
HOST = '192.168.2.1'
COUNT = 262144
SETTINGS = [('INPUT', 'voltage0', 'sampling_frequency', '2500000'),
            ('INPUT', 'voltage0', 'rf_bandwidth', '1500000'),
            ('OUTPUT', 'altvoltage0', 'frequency', '2400000000'),
            ('INPUT', 'voltage0', 'gain_control_mode', 'manual'),
            ('INPUT', 'voltage0', 'hardwaregain', '20')]


def utc():
    return datetime.now(timezone.utc).isoformat()


def check_route():
    boards = [d for d in Path('/sys/bus/usb/devices').iterdir()
              if (d/'idVendor').exists() and (d/'idVendor').read_text().strip() == '0456'
              and (d/'idProduct').read_text().strip() == 'b673']
    if len(boards) != 1:
        raise ValueError('expected exactly one target USB board')
    interfaces = [p.name for p in boards[0].parent.glob(boards[0].name + ':*/net/*')]
    route = json.loads(subprocess.check_output(['ip', '-j', '-4', 'route', 'get', HOST], text=True))
    validate_route(interfaces, route)


def validate_route(interfaces, route):
    if len(interfaces) != 1 or len(route) != 1 or route[0].get('dev') != interfaces[0] or 'gateway' in route[0] or route[0].get('prefsrc') != '192.168.2.10':
        raise ValueError('board route must be direct over target USB RNDIS with expected source')


def validate_context(payload):
    root = ET.fromstring(payload)
    def unique(name):
        values = [d for d in root.findall('device') if d.get('name') == name]
        if len(values) != 1:
            raise ValueError(f'expected one {name}')
        probe.safe_token(values[0].get('id'))
        return values[0]
    phy, rx = unique('ad9361-phy'), unique('cf-ad9361-lpc')
    for direction, channel, attr, _ in SETTINGS:
        matches = [c for c in phy.findall('channel') if c.get('id') == channel and c.get('type') == direction.lower()]
        if len(matches) != 1 or attr not in [a.get('name') for a in matches[0].findall('attribute')]:
            raise ValueError('missing required RX attribute')
        if direction == 'OUTPUT' and matches[0].get('name') != 'RX_LO':
            raise ValueError('output channel is not RX_LO')
    channels = rx.findall('channel')
    if len(channels) != 2:
        raise ValueError('expected exactly two RX scan channels')
    formats = []
    for index, ch in enumerate(channels):
        scan = ch.find('scan-element')
        if ch.get('id') != f'voltage{index}' or ch.get('type') != 'input' or scan is None:
            raise ValueError('unexpected RX channels/order')
        if scan.get('index') != str(index) or scan.get('format') != 'le:S12/16>>0':
            raise ValueError(f'unsupported RX scan format: {scan.attrib}')
        formats.append(dict(scan.attrib))
    return phy.get('id'), rx.get('id'), formats


class Session:
    def __init__(self, sock, phy, rx, log):
        self.sock, self.stream = sock, sock.makefile('rb')
        self.phy, self.rx, self.log = phy, rx, log

    def send(self, command):
        self.log.append({'utc': utc(), 'command': command})
        probe.send_command(self.sock, command)

    def status(self):
        result = probe.response_length(self.stream, 8 * 1024 * 1024)
        if result < 0:
            raise OSError(f'IIOD error {result}')
        return result

    def read(self, direction, channel, attr):
        self.send(f'READ {self.phy} {direction} {channel} {attr}')
        length = self.status()
        if length > 256:
            raise ValueError('attribute too long')
        value = probe.read_exact(self.stream, length + 1)
        if not value.endswith(b'\n'):
            raise ValueError('attribute terminator')
        return value[:-1].decode('ascii').strip(' \t\r\n\x00')

    def write(self, direction, channel, attr, value):
        if (direction, channel, attr) not in [s[:3] for s in SETTINGS]:
            raise ValueError('write outside RX allowlist')
        payload = value.encode('ascii')
        self.send(f'WRITE {self.phy} {direction} {channel} {attr} {len(payload)}')
        self.sock.sendall(payload)
        if self.status() != len(payload):
            raise ValueError('short attribute write')

    def acquire(self, count=None):
        count = COUNT if count is None else count
        if type(count) is not int or not 1 <= count <= 1310720:
            raise ValueError('capture size outside bounded range')
        self.send(f'OPEN {self.rx} {count} 00000003')
        if self.status() != 0:
            raise ValueError('unexpected OPEN status')
        self.send(f'READBUF {self.rx} {count * 4}')
        chunks, remaining, first = [], count * 4, True
        while remaining:
            length = self.status()
            if not 0 < length <= remaining or length % 4:
                raise ValueError('short or malformed RX block')
            if first:
                if probe.read_exact(self.stream, 9) != b'00000003\n':
                    raise ValueError('unexpected RX channel mask')
                first = False
            chunks.append(probe.read_exact(self.stream, length))
            remaining -= length
        self.send(f'CLOSE {self.rx}')
        if self.status() != 0:
            raise ValueError('unexpected CLOSE status')
        return b''.join(chunks)


def frequency_readback_matches(requested, observed):
    # Only this observed 6ch readback pair is additionally accepted, not a broad tolerance.
    # This is driver reporting, not a measured RF LO accuracy guarantee.
    return observed == requested or (requested == '2437000000' and observed == '2436999998')


def run(output, settle_samples=0, samples=None, frequency=2400000000):
    if frequency not in (2400000000, 2412000000, 2437000000):
        raise ValueError('frequency outside experiment allowlist')
    settings = [(*s[:3], str(frequency) if s[2] == 'frequency' else s[3]) for s in SETTINGS]
    samples = COUNT if samples is None else samples
    if samples not in (COUNT, 4 * COUNT) or settle_samples not in (0, COUNT):
        raise ValueError('unsupported experiment size')
    os.umask(0o077)
    check_route()
    if shutil.disk_usage(output.parent).free < 64 * 1024 * 1024:
        raise ValueError('need at least 64 MiB free')
    output.mkdir(parents=True, exist_ok=False)
    log, baseline, actual = [], {}, {}
    report = {'start_utc': utc(), 'status': 'incomplete', 'commands': log,
              'baseline': baseline, 'actual': actual, 'settings_restored': False,
              'git_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'python': sys.version.split()[0]}
    report.update(settle_samples=settle_samples, retained_samples=samples,
                  acquired_samples=settle_samples + samples,
                  host_loadavg_before=list(os.getloadavg()))
    started, cpu = time.perf_counter(), time.process_time()
    try:
        with socket.create_connection((HOST, 30431), timeout=5) as sock:
            sock.settimeout(5)
            stream = sock.makefile('rb')
            length, payload = probe.fetch_payload(sock, stream, 'PRINT', probe.MAX_XML_BYTES)
            if length < 0:
                raise OSError('PRINT failed')
            phy, rx, formats = validate_context(payload)
            stream.close()
            report['scan_elements'] = formats
            session = Session(sock, phy, rx, log)
            try:
                for direction, channel, attr, _ in SETTINGS:
                    baseline[attr] = session.read(direction, channel, attr)
                # Keep original gain mode; AGC gain is time-varying and not restored.
                for direction, channel, attr, value in settings:
                    session.write(direction, channel, attr, value)
                for direction, channel, attr, expected in settings:
                    value = session.read(direction, channel, attr)
                    actual[attr] = value
                    if attr == 'frequency':
                        ok = frequency_readback_matches(expected,value)
                    elif attr == 'gain_control_mode':
                        ok = value == expected
                    else:
                        ok = float(value.split()[0]) == float(expected)
                    if not ok:
                        raise ValueError(f'RX readback mismatch: {attr}')
                report['readbuf_start_utc'] = utc()
                tick = time.perf_counter()
                acquired = session.acquire(settle_samples + samples)
                report['acquire_wall_seconds'] = time.perf_counter() - tick
                report['readbuf_end_utc'] = utc()
                raw = acquired[settle_samples * 4:]
                if settle_samples:
                    (output/'capture-all.iq').write_bytes(acquired)
                (output/'capture.iq').write_bytes(raw)
                report['acquired_sha256'] = hashlib.sha256(acquired).hexdigest()
                report['acquired_bytes'] = len(acquired)
                # Restore only the same five RX attributes on a healthy session.
                for direction, channel, attr, _ in SETTINGS:
                    if attr == 'gain_control_mode':
                        continue
                    if attr == 'hardwaregain' and baseline['gain_control_mode'] != 'manual':
                        continue
                    session.write(direction, channel, attr, baseline[attr].split()[0])
                session.write('INPUT', 'voltage0', 'gain_control_mode', baseline['gain_control_mode'])
                restored = {}
                for direction, channel, attr, _ in SETTINGS:
                    restored[attr] = session.read(direction, channel, attr)
                    if attr == 'hardwaregain' and baseline['gain_control_mode'] != 'manual':
                        continue
                    if restored[attr] != baseline[attr]:
                        raise ValueError(f'RX restore mismatch: {attr}')
                report['restored_readback'] = restored
                report['settings_restored'] = True
                session.send('EXIT')
            finally:
                session.stream.close()
        meta = {'schema_version': 1, 'source': 'hardware', 'format': 'ci16_le', 'iq_order': 'IQ',
                'valid_bits': 12, 'storage_shift': 0, 'sample_rate_hz': 2500000,
                'center_frequency_hz': frequency, 'center_frequency_readback_hz': int(actual['frequency']), 'gain_db': 20, 'rf_bandwidth_hz': 1500000,
                'start_utc': report['readbuf_start_utc'], 'timestamp_scope': 'host command time; not hardware timestamp',
                'sample_count': samples, 'sha256': hashlib.sha256(raw).hexdigest(), 'markers': [],
                'discarded_prefix_samples': settle_samples,
                'nominal_start_offset_s': settle_samples / 2500000}
        (output/'capture.json').write_text(json.dumps(meta, indent=2)+'\n')
        report['status'] = 'success'
    except BaseException as exc:
        report['error'] = str(exc)
        raise
    finally:
        report.update(end_utc=utc(), wall_seconds=time.perf_counter()-started,
                      host_loadavg_after=list(os.getloadavg()),
                      process_cpu_seconds=time.process_time()-cpu,
                      peak_rss_kib_linux=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        (output/'run.json').write_text(json.dumps(report, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--settle-samples', type=int, choices=(0, COUNT), default=0)
    parser.add_argument('--samples', type=int, choices=(COUNT, 4 * COUNT), default=COUNT)
    parser.add_argument('--frequency', type=int, choices=(2400000000, 2412000000, 2437000000), default=2400000000)
    args = parser.parse_args()
    def stop(signum, frame):
        raise TimeoutError(f'capture stopped by signal {signum}')
    signal.signal(signal.SIGALRM, stop)
    signal.signal(signal.SIGTERM, stop)
    signal.alarm(40)
    try:
        run(args.output, args.settle_samples, args.samples, args.frequency)
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
