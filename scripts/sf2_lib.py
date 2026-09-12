#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小 SF2 解析器（只读）——回答"这个音源里到底有什么、某个鼓采样多长"

为什么需要：GeneralUser GS 的底鼓采样短而干，导致低频连续性对不上参考曲
（`PITFALLS.md` 坑 74/80）。要判断"是不是选错了鼓组 / 有没有更长的采样"，
就得能读 sf2 的 pdta 表（phdr/inst/shdr…），而不是靠盲试 note 号。

只实现查表所需的子集：preset → instrument → 采样（名字/长度/采样率）。
不解析 mod 表（本项目不用 LFO/包络调制）。
"""
import struct

GEN_INSTRUMENT = 41
GEN_KEYRANGE = 43
GEN_VELRANGE = 44
GEN_SAMPLEID = 53


def _sub(buf, off, end):
    """遍历 chunk（RIFF：4 字节 id + 4 字节长度 + 数据，奇数长度补 1 字节）"""
    while off + 8 <= end:
        cid = buf[off:off + 4].decode('latin-1', 'replace')
        sz = struct.unpack_from('<I', buf, off + 4)[0]
        yield cid, off + 8, off + 8 + sz
        off += 8 + sz + (sz & 1)


def _name(b):
    return b.split(b'\x00')[0].decode('latin-1', 'replace').strip()


class Sf2(object):
    def __init__(self, path):
        self.path = path
        buf = open(path, 'rb').read()
        if buf[:4] != b'RIFF' or buf[8:12] != b'sfbk':
            raise ValueError('不是 SF2 文件: %s' % path)
        pdta = {}
        for cid, s, e in _sub(buf, 12, len(buf)):
            if cid == 'LIST' and buf[s:s + 4] == b'pdta':
                for c2, s2, e2 in _sub(buf, s + 4, e):
                    pdta[c2] = buf[s2:e2]
        need = ('phdr', 'pbag', 'pgen', 'inst', 'ibag', 'igen', 'shdr')
        miss = [k for k in need if k not in pdta]
        if miss:
            raise ValueError('缺表: %s' % ', '.join(miss))
        self._parse(pdta)

    def _parse(self, pdta):
        d = pdta
        self.presets = []
        for i in range(len(d['phdr']) // 38):
            o = i * 38
            nm = _name(d['phdr'][o:o + 20])
            pr, bk = struct.unpack_from('<HH', d['phdr'], o + 20)
            bag = struct.unpack_from('<H', d['phdr'], o + 24)[0]
            self.presets.append(dict(name=nm, program=pr, bank=bk, bag=bag))
        self.pbags = [struct.unpack_from('<HH', d['pbag'], i * 4)
                      for i in range(len(d['pbag']) // 4)]
        self.pgens = [struct.unpack_from('<Hh', d['pgen'], i * 4)
                      for i in range(len(d['pgen']) // 4)]
        self.insts = []
        for i in range(len(d['inst']) // 22):
            o = i * 22
            self.insts.append(dict(name=_name(d['inst'][o:o + 20]),
                                   bag=struct.unpack_from('<H', d['inst'], o + 20)[0]))
        self.ibags = [struct.unpack_from('<HH', d['ibag'], i * 4)
                      for i in range(len(d['ibag']) // 4)]
        self.igens = [struct.unpack_from('<Hh', d['igen'], i * 4)
                      for i in range(len(d['igen']) // 4)]
        self.samples = []
        for i in range(len(d['shdr']) // 46):
            o = i * 46
            nm = _name(d['shdr'][o:o + 20])
            st, en, sl, el, sr = struct.unpack_from('<IIIII', d['shdr'], o + 20)
            typ = struct.unpack_from('<H', d['shdr'], o + 44)[0]
            self.samples.append(dict(name=nm, start=st, end=en, loop=(sl, el),
                                     rate=sr or 44100, type=typ,
                                     secs=(en - st) / float(sr or 44100)))
        # **zone 范围由"下一条记录的索引"界定，不是数组相邻项**：
        # phdr[i] 的 zone 从 pbag[phdr[i].bag] 到 pbag[phdr[i+1].bag]；
        # inst[i] 同理用 ibag[inst[i+1].bag]。踩过：用 pbag[bag+1] 会让每个 preset
        # 只看到第一个（全局）zone，于是所有鼓组都"查不到采样"。
        for i, p in enumerate(self.presets):
            p['genend'] = (self.presets[i + 1]['bag'] if i + 1 < len(self.presets)
                           else len(self.pbags) - 1)
        for i, it in enumerate(self.insts):
            it['bagend'] = (self.insts[i + 1]['bag'] if i + 1 < len(self.insts)
                            else len(self.ibags) - 1)

    # ---------------------------------------------------------------- 查表
    def preset_instruments(self, p):
        """preset 记录 → 它引用的 instrument 下标列表

        两级索引容易写错：**pbag/bag 是"zone 数组"，每个 zone 再由 pbag[z] 的
        wGenNdx 指向 pgen 的一段**。所以先按 zone 循环，再展开该 zone 的 gen。
        （踩过：直接拿 pbag[z][0] 当 pgen 下标，会把相邻 zone 的 gen 混在一起。）"""
        out = []
        for z in range(p['bag'], p['genend']):
            for gi in range(self.pbags[z][0], self.pbags[z + 1][0]):
                op, amt = self.pgens[gi]
                if op == GEN_INSTRUMENT:
                    out.append(amt)
        return out

    def inst_zones(self, ii):
        """instrument 下标 → [(键范围, 力度范围, 采样下标)]"""
        out, it = [], self.insts[ii]
        for z in range(it['bag'], it['bagend']):
            kr, vr, sid = (0, 127), (0, 127), None
            for gj in range(self.ibags[z][0], self.ibags[z + 1][0]):
                op, amt = self.igens[gj]
                if op == GEN_KEYRANGE:
                    kr = (amt & 0xFF, (amt >> 8) & 0xFF)
                elif op == GEN_VELRANGE:
                    vr = (amt & 0xFF, (amt >> 8) & 0xFF)
                elif op == GEN_SAMPLEID:
                    sid = amt
            if sid is not None:
                out.append((kr, vr, sid))
        return out

    def drum_lookup(self, bank, program, note, vel=100):
        """鼓组查表：返回命中该 note/vel 的采样记录列表"""
        hit = []
        for p in self.presets:
            if p['bank'] == bank and p['program'] == program:
                for ii in self.preset_instruments(p):
                    for kr, vr, sid in self.inst_zones(ii):
                        if kr[0] <= note <= kr[1] and vr[0] <= vel <= vr[1]:
                            hit.append(self.samples[sid])
        return hit


def main():
    import sys
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 1
    s = Sf2(a[0])
    if '--drums' in a:
        # 鼓组体检：每个 kit 的低频打击采样有多长（判断"能不能垫住低频"）
        notes = [int(x) for x in (a[a.index('--drums') + 1].split(',')
                                  if a.index('--drums') + 1 < len(a) else ['35', '36'])]
        print('%-22s %-6s %-28s %7s' % ('kit', 'note', 'sample', '秒'))
        for p in s.presets:
            if p['bank'] != 128:
                continue
            for n in notes:
                for smp in s.drum_lookup(128, p['program'], n):
                    print('%-22s %-6d %-28s %7.3f'
                          % (p['name'][:22], n, smp['name'][:28], smp['secs']))
        return 0
    print('%s: %d presets, %d instruments, %d samples'
          % (s.path, len(s.presets), len(s.insts), len(s.samples)))
    for p in s.presets:
        print('  bank %3d prog %3d  %s' % (p['bank'], p['program'], p['name']))
    return 0


import cli_utf8 as _cu; _cu.setup()   # 控制台编码兜底（GBK 下打印会崩）
if __name__ == '__main__':
    import sys
    sys.exit(main())
