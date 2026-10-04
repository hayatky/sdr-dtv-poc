# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract fixed gr-isdbt receive blocks without transmitter code."""

import hashlib
import json
import re
import subprocess
from pathlib import Path
import sys

COMMIT = "56b2556c14ecc5d710070f969fda7a2deae65d8b"
BLOCKS = {
    "ofdm_synchronization": ("block", "mode cp_length interpolate"),
    "tmcc_decoder": ("block", "mode print_params"),
    "frequency_deinterleaver": ("sync_block", "oneseg mode"),
    "time_deinterleaver": ("sync_block", "mode segments_A length_A segments_B length_B segments_C length_C"),
    "symbol_demapper": ("sync_block", "mode segments_A constellation_size_A segments_B constellation_size_B segments_C constellation_size_C"),
    "bit_deinterleaver": ("sync_interpolator", "mode segments constellation_size"),
    "viterbi_decoder": ("block", "constellation_size rate"),
    "byte_deinterleaver": ("block", ""),
    "energy_descrambler": ("block", ""),
    "reed_solomon_dec_isdbt": ("block", ""),
}


def prepare(repository: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "include/gnuradio/isdbt"
    headers.mkdir(parents=True)
    manifest = []

    def copy(source: str, destination: Path) -> None:
        raw = subprocess.check_output(["git", "-C", str(repository), "show", f"{COMMIT}:{source}"])
        data = raw
        if source == "lib/ofdm_synchronization_impl.cc":
            # CP tracking expands by 8/16 samples. Near the initial search's
            # upper edge this exceeded d_norm's allocation in the fixed source.
            marker = b"assert(lookup_start >= lookup_stop);"
            replacement = (b"lookup_start = std::min(lookup_start, 2*d_fft_length+d_cp_length-1);\n"
                           b"                if (lookup_start <= lookup_stop) return false;\n"
                           b"                " + marker)
            if data.count(marker) != 1:
                raise RuntimeError("OFDM CP search boundary fix no longer applies")
            data = data.replace(marker, replacement)
        if source == "lib/viterbi_decoder_impl.cc":
            data = raw.replace(b"// Initialize starting metrics to prefer 0 state",
                               b"store_pos = 0;\n                // Initialize starting metrics to prefer 0 state")
            if data == raw:
                raise RuntimeError("Viterbi cursor fix no longer applies")
            # Mutable traceback/metrics must belong to each layer instance.
            # The fixed upstream static arrays corrupt concurrent A/B decoders.
            pattern = (rb"        __GR_ATTR_ALIGNED\(16\)(?:\n            | )"
                       rb"(?:__m128i|unsigned char|branchtab27) "
                       rb"viterbi_decoder_impl::(?:d_metric[01](?:_generic)?|"
                       rb"d_path[01](?:_generic)?|Branchtab27_(?:sse2|generic)|"
                       rb"mmresult|ppresult)\[[^;]+;\n")
            data, count = re.subn(pattern, b"", data)
            if count != 12:
                raise RuntimeError(f"Viterbi instance storage definitions changed: {count}")
            # At initialization the decoder emits the input prefix from byte 0;
            # only later calls need the pending traceback offset. Adding it on
            # the first call misaligns an exactly frame-aligned known input.
            marker = b"                    int to_out = noutput_items;"
            if data.count(marker) != 1:
                raise RuntimeError("Viterbi initial frame offset marker changed")
            data = data.replace(marker, marker + b"\n                    const bool initial_output = (d_init == 0);")
            before = b"this->nitems_written(0) + d_ntraceback + (tags[0].offset"
            after = b"this->nitems_written(0) + (initial_output ? 0 : d_ntraceback) + (tags[0].offset"
            if data.count(before) != 1:
                raise RuntimeError("Viterbi frame offset equation changed")
            data = data.replace(before, after)
        if source == "lib/viterbi_decoder_impl.h":
            pattern = (rb"static ((?:__m128i|unsigned char|branchtab27) "
                       rb"(?:d_metric[01](?:_generic)?|d_path[01](?:_generic)?|"
                       rb"Branchtab27_(?:sse2|generic)|mmresult|ppresult)\[)")
            data, count = re.subn(pattern, rb"alignas(16) \1", data)
            if count != 12:
                raise RuntimeError(f"Viterbi instance storage declarations changed: {count}")
        if source == "lib/tmcc_decoder_impl.cc":
            # The upstream exclusive end compares only 15 of 16 sync bits.
            data = data.replace(b"d_rcv_tmcc_data.begin() + d_tmcc_sync_size,",
                                b"d_rcv_tmcc_data.begin() + 1 + d_tmcc_sync_size,")
            # At frame_begin the accepted frame still occupies the bit deque.
            marker = b"this->add_item_tag(0,offset,key,value);"
            addition = (marker + b"\n                        std::string bits;\n"
                        b"                        for (auto bit : d_rcv_tmcc_data) bits.push_back('0' + bit);\n"
                        b"                        this->add_item_tag(0,offset,pmt::intern(\"tmcc_bits\"),pmt::intern(bits));")
            data = data.replace(marker, addition, 1)
            data = data.replace(b"carrier == d_ac_carriers[ac_pilot_index]",
                                b"ac_pilot_index < ac_carriers_size && carrier == d_ac_carriers[ac_pilot_index]")
            data = data.replace(b"carrier == tmcc_carriers[tmcc_pilot_index]",
                                b"tmcc_pilot_index < tmcc_carriers_size && carrier == tmcc_carriers[tmcc_pilot_index]")
            if data == raw or b"tmcc_bits" not in data:
                raise RuntimeError("TMCC audit tags no longer apply")
        if source == "lib/reed_solomon_dec_isdbt_impl.cc":
            before = b"float *ber_out = (float *) output_items[1]; \n                bool ber_out_connected  = output_items.size()>=2;"
            after = b"bool ber_out_connected = output_items.size() >= 2;\n                float *ber_out = ber_out_connected ? (float *) output_items[1] : nullptr;"
            data = data.replace(before, after)
            if data == raw:
                raise RuntimeError("RS optional output fix no longer applies")
        if source == "lib/symbol_demapper_impl.cc":
            before = b"unsigned char *out_C = (unsigned char *) output_items[2];"
            data = data.replace(before, b"unsigned char *out_C = output_items.size() >= 3 ? (unsigned char *) output_items[2] : nullptr;")
            if data == raw:
                raise RuntimeError("demapper optional output fix no longer applies")
        destination.write_bytes(data)
        manifest.append({"commit": COMMIT, "source": source,
                         "source_sha256": hashlib.sha256(raw).hexdigest(),
                         "output": str(destination.relative_to(output)),
                         "output_sha256": hashlib.sha256(data).hexdigest()})

    for name in BLOCKS:
        copy(f"include/gnuradio/isdbt/{name}.h", headers / f"{name}.h")
        for suffix in ("_impl.cc", "_impl.h"):
            copy(f"lib/{name}{suffix}", output / f"{name}{suffix}")
    copy("include/gnuradio/isdbt/api.h", headers / "api.h")
    copy("COPYING", output / "COPYING")
    copy("LICENSE", output / "LICENSE")

    binding = ["// SPDX-License-Identifier: GPL-3.0-or-later", "#include <pybind11/pybind11.h>",
               "#include <pybind11/complex.h>", "namespace py = pybind11;"]
    binding += [f"#include <gnuradio/isdbt/{name}.h>" for name in BLOCKS]
    binding += ["PYBIND11_MODULE(hlfecwideband, m) {", 'py::module_::import("gnuradio.gr");',
                'm.attr("viterbi_instance_storage") = true;',
                'm.attr("initial_frame_tag_alignment") = true;']
    for name, (base, args) in BLOCKS.items():
        bases = {"block": "gr::block, gr::basic_block",
                 "sync_block": "gr::sync_block, gr::block, gr::basic_block",
                 "sync_interpolator": "gr::sync_interpolator, gr::sync_block, gr::block, gr::basic_block"}[base]
        arguments = "".join(f', py::arg("{arg}")' for arg in args.split())
        binding += [f"using {name} = gr::isdbt::{name};",
                    f'py::class_<{name}, {bases}, std::shared_ptr<{name}>>(m, "{name}")',
                    f".def(py::init(&{name}::make){arguments});"]
    binding.append("}")
    (output / "bindings.cc").write_text("\n".join(binding) + "\n")
    sources = " ".join(name + "_impl.cc" for name in BLOCKS)
    (output / "CMakeLists.txt").write_text(f"""cmake_minimum_required(VERSION 3.16)
project(hlfecwideband LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(Gnuradio 3.10 REQUIRED COMPONENTS blocks fft filter fec)
find_package(Volk REQUIRED)
find_package(pybind11 REQUIRED)
pybind11_add_module(hlfecwideband bindings.cc {sources})
target_include_directories(hlfecwideband PRIVATE include)
target_compile_definitions(hlfecwideband PRIVATE VOLK_GT_122=1 gnuradio_isdbt_EXPORTS DTV_SSE2=1)
target_compile_options(hlfecwideband PRIVATE -msse2)
target_link_libraries(hlfecwideband PRIVATE gnuradio::gnuradio-runtime gnuradio::gnuradio-blocks gnuradio::gnuradio-fft gnuradio::gnuradio-filter gnuradio::gnuradio-fec Volk::volk)
""")
    (output / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
