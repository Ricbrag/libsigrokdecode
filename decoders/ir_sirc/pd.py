##
## This file is part of the libsigrokdecode project.
##
## Copyright (C) 2020 Tom Flanagan <knio@zkpq.ca>
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, see <http://www.gnu.org/licenses/>.
##

from common.srdhelper import bitpack
from .lists import ADDRESSES
import sigrokdecode as srd

class SamplerateError(Exception):
    pass

class SIRCError(Exception):
    pass

class SIRCErrorSilent(SIRCError):
    pass

class Decoder(srd.Decoder):
    api_version = 3
    id = 'ir_sirc'
    name = 'IR SIRC'
    longname = 'Sony IR (SIRC)'
    desc = 'Sony infrared remote control protocol (SIRC).'
    license = 'gplv2+'
    tags = ['IR']
    inputs = ['logic']
    outputs = []
    channels = (
        {'id': 'ir', 'name': 'IR', 'desc': 'IR data line'},
    )
    options = (
        {'id': 'polarity', 'desc': 'Polarity', 'default': 'active-low',
            'values': ('active-low', 'active-high')},
    )
    annotations = (
        ('bit', 'Bit'),
        ('agc', 'AGC'),
        ('pause', 'Pause'),
        ('start', 'Start'),
        ('command', 'Command'),
        ('address', 'Address'),
        ('extended', 'Extended'),
        ('remote', 'Remote'),
        ('warning', 'Warning'),
    )
    annotation_rows = (
        ('bits', 'Bits', (0, 1, 2)),
        ('fields', 'Fields', (3, 4, 5, 6)),
        ('remotes', 'Remotes', (7,)),
        ('warnings', 'Warnings', (8,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)
        self.active = self.options['polarity'] == 'active-high'

    def metadata(self, key, value):
        if key == srd.SRD_CONF_SAMPLERATE:
            self.samplerate = value
            self.snum_per_us = self.samplerate / 1e6

    def putg(self, ss, es, cls, texts):
        self.put(ss, es, self.out_ann, [cls, texts])

    def tolerance(self, ss, es, expected):
        microseconds = (es - ss) / self.snum_per_us
        tolerance = expected * 0.30
        return (expected - tolerance) < microseconds < (expected + tolerance)

    def wait_wrap(self, conds, timeout=None):
        conds = list(conds)
        if timeout is not None:
            to = int(timeout * self.snum_per_us)
            conds.append({'skip': to})
        ss = self.samplenum
        pins = self.wait(conds)
        es = self.samplenum
        return pins, ss, es, self.matched

    def read_pulse(self, high, time):
        e = 'f' if high else 'r'
        max_time = int(time * 1.30)
        pins, ss, es, (edge, timeout) = self.wait_wrap([{0: e}], max_time)
        if timeout or not self.tolerance(ss, es, time):
            raise SIRCError('Timeout')
        return pins, ss, es, (edge, timeout)

    def read_bit(self):
        e = 'f' if self.active else 'r'
        _, high_ss, high_es, (edge, timeout) = self.wait_wrap([{0: e}], 2000)
        if timeout:
            raise SIRCError('Bit High Timeout')
        if self.tolerance(high_ss, high_es, 1200):
            bit = 1
        elif self.tolerance(high_ss, high_es, 600):
            bit = 0
        else:
            raise SIRCError('Bit Low Timeout')
        try:
            _, low_ss, low_es, matched = self.read_pulse(not self.active, 600)
            good = True
        except SIRCError:
            low_es = high_es + int(600 * self.snum_per_us)
            good = False
        self.putg(high_ss, low_es, 0, ['{}'.format(bit)])
        return bit, high_ss, low_es, good

    def read_signal(self):
        # Start code
        try:
            _, agc_ss, agc_es, matched = self.read_pulse(self.active, 2400)
            _, pause_ss, pause_es, matched = self.read_pulse(not self.active, 600)
        except SIRCError:
            raise SIRCErrorSilent('not an SIRC message')
        self.putg(agc_ss, agc_es, 1, ['AGC', 'A'])
        self.putg(pause_ss, pause_es, 2, ['Pause', 'P'])
        self.putg(agc_ss, pause_es, 3, ['Start', 'S'])

        # Read bits
        bits = []
        while True:
            bit, ss, es, good = self.read_bit()
            bits.append((bit, ss, es))
            if len(bits) > 20:
                raise SIRCError('too many bits')
            if not good:
                if len(bits) == 12:
                    command = bits[0:7]
                    address = bits[7:12]
                    extended = []
                elif len(bits) == 15:
                    command = bits[0:7]
                    address = bits[7:15]
                    extended = []
                elif len(bits) == 20:
                    command = bits[0:7]
                    address = bits[7:12]
                    extended = bits[12:20]
                else:
                    raise SIRCError('incorrect number of bits: {}'.format(len(bits)))
                break

        command_num = bitpack([b[0] for b in command])
        address_num = bitpack([b[0] for b in address])
        command_str = '0x{:02X}'.format(command_num)
        address_str = '0x{:02X}'.format(address_num)
        self.putg(command[0][1], command[-1][2], 4, [
            'Command: {}'.format(command_str),
            'C:{}'.format(command_str),
        ])
        self.putg(address[0][1], address[-1][2], 5, [
            'Address: {}'.format(address_str),
            'A:{}'.format(address_str),
        ])
        extended_num = None
        if extended:
            extended_num = bitpack([b[0] for b in extended])
            extended_str = '0x{:02X}'.format(extended_num)
            self.putg(extended[0][1], extended[-1][2], 6, [
                'Extended: {}'.format(extended_str),
                'E:{}'.format(extended_str),
            ])
        return address_num, command_num, extended_num, bits[0][1], bits[-1][2]

    def decode(self):
        if not self.samplerate:
            raise SamplerateError('Cannot decode without samplerate.')

        while True:
            e = 'h' if self.active else 'l'
            signal, ss, es, matched = self.wait_wrap([{0: e}], None)
            try:
                address, command, extended, payload_ss, payload_es = self.read_signal()
                names, commands = ADDRESSES.get((address, extended), (['Unknown Device: ', 'UNK: '], {}))
                text = commands.get(command, 'Unknown')
                self.putg(es, payload_es, 7, [n + text for n in names])
            except SIRCErrorSilent as e:
                continue
            except SIRCError as e:
                self.putg(es, self.samplenum, 8, [
                    'Error: {}'.format(e),
                    'Error',
                    'E',
                ])
                continue