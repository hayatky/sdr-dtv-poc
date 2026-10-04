# SPDX-License-Identifier: GPL-3.0-or-later
"""TMCC bit audit from ARIB STD-B31 v2.2, section 3.15 (no error correction).

https://www.arib.or.jp/english/html/overview/doc/6-STD-B31v2_2-E1.pdf
The difference-set cyclic code is checked by polynomial division, independently
of the native decoder's parity-check matrix. Mode is an external hypothesis.
"""
GENERATOR = sum(1 << i for i in (82,77,76,71,67,66,56,52,48,40,36,34,24,22,18,10,4,0))
SYNC = ('0011010111101110', '1100101000010001')


def remainder(value):
    while value.bit_length() >= GENERATOR.bit_length():
        value ^= GENERATOR << (value.bit_length()-GENERATOR.bit_length())
    return value


def decode(bits, mode):
    if len(bits) != 204 or set(bits)-{'0','1'} or mode not in (1,2,3):
        raise ValueError('invalid TMCC bit string or mode')
    if bits[1:17] not in SYNC:
        raise ValueError('TMCC 16-bit sync mismatch')
    if remainder(int(bits[20:],2)):
        raise ValueError('TMCC cyclic-code parity mismatch')
    def number(start, length): return int(bits[start:start+length],2)
    def layers(start):
        result={}
        for i,name in enumerate('ABC'):
            offset=start+13*i
            modulation,rate,interleave,segments=number(offset,3),number(offset+3,3),number(offset+6,3),number(offset+9,4)
            result[name]={'modulation_code':modulation,'modulation':{0:'DQPSK',1:'QPSK',2:'16QAM',3:'64QAM',7:'unused'}.get(modulation,'reserved'),
                          'rate_code':rate,'code_rate':{0:'1/2',1:'2/3',2:'3/4',3:'5/6',4:'7/8',7:'unused'}.get(rate,'reserved'),
                          'interleave_code':interleave,'interleave_length':((0,4,8,16)[interleave]//2**(mode-1) if interleave<4 else None),
                          'segments_code':segments,'segments':segments if 1<=segments<=13 else None}
        return result
    return {'raw_bits':bits,'sync_word':bits[1:17],'parity_valid':True,
            'parity_algorithm':'polynomial division, shortened (184,102) difference-set cyclic code',
            'mode_hypothesis':mode,'system_id':number(20,2),'switch_countdown':number(22,4),
            'emergency_start_flag':number(26,1),'partial_reception':bool(number(27,1)),
            'current_layers':layers(28),'next_partial_reception':bool(number(67,1)),
            'next_layers':layers(68)}
