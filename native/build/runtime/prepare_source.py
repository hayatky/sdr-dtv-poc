# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract only receive blocks from fixed GPL-3.0-or-later upstream revisions.

Generated files retain upstream copyright/license headers. No RF source/sink is
built or instantiated. This script contains the complete port transformations.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess

CURRENT = '56b2556c14ecc5d710070f969fda7a2deae65d8b'
LEGACY = '261019a65f5ac09144a81f0800f9a80bdc88e539'
BLOCKS = {
    'ofdm_synchronization_1seg': ('block', ['mode', 'cp_length']),
    'tmcc_decoder_1seg': ('sync_block', ['mode', 'print_params']),
    'frequency_deinterleaver_1seg': ('sync_block', ['mode']),
    'time_deinterleaver_1seg': ('sync_block', ['mode', 'length']),
    'symbol_demapper_1seg': ('sync_block', ['mode', 'constellation_size']),
    'bit_deinterleaver': ('sync_interpolator', ['mode', 'segments', 'constellation_size']),
    'viterbi_decoder': ('block', ['constellation_size', 'rate']),
    'byte_deinterleaver': ('block', []),
    'energy_descrambler': ('block', []),
    'reed_solomon_dec_isdbt': ('block', []),
}


def prepare(repository, output):
    output.mkdir(parents=True, exist_ok=False)
    headers = output / 'include/gnuradio/isdbt'
    headers.mkdir(parents=True)
    provenance = []
    def extract(commit, source, destination):
        raw = subprocess.check_output(['git', '-C', str(repository), 'show', f'{commit}:{source}'])
        text = raw.decode()
        if commit == LEGACY:
            text = text.replace('#include <isdbt/', '#include <gnuradio/isdbt/')
            text = text.replace('boost::shared_ptr', 'std::shared_ptr')
            text = text.replace('gr::fft::fft_complex(', 'gr::fft::fft_complex_fwd(')
            text = text.replace('gr::fft::fft_complex ', 'gr::fft::fft_complex_fwd ')
            text = text.replace('pow(2.0,7+mode),true,1', 'pow(2.0,7+mode),1')
            if source.startswith('include/'):
                text = '#include <memory>\n' + text
        if source == 'lib/ofdm_synchronization_1seg_impl.cc':
            text = text.replace('d_est_freq = 0;',
                                'd_est_freq = 0; d_delta_aux = 0; d_est_delta = 0; d_freq_offset_candidate = 0;')
            text = text.replace('assert(lookup_start >= lookup_stop);',
                                'lookup_start = std::min(lookup_start, 2*d_fft_length+d_cp_length-1);\n'
                                '                if (lookup_start <= lookup_stop) return false;\n'
                                '                assert(lookup_start >= lookup_stop);')
            # A failed narrow search leaves its output frequency undefined.
            text = text.replace('d_peak_epsilon = peak_epsilon_aux;',
                                'if (d_cp_found) d_peak_epsilon = peak_epsilon_aux;')
            text = text.replace('d_samp_phase = 0; \n                            d_moved_cp',
                                'if (d_cp_found) d_peak_epsilon = peak_epsilon_aux;\n'
                                '                            d_samp_phase = 0; \n                            d_moved_cp')
        if source == 'lib/tmcc_decoder_1seg_impl.cc':
            # std::equal uses an exclusive end: compare all 16 sync bits.
            text = text.replace('d_rcv_tmcc_data.begin() + d_tmcc_sync_size,',
                                'd_rcv_tmcc_data.begin() + 1 + d_tmcc_sync_size,')
            # At frame_begin the deque still contains the accepted prior frame.
            text = text.replace('this->add_item_tag(0,offset,key,value);',
                                'this->add_item_tag(0,offset,key,value);\n'
                                '                        if (pmt::eq(key, pmt::intern("frame_begin"))) {\n'
                                '                            std::string bits;\n'
                                '                            for (auto bit : d_rcv_tmcc_data) bits.push_back(\'0\' + bit);\n'
                                '                            this->add_item_tag(0,offset,pmt::intern("tmcc_bits"),pmt::intern(bits));\n'
                                '                        }')
            # ASAN: array index reached the end after the final AC/TMCC pilot.
            text = text.replace('carrier == d_ac_carriers[ac_pilot_index]',
                                'ac_pilot_index < ac_carriers_size && carrier == d_ac_carriers[ac_pilot_index]')
            text = text.replace('carrier == tmcc_carriers[tmcc_pilot_index]',
                                'tmcc_pilot_index < tmcc_carriers_size && carrier == tmcc_carriers[tmcc_pilot_index]')
            text = re.sub(r'int(\s+tmcc_decoder_1seg_impl::tmcc_print)', r'void\1', text)
        if source == 'lib/tmcc_decoder_1seg_impl.h':
            text = text.replace('int tmcc_print();', 'void tmcc_print();')
        if source == 'lib/viterbi_decoder_impl.cc':
            # The circular traceback cursor was never initialized, including
            # when a resync resets the path arrays (ASAN global overrun).
            text = text.replace('// Initialize starting metrics to prefer 0 state',
                                'store_pos = 0;\n                // Initialize starting metrics to prefer 0 state')
        destination.write_text(text)
        provenance.append({'commit': commit, 'source': source, 'source_sha256': hashlib.sha256(raw).hexdigest(),
                           'output': str(destination.relative_to(output)), 'output_sha256': hashlib.sha256(text.encode()).hexdigest()})
    for name in BLOCKS:
        commit = LEGACY if name.endswith('_1seg') else CURRENT
        path = 'include/isdbt/' if commit == LEGACY else 'include/gnuradio/isdbt/'
        extract(commit, path + name + '.h', headers / (name + '.h'))
        for suffix in ('_impl.cc', '_impl.h'):
            extract(commit, 'lib/' + name + suffix, output / (name + suffix))
    extract(CURRENT, 'include/gnuradio/isdbt/api.h', headers / 'api.h')
    extract(CURRENT, 'COPYING', output / 'COPYING')
    extract(CURRENT, 'LICENSE', output / 'LICENSE')
    binding = ['// SPDX-License-Identifier: GPL-3.0-or-later', '#include <pybind11/pybind11.h>',
               '#include <pybind11/complex.h>', 'namespace py = pybind11;']
    binding += [f'#include <gnuradio/isdbt/{name}.h>' for name in BLOCKS]
    binding += ['PYBIND11_MODULE(hlfeconeseg, m) {', 'py::module_::import("gnuradio.gr");']
    for name, (base, args) in BLOCKS.items():
        bases = {'block': 'gr::block, gr::basic_block',
                 'sync_block': 'gr::sync_block, gr::block, gr::basic_block',
                 'sync_interpolator': 'gr::sync_interpolator, gr::sync_block, gr::block, gr::basic_block'}[base]
        arguments = ''.join(', py::arg("' + arg + '")' for arg in args)
        binding += [f'using {name} = gr::isdbt::{name};',
                    f'py::class_<{name}, {bases}, std::shared_ptr<{name}>>(m, "{name}")',
                    f'.def(py::init(&{name}::make){arguments});']
    binding += ['}']
    (output / 'bindings.cc').write_text('\n'.join(binding) + '\n')
    sources = ' '.join(name + '_impl.cc' for name in BLOCKS)
    (output / 'CMakeLists.txt').write_text(f'''cmake_minimum_required(VERSION 3.16)
project(hlfeconeseg LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(Gnuradio 3.10 REQUIRED COMPONENTS blocks fft filter fec)
find_package(Volk REQUIRED)
find_package(pybind11 REQUIRED)
pybind11_add_module(hlfeconeseg bindings.cc {sources})
target_include_directories(hlfeconeseg PRIVATE include)
target_compile_definitions(hlfeconeseg PRIVATE VOLK_GT_122=1 gnuradio_isdbt_EXPORTS)
target_compile_options(hlfeconeseg PRIVATE -msse2)
target_link_libraries(hlfeconeseg PRIVATE gnuradio::gnuradio-runtime gnuradio::gnuradio-blocks gnuradio::gnuradio-fft gnuradio::gnuradio-filter gnuradio::gnuradio-fec Volk::volk)
''')
    (output / 'source-manifest.json').write_text(json.dumps(provenance, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    prepare(args.repository, args.output)
