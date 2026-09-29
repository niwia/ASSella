#!/usr/bin/env python3
import argparse
import os
import struct
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
try:
    from Crypto.Cipher import AES
except ImportError:
    try:
        from Cryptodome.Cipher import AES
    except ImportError as e:
        print(f'ERROR: pycryptodome not importable in this interpreter ({sys.executable}).')
        print(f'       Underlying error: {e}')
        print(f'       Install with: {sys.executable} -m pip install pycryptodome')
        sys.exit(1)
try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_MODE_64
    from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG
    HAVE_CAPSTONE = True
except ImportError:
    HAVE_CAPSTONE = False
IMAGE_DOS_HEADER_SIZE = 64
IMAGE_FILE_HEADER_SIZE = 20
IMAGE_OPTIONAL_HEADER32_SIZE = 224
IMAGE_OPTIONAL_HEADER64_SIZE = 240
IMAGE_NT_HEADERS32_SIZE = 4 + IMAGE_FILE_HEADER_SIZE + IMAGE_OPTIONAL_HEADER32_SIZE
IMAGE_NT_HEADERS64_SIZE = 4 + IMAGE_FILE_HEADER_SIZE + IMAGE_OPTIONAL_HEADER64_SIZE
IMAGE_SECTION_HEADER_SIZE = 40
IMAGE_TLS_DIRECTORY32_SIZE = 24
IMAGE_TLS_DIRECTORY64_SIZE = 40
IMAGE_FILE_MACHINE_I386 = 332
IMAGE_FILE_MACHINE_AMD64 = 34404
IMAGE_NT_OPTIONAL_HDR32_MAGIC = 267
IMAGE_NT_OPTIONAL_HDR64_MAGIC = 523
IMAGE_DIRECTORY_ENTRY_TLS = 9
STEAMSTUB_V3_0_SIGNATURE = 3235823838
STEAMSTUB_V3_1_SIGNATURE = 3235823839
STUB_FLAG_NO_ENCRYPTION = 4
def align_up(value: int, alignment: int) -> int:
    if alignment == 0:
        return value
    return value + alignment - 1 & ~(alignment - 1)
def pattern_find(buf: bytes, pattern: str, start: int=0) -> int:
    tokens = pattern.split()
    plen = len(tokens)
    mask = [t == '??' for t in tokens]
    bytes_pat = [0 if m else int(t, 16) for m, t in zip(mask, tokens)]
    end = len(buf) - plen
    for i in range(start, end + 1):
        ok = True
        for j in range(plen):
            if not mask[j] and buf[i + j] != bytes_pat[j]:
                ok = False
                break
        if ok:
            return i
    return -1
def steam_xor(data: bytearray, size: int, key: int=0) -> int:
    offset = 0
    if key == 0:
        if size < 4:
            return 0
        key = struct.unpack_from('<I', data, 0)[0]
        offset = 4
    x = offset
    while x + 4 <= size:
        val = struct.unpack_from('<I', data, x)[0]
        struct.pack_into('<I', data, x, (val ^ key) & 4294967295)
        key = val
        x += 4
    return key
def xtea_decrypt_pass2(keys: List[int], v1: int, v2: int, rounds: int=32) -> Tuple[int, int]:
    mask = 4294967295
    delta = 2654435769
    s = delta * rounds & mask
    for _ in range(rounds):
        t = (v1 << 4 & mask ^ v1 >> 5) + v1
        v2 = v2 - (t ^ s + keys[s >> 11 & 3] & mask) & mask
        s = s - delta & mask
        t = (v2 << 4 & mask ^ v2 >> 5) + v2
        v1 = v1 - (t ^ s + keys[s & 3] & mask) & mask
    return (v1, v2)
def xtea_decrypt_pass1(data: bytearray, size: int, keys: List[int]) -> None:
    mask = 4294967295
    v1 = 1431655765
    v2 = 1431655765
    x = 0
    while x + 8 <= size:
        d1 = struct.unpack_from('<I', data, x)[0]
        d2 = struct.unpack_from('<I', data, x + 4)[0]
        r1, r2 = xtea_decrypt_pass2(keys, d1, d2)
        struct.pack_into('<I', data, x, (r1 ^ v1) & mask)
        struct.pack_into('<I', data, x + 4, (r2 ^ v2) & mask)
        v1 = d1
        v2 = d2
        x += 8
class AesHelper:
    def __init__(self, key: bytes, iv: bytes):
        self.key = bytes(key)
        self.iv = bytes(iv)
    def rebuild_iv(self) -> None:
        cipher = AES.new(self.key, AES.MODE_ECB)
        self.iv = cipher.decrypt(self.iv)
    def decrypt(self, data: bytes) -> bytes:
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        return cipher.decrypt(data)
@dataclass
class Section:
    name: bytes
    virtual_size: int
    virtual_address: int
    size_of_raw_data: int
    pointer_to_raw_data: int
    pointer_to_relocations: int
    pointer_to_linenumbers: int
    number_of_relocations: int
    number_of_linenumbers: int
    characteristics: int
    raw: bytearray = field(default_factory=bytearray)
    def pack(self) -> bytes:
        return struct.pack('<8sIIIIIIHHI', self.name.ljust(8, b'\x00')[:8], self.virtual_size, self.virtual_address, self.size_of_raw_data, self.pointer_to_raw_data, self.pointer_to_relocations, self.pointer_to_linenumbers, self.number_of_relocations, self.number_of_linenumbers, self.characteristics)
    @property
    def section_name(self) -> str:
        return self.name.rstrip(b'\x00').decode('ascii', errors='replace')
class PEFile:
    def __init__(self, path: str):
        self.path = path
        with open(path, 'rb') as fh:
            self.file_data = bytearray(fh.read())
        self.dos_header = bytes(self.file_data[:IMAGE_DOS_HEADER_SIZE])
        self.dos_stub_data = bytearray()
        self.e_lfanew = struct.unpack_from('<I', self.dos_header, 60)[0]
        self.dos_stub_data = bytearray(self.file_data[IMAGE_DOS_HEADER_SIZE:self.e_lfanew])
        sig = struct.unpack_from('<I', self.file_data, self.e_lfanew)[0]
        if sig != 17744:
            raise ValueError('Not a valid PE file (missing PE\\0\\0 signature).')
        file_hdr_off = self.e_lfanew + 4
        self.file_header = bytearray(self.file_data[file_hdr_off:file_hdr_off + IMAGE_FILE_HEADER_SIZE])
        self.machine = struct.unpack_from('<H', self.file_header, 0)[0]
        self.num_sections = struct.unpack_from('<H', self.file_header, 2)[0]
        self.size_of_optional_header = struct.unpack_from('<H', self.file_header, 16)[0]
        opt_off = file_hdr_off + IMAGE_FILE_HEADER_SIZE
        magic = struct.unpack_from('<H', self.file_data, opt_off)[0]
        if magic == IMAGE_NT_OPTIONAL_HDR32_MAGIC:
            self.is_64 = False
            self.opt_size = IMAGE_OPTIONAL_HEADER32_SIZE
        elif magic == IMAGE_NT_OPTIONAL_HDR64_MAGIC:
            self.is_64 = True
            self.opt_size = IMAGE_OPTIONAL_HEADER64_SIZE
        else:
            raise ValueError(f'Unknown optional header magic: 0x{magic:X}')
        self.optional_header = bytearray(self.file_data[opt_off:opt_off + self.opt_size])
        sec_off = opt_off + self.size_of_optional_header
        self.sections: List[Section] = []
        for i in range(self.num_sections):
            off = sec_off + i * IMAGE_SECTION_HEADER_SIZE
            fields = struct.unpack_from('<8sIIIIIIHHI', self.file_data, off)
            s = Section(*fields)
            if s.pointer_to_raw_data and s.size_of_raw_data:
                s.raw = bytearray(self.file_data[s.pointer_to_raw_data:s.pointer_to_raw_data + s.size_of_raw_data])
            self.sections.append(s)
    def _get_u32(self, off: int) -> int:
        return struct.unpack_from('<I', self.optional_header, off)[0]
    def _set_u32(self, off: int, val: int) -> None:
        struct.pack_into('<I', self.optional_header, off, val & 4294967295)
    def _get_u64(self, off: int) -> int:
        return struct.unpack_from('<Q', self.optional_header, off)[0]
    def _set_u64(self, off: int, val: int) -> None:
        struct.pack_into('<Q', self.optional_header, off, val & 18446744073709551615)
    @property
    def address_of_entry_point(self) -> int:
        return self._get_u32(16)
    @address_of_entry_point.setter
    def address_of_entry_point(self, v: int) -> None:
        self._set_u32(16, v)
    @property
    def image_base(self) -> int:
        if self.is_64:
            return self._get_u64(24)
        return self._get_u32(28)
    @image_base.setter
    def image_base(self, v: int) -> None:
        if self.is_64:
            self._set_u64(24, v)
        else:
            self._set_u32(28, v)
    @property
    def section_alignment(self) -> int:
        return self._get_u32(32)
    @property
    def file_alignment(self) -> int:
        return self._get_u32(36)
    @property
    def size_of_image(self) -> int:
        return self._get_u32(56)
    @size_of_image.setter
    def size_of_image(self, v: int) -> None:
        self._set_u32(56, v)
    @property
    def checksum(self) -> int:
        return self._get_u32(64)
    @checksum.setter
    def checksum(self, v: int) -> None:
        self._set_u32(64, v)
    @property
    def number_of_rva_and_sizes(self) -> int:
        return self._get_u32(108 if self.is_64 else 92)
    def data_directory(self, index: int) -> Tuple[int, int]:
        base = (112 if self.is_64 else 96) + index * 8
        va = struct.unpack_from('<I', self.optional_header, base)[0]
        sz = struct.unpack_from('<I', self.optional_header, base + 4)[0]
        return (va, sz)
    def set_data_directory(self, index: int, va: int, size: int) -> None:
        base = (112 if self.is_64 else 96) + index * 8
        struct.pack_into('<II', self.optional_header, base, va & 4294967295, size & 4294967295)
    def get_section_by_name(self, name: str) -> Optional[Section]:
        target = name.encode('ascii')
        for s in self.sections:
            if s.name.rstrip(b'\x00') == target:
                return s
        return None
    def get_owner_section(self, rva: int) -> Optional[Section]:
        for s in self.sections:
            if s.virtual_address <= rva < s.virtual_address + max(s.virtual_size, s.size_of_raw_data):
                return s
        return None
    def rva_to_file_offset(self, rva: int) -> int:
        s = self.get_owner_section(rva)
        if s is None:
            return -1
        return rva - (s.virtual_address - s.pointer_to_raw_data)
    def va_to_rva(self, va: int) -> int:
        return va - self.image_base
    def va_to_file_offset(self, va: int) -> int:
        return self.rva_to_file_offset(va - self.image_base)
    @property
    def tls_callbacks(self) -> List[int]:
        tls_va, tls_sz = self.data_directory(IMAGE_DIRECTORY_ENTRY_TLS)
        if tls_va == 0 or tls_sz == 0:
            return []
        tls_off = self.rva_to_file_offset(tls_va)
        if tls_off < 0:
            return []
        if self.is_64:
            cb_va = struct.unpack_from('<Q', self.file_data, tls_off + 24)[0]
        else:
            cb_va = struct.unpack_from('<I', self.file_data, tls_off + 12)[0]
        if cb_va == 0:
            return []
        cb_off = self.rva_to_file_offset(cb_va - self.image_base)
        if cb_off < 0:
            return []
        out = []
        step = 8 if self.is_64 else 4
        fmt = '<Q' if self.is_64 else '<I'
        while cb_off + step <= len(self.file_data):
            v = struct.unpack_from(fmt, self.file_data, cb_off)[0]
            if v == 0:
                break
            out.append(v)
            cb_off += step
        return out
    def remove_section(self, section: Section) -> None:
        self.sections.remove(section)
        struct.pack_into('<H', self.file_header, 2, len(self.sections))
    def rebuild_sections(self) -> None:
        if not self.sections:
            return
        sec_align = self.section_alignment
        file_align = self.file_alignment
        self.sections.sort(key=lambda s: s.virtual_address)
        size_of_headers = self._get_u32(60)
        current_va = align_up(size_of_headers, sec_align)
        current_raw = align_up(size_of_headers, file_align)
        for s in self.sections:
            s.virtual_size = align_up(s.virtual_size, sec_align) if s.virtual_size else s.virtual_size
            s.size_of_raw_data = align_up(s.size_of_raw_data, file_align) if s.size_of_raw_data else 0
            if s.size_of_raw_data and len(s.raw) < s.size_of_raw_data:
                s.raw += bytearray(s.size_of_raw_data - len(s.raw))
        last = self.sections[-1]
        self.size_of_image = align_up(last.virtual_address + last.virtual_size, sec_align)
    def write(self, out_path: str, zero_dos_stub: bool=False, recalc_checksum: bool=False) -> None:
        self.checksum = 0
        dos = bytearray(self.dos_header)
        stub = bytearray(self.dos_stub_data)
        if zero_dos_stub:
            for i in range(len(stub)):
                stub[i] = 0
        e_lfanew = IMAGE_DOS_HEADER_SIZE + len(stub)
        struct.pack_into('<I', dos, 60, e_lfanew)
        nt_sig = b'PE\x00\x00'
        struct.pack_into('<H', self.file_header, 16, self.opt_size)
        struct.pack_into('<H', self.file_header, 2, len(self.sections))
        headers = bytes(dos) + bytes(stub) + nt_sig + bytes(self.file_header) + bytes(self.optional_header)
        for s in self.sections:
            headers += s.pack()
        sorted_secs = sorted(self.sections, key=lambda s: s.pointer_to_raw_data if s.pointer_to_raw_data else 1 << 62)
        if sorted_secs and sorted_secs[0].pointer_to_raw_data:
            first_prd = sorted_secs[0].pointer_to_raw_data
            if len(headers) < first_prd:
                headers = headers + bytes(first_prd - len(headers))
            elif len(headers) > first_prd:
                print(f'[!] Header overflow: headers size {len(headers)} > first PRD {first_prd}')
        max_end = len(headers)
        for s in self.sections:
            if s.pointer_to_raw_data and s.size_of_raw_data:
                end = s.pointer_to_raw_data + s.size_of_raw_data
                if end > max_end:
                    max_end = end
        out = bytearray(max_end)
        out[:len(headers)] = headers
        for s in self.sections:
            if s.pointer_to_raw_data and s.size_of_raw_data:
                data = s.raw[:s.size_of_raw_data]
                if len(data) < s.size_of_raw_data:
                    data = data + bytearray(s.size_of_raw_data - len(data))
                out[s.pointer_to_raw_data:s.pointer_to_raw_data + s.size_of_raw_data] = data
        with open(out_path, 'wb') as fh:
            fh.write(out)
        if recalc_checksum:
            try:
                import ctypes
                ih = ctypes.windll.imagehlp
                MapFileAndCheckSumA = ih.MapFileAndCheckSumA
                MapFileAndCheckSumA.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
                MapFileAndCheckSumA.restype = ctypes.c_uint32
                orig = ctypes.c_uint32(0)
                new = ctypes.c_uint32(0)
                ret = MapFileAndCheckSumA(out_path.encode('mbcs'), ctypes.byref(orig), ctypes.byref(new))
                if ret == 0:
                    with open(out_path, 'r+b') as fh:
                        fh.seek(e_lfanew + 4 + IMAGE_FILE_HEADER_SIZE + 64)
                        fh.write(struct.pack('<I', new.value))
                else:
                    print(f'[!] MapFileAndCheckSum failed with {ret}')
            except Exception as e:
                print(f'[!] Could not recalc checksum (not on Windows?): {e}')
def _require_capstone():
    if not HAVE_CAPSTONE:
        raise RuntimeError('capstone is required for SteamStub v2.0/v2.1. Install: pip install capstone')
def _scan_entry_mov_imm(pe: PEFile, max_insn: int=1024):
    _require_capstone()
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    ep_rva = pe.address_of_entry_point
    ep_off = pe.rva_to_file_offset(ep_rva)
    if ep_off < 0:
        return []
    code = bytes(pe.file_data[ep_off:ep_off + 16384])
    return list(md.disasm(code, pe.image_base + ep_rva))
@dataclass
class Options:
    keep_bind_section: bool = False
    zero_dos_stub: bool = False
    dont_realign_sections: bool = False
    recalculate_checksum: bool = False
    dump_payload: bool = False
    dump_drmp: bool = False
    use_experimental: bool = False
    strip_dsstext: bool = False
class Variant10x86:
    name = 'SteamStub Variant 1.0 (x86)'
    @staticmethod
    def detect(pe: PEFile) -> bool:
        if pe.is_64:
            return False
        bind = pe.get_section_by_name('.bind')
        if bind is None:
            return False
        pat = '60 81 EC 00 10 00 00 BE ?? ?? ?? ?? B9'
        return pattern_find(bytes(bind.raw), pat) != -1
    def process(self, pe: PEFile, opts: Options) -> bool:
        bind = pe.get_section_by_name('.bind')
        if bind is None:
            return False
        bind_bytes = bytes(bind.raw)
        off = pattern_find(bind_bytes, '60 81 EC 00 10 00 00 BE ?? ?? ?? ?? B9')
        if off < 0:
            return False
        header_va = struct.unpack_from('<I', bind_bytes, off + 8)[0]
        header_rva = header_va - pe.image_base
        header_size = bind_bytes[off + 13] * 4
        header_off_in_bind = header_rva - bind.virtual_address
        if header_off_in_bind < 0 or header_off_in_bind + header_size > len(bind_bytes):
            print('[!] V1.0: header out of range')
            return False
        header = bytearray(bind_bytes[header_off_in_bind:header_off_in_bind + header_size])
        for x in range(header_size):
            header[x] ^= x * x & 255
        expected = pe.address_of_entry_point + pe.image_base
        found = False
        for dw_off in range(0, min(32, len(header)), 4):
            if struct.unpack_from('<I', header, dw_off)[0] == expected:
                found = True
                break
        if not found:
            bind_function = struct.unpack_from('<I', header, 8)[0]
            print(f'[!] V1.0: BindFunction not found (hdr[8]=0x{bind_function:X}, expected 0x{expected:X})')
            return False
        oep_off = pattern_find(bind_bytes, '61 B8 ?? ?? ?? ?? FF E0')
        if oep_off < 0:
            print('[!] V1.0: OEP pattern not found')
            return False
        oep_va = struct.unpack_from('<I', bind_bytes, oep_off + 2)[0]
        oep_rva = oep_va - pe.image_base
        pe.address_of_entry_point = oep_rva
        if not opts.keep_bind_section:
            pe.remove_section(bind)
        if not opts.dont_realign_sections:
            pe.rebuild_sections()
        return True
class Variant20x86:
    name = 'SteamStub Variant 2.0 (x86)'
    @staticmethod
    def detect(pe: PEFile) -> bool:
        if pe.is_64:
            return False
        bind = pe.get_section_by_name('.bind')
        if bind is None:
            return False
        return pattern_find(bytes(bind.raw), '53 51 52 56 57 55 8B EC 81 EC 00 10 00 00 BE') != -1
    def process(self, pe: PEFile, opts: Options) -> bool:
        ep_off = pe.rva_to_file_offset(pe.address_of_entry_point)
        if ep_off < 4:
            return False
        sig = struct.unpack_from('<I', pe.file_data, ep_off - 4)[0]
        if sig != STEAMSTUB_V3_0_SIGNATURE:
            print(f'[!] V2.0: signature {STEAMSTUB_V3_0_SIGNATURE:#X} not found')
            return False
        insns = _scan_entry_mov_imm(pe)
        struct_offset = None
        struct_size = None
        for ins in insns[:20]:
            if ins.mnemonic == 'mov' and len(ins.operands) == 2 and (ins.operands[1].type == X86_OP_IMM):
                imm = ins.operands[1].imm & 4294967295
                if struct_offset is None:
                    struct_offset = imm - pe.image_base
                elif struct_size is None:
                    struct_size = imm * 4 & 4294967295
                    break
        if struct_offset is None or struct_size is None:
            print('[!] V2.0: could not locate header offset/size')
            return False
        header_file_off = pe.rva_to_file_offset(struct_offset)
        if header_file_off < 0:
            return False
        header = bytearray(pe.file_data[header_file_off:header_file_off + struct_size])
        steam_xor(header, len(header), 1)
        if len(header) not in (856, 884, 952):
            print(f'[!] V2.0: unexpected header size {len(header)}')
        flags = struct.unpack_from('<I', header, 0)[0]
        if len(header) == 856:
            oep = struct.unpack_from('<I', header, 20)[0]
            code_va = struct.unpack_from('<I', header, 36)[0]
            code_size = struct.unpack_from('<I', header, 40)[0]
            code_xor = struct.unpack_from('<I', header, 44)[0]
        elif len(header) == 884:
            oep = struct.unpack_from('<I', header, 20)[0]
            code_va = struct.unpack_from('<I', header, 40)[0]
            code_size = struct.unpack_from('<I', header, 44)[0]
            code_xor = struct.unpack_from('<I', header, 48)[0]
        else:
            oep = struct.unpack_from('<I', header, 20)[0]
            code_va = struct.unpack_from('<I', header, 48)[0]
            code_size = struct.unpack_from('<I', header, 52)[0]
            code_xor = struct.unpack_from('<I', header, 56)[0]
        oep_rva = oep - pe.image_base
        if flags & 4:
            code_file_off = pe.rva_to_file_offset(code_va)
            if code_file_off >= 0 and code_size > 0:
                buf = bytearray(pe.file_data[code_file_off:code_file_off + code_size])
                steam_xor(buf, len(buf), code_xor)
                owner = pe.get_owner_section(code_va)
                if owner:
                    rel = code_va - owner.virtual_address
                    owner.raw[rel:rel + code_size] = buf
        pe.address_of_entry_point = oep_rva
        bind = pe.get_section_by_name('.bind')
        if bind and (not opts.keep_bind_section):
            pe.remove_section(bind)
        if not opts.dont_realign_sections:
            pe.rebuild_sections()
        return True
class Variant21x86:
    name = 'SteamStub Variant 2.1 (x86)'
    @staticmethod
    def detect(pe: PEFile) -> bool:
        if pe.is_64:
            return False
        bind = pe.get_section_by_name('.bind')
        if bind is None:
            return False
        return pattern_find(bytes(bind.raw), '53 51 52 56 57 55 8B EC 81 EC 00 10 00 00 C7') != -1
    def _disasm_entry(self, pe: PEFile):
        _require_capstone()
        md = Cs(CS_ARCH_X86, CS_MODE_32)
        md.detail = True
        ep_rva = pe.address_of_entry_point
        ep_off = pe.rva_to_file_offset(ep_rva)
        code = bytes(pe.file_data[ep_off:ep_off + 4096])
        struct_offset = 0
        struct_xor_key = 0
        struct_size = 0
        for ins in md.disasm(code, pe.image_base + ep_rva):
            if struct_offset and struct_size and struct_xor_key:
                return (struct_offset, struct_size, struct_xor_key)
            if ins.mnemonic == 'mov' and len(ins.operands) == 2:
                o0, o1 = (ins.operands[0], ins.operands[1])
                if o1.type == X86_OP_IMM:
                    imm = o1.imm & 4294967295
                    if o0.type == X86_OP_MEM:
                        if struct_offset == 0:
                            struct_offset = imm - pe.image_base & 4294967295
                        else:
                            struct_xor_key = imm
                    elif o0.type == X86_OP_REG:
                        struct_size = imm * 4 & 4294967295
        return (None, None, None)
    def process(self, pe: PEFile, opts: Options) -> bool:
        ep_off = pe.rva_to_file_offset(pe.address_of_entry_point)
        if ep_off < 4:
            return False
        sig = struct.unpack_from('<I', pe.file_data, ep_off - 4)[0]
        if sig != STEAMSTUB_V3_0_SIGNATURE:
            print(f'[!] V2.1: signature {STEAMSTUB_V3_0_SIGNATURE:#X} not found')
            return False
        struct_offset, struct_size, struct_xor_key = self._disasm_entry(pe)
        if struct_offset is None:
            print('[!] V2.1: could not locate structure info')
            return False
        header_file_off = pe.rva_to_file_offset(struct_offset)
        if header_file_off < 0:
            return False
        header = bytearray(pe.file_data[header_file_off:header_file_off + struct_size])
        xor_key = steam_xor(header, len(header), struct_xor_key)
        is_d0 = struct_size // 4 == 208
        if is_d0:
            off_payload_va = 32
            off_payload_size = 36
            off_drmp_va = 56
            off_drmp_size = 60
            off_xtea_keys = 64
        else:
            off_payload_va = 36
            off_payload_size = 40
            off_drmp_va = 60
            off_drmp_size = 64
            off_xtea_keys = 68
        payload_va = struct.unpack_from('<I', header, off_payload_va)[0]
        payload_size = struct.unpack_from('<I', header, off_payload_size)[0]
        drmp_va_off = struct.unpack_from('<I', header, off_drmp_va)[0]
        drmp_size_off = struct.unpack_from('<I', header, off_drmp_size)[0]
        xtea_keys_off = struct.unpack_from('<I', header, off_xtea_keys)[0]
        payload_addr = pe.rva_to_file_offset(payload_va - pe.image_base)
        if payload_addr < 0:
            print('[!] V2.1: payload VA not resolvable')
            return False
        payload = bytearray(pe.file_data[payload_addr:payload_addr + payload_size])
        xor_key = steam_xor(payload, len(payload), xor_key)
        if opts.dump_payload:
            with open(pe.path + '.payload', 'wb') as fh:
                fh.write(payload)
        drmp_va = struct.unpack_from('<I', payload, drmp_va_off)[0]
        drmp_size = struct.unpack_from('<I', payload, drmp_size_off)[0]
        drmp_addr = pe.rva_to_file_offset(drmp_va - pe.image_base)
        drmp = bytearray(pe.file_data[drmp_addr:drmp_addr + drmp_size])
        xtea_keys = []
        p = xtea_keys_off
        while p + 4 <= len(payload):
            xtea_keys.append(struct.unpack_from('<I', payload, p)[0])
            p += 4
        xtea_decrypt_pass1(drmp, len(drmp), xtea_keys)
        if opts.dump_drmp:
            with open(pe.path + '.SteamDRMP.dll', 'wb') as fh:
                fh.write(drmp)
        drmp_block = pattern_find(bytes(drmp), '8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8D ?? ?? ?? ?? ?? 05')
        use_fallback = False
        if drmp_block == -1:
            drmp_block = pattern_find(bytes(drmp), '8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B')
            if drmp_block == -1:
                drmp_block = pattern_find(bytes(drmp), '8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? A3 ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? A3 ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? A3 ?? ?? ?? ?? 8B')
                if drmp_block == -1:
                    print('[!] V2.1: could not find SteamDRMP offsets block')
                    return False
                use_fallback = True
        block = bytes(drmp[drmp_block:drmp_block + 1024])
        if use_fallback:
            positions = (2, 14, 25, 36, 47, 61, 72)
        else:
            positions = (2, 14, 26, 38, 50, 62, 67)
        offs = [struct.unpack_from('<i', block, positions[i])[0] for i in range(7)]
        aes_iv_off = offs[6]
        offs.append(aes_iv_off + 16)
        flags = struct.unpack_from('<I', payload, offs[0])[0]
        code_va_raw = struct.unpack_from('<I', payload, offs[3])[0]
        code_section = pe.get_owner_section(code_va_raw - pe.image_base)
        oep_va = struct.unpack_from('<I', payload, offs[2])[0]
        if not flags & STUB_FLAG_NO_ENCRYPTION and code_section is not None:
            aes_key = bytes(payload[offs[5]:offs[5] + 32])
            aes_iv = bytes(payload[offs[6]:offs[6] + 16])
            stolen = bytes(payload[offs[7]:offs[7] + 16])
            encrypted_size = struct.unpack_from('<I', payload, offs[4])[0]
            code_file_off = pe.rva_to_file_offset(code_section.virtual_address)
            cipher = bytes(pe.file_data[code_file_off:code_file_off + encrypted_size])
            combined = stolen + cipher
            combined_trim = combined[:len(combined) // 16 * 16]
            aes = AesHelper(aes_key, aes_iv)
            aes.rebuild_iv()
            dec = aes.decrypt(combined_trim)
            rel = 0
            code_section.raw[rel:rel + len(dec)] = dec
        pe.address_of_entry_point = oep_va - pe.image_base & 4294967295
        bind = pe.get_section_by_name('.bind')
        if bind and (not opts.keep_bind_section):
            pe.remove_section(bind)
        if not opts.dont_realign_sections:
            pe.rebuild_sections()
        return True
V3_BIND_PATTERN_X86 = 'E8 00 00 00 00 50 53 51 52 56 57 55 8B 44 24 1C 2D 05 00 00 00 8B CC 83 E4 F0 51 51 51 50'
V3_BIND_PATTERN_X64 = 'E8 00 00 00 00 50 53 51 52 56 57 55 41 50'
V3_HEADER_SIZE_PATTERNS_X86 = [('55 8B EC 81 EC ?? ?? ?? ?? 53 ?? ?? ?? ?? ?? 68', 16), ('55 8B EC 81 EC ?? ?? ?? ?? 53 ?? ?? ?? ?? ?? 8D 83', 22), ('55 8B EC 81 EC ?? ?? ?? ?? 56 ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? 8D', 16)]
V3_HEADER_SIZE_PATTERNS_X64 = [('48 8D 91 ?? ?? ?? ?? 48', 3), ('48 8D 91 ?? ?? ?? ?? 41', 3), ('48 C7 84 24 ?? ?? ?? ?? ?? ?? ?? ?? 48', 8)]
def _detect_v3_header_size(bind_bytes: bytes, patterns) -> int:
    for pat, imm_off in patterns:
        off = pattern_find(bind_bytes, pat)
        if off >= 0:
            val = struct.unpack_from('<i', bind_bytes, off + imm_off)[0]
            return abs(val)
    return 0
class Variant30x86:
    name = 'SteamStub Variant 3.0 (x86)'
    SIGNATURE = STEAMSTUB_V3_0_SIGNATURE
    HEADER_SIZES = (176, 208)
    HEADER_SIZE_PATTERNS = V3_HEADER_SIZE_PATTERNS_X86
    BIND_PATTERN = V3_BIND_PATTERN_X86
    @classmethod
    def detect(cls, pe: PEFile) -> bool:
        if pe.is_64:
            return False
        bind = pe.get_section_by_name('.bind')
        if bind is None:
            return False
        if pattern_find(bytes(bind.raw), cls.BIND_PATTERN) == -1:
            return False
        hs = _detect_v3_header_size(bytes(bind.raw), cls.HEADER_SIZE_PATTERNS)
        return hs in cls.HEADER_SIZES
    def _parse_header(self, header: bytes):
        hdr = {}
        off = 0
        hdr['XorKey'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Signature'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['ImageBase'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['AddressOfEntryPoint'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['BindSectionOffset'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0000'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['OriginalEntryPoint'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0001'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['PayloadSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['DRMPDllOffset'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['DRMPDllSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['SteamAppId'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Flags'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['BindSectionVirtualSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0002'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['CodeSectionVirtualAddress'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['CodeSectionRawSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['AES_Key'] = bytes(header[off:off + 32])
        off += 32
        hdr['AES_IV'] = bytes(header[off:off + 16])
        off += 16
        hdr['CodeSectionStolenData'] = bytes(header[off:off + 16])
        off += 16
        hdr['EncryptionKeys'] = [struct.unpack_from('<I', header, off + i * 4)[0] for i in range(4)]
        off += 16
        return hdr
    def process(self, pe: PEFile, opts: Options) -> bool:
        bind = pe.get_section_by_name('.bind')
        if bind is None:
            return False
        bind_bytes = bytes(bind.raw)
        if self.__class__.HEADER_SIZES == (240,):
            header_size = 240
        else:
            header_size = _detect_v3_header_size(bind_bytes, self.__class__.HEADER_SIZE_PATTERNS)
            if header_size not in self.HEADER_SIZES:
                header_size = 176
        ep_off = pe.rva_to_file_offset(pe.address_of_entry_point)
        if ep_off < 0:
            return False
        header_file_off = ep_off - header_size
        header = bytearray(pe.file_data[header_file_off:header_file_off + header_size])
        xor_key = steam_xor(header, len(header), 0)
        hdr = self._parse_header(header)
        if hdr['Signature'] != self.SIGNATURE:
            print(f"[!] {self.name}: bad signature 0x{hdr['Signature']:X}")
            return False
        payload_size = hdr['PayloadSize'] + 15 & 4294967280
        payload_rva = pe.address_of_entry_point - hdr['BindSectionOffset']
        payload_off = pe.rva_to_file_offset(payload_rva)
        if payload_off < 0:
            payload_off = header_file_off + header_size
        payload = bytearray(pe.file_data[payload_off:payload_off + payload_size])
        if len(payload):
            steam_xor(payload, len(payload), xor_key)
            if opts.dump_payload:
                with open(pe.path + '.payload', 'wb') as fh:
                    fh.write(payload)
            drmp = bytearray(payload[hdr['DRMPDllOffset']:hdr['DRMPDllOffset'] + hdr['DRMPDllSize']])
            xtea_decrypt_pass1(drmp, len(drmp), hdr['EncryptionKeys'])
            if opts.dump_drmp:
                with open(pe.path + '.SteamDRMP.dll', 'wb') as fh:
                    fh.write(drmp)
        if not hdr['Flags'] & STUB_FLAG_NO_ENCRYPTION and hdr['CodeSectionRawSize']:
            code_va = hdr['CodeSectionVirtualAddress']
            code_raw = hdr['CodeSectionRawSize']
            code_file_off = pe.rva_to_file_offset(code_va)
            if code_file_off >= 0:
                cipher_data = bytes(pe.file_data[code_file_off:code_file_off + code_raw])
                aes = AesHelper(hdr['AES_Key'], hdr['AES_IV'])
                aes.rebuild_iv()
                combined = hdr['CodeSectionStolenData'] + cipher_data
                combined_trim = combined[:len(combined) // 16 * 16]
                dec = aes.decrypt(combined_trim)
                owner = pe.get_owner_section(code_va)
                if owner:
                    rel = code_va - owner.virtual_address
                    owner.raw[rel:rel + len(dec)] = dec
        pe.address_of_entry_point = hdr['OriginalEntryPoint']
        if bind and (not opts.keep_bind_section):
            pe.remove_section(bind)
        if not opts.dont_realign_sections:
            pe.rebuild_sections()
        return True
class Variant30x64:
    name = 'SteamStub Variant 3.0 (x64)'
    SIGNATURE = STEAMSTUB_V3_0_SIGNATURE
    HEADER_SIZES = (176, 208)
    HEADER_SIZE_PATTERNS = V3_HEADER_SIZE_PATTERNS_X64
    BIND_PATTERN = V3_BIND_PATTERN_X64
    @classmethod
    def detect(cls, pe: PEFile) -> bool:
        if not pe.is_64:
            return False
        bind = pe.get_section_by_name('.bind')
        if bind is None:
            return False
        if pattern_find(bytes(bind.raw), cls.BIND_PATTERN) == -1:
            return False
        hs = _detect_v3_header_size(bytes(bind.raw), cls.HEADER_SIZE_PATTERNS)
        return hs in cls.HEADER_SIZES
    def _parse_header(self, header: bytes):
        hdr = {}
        off = 0
        hdr['XorKey'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Signature'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['ImageBase'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['AddressOfEntryPoint'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['BindSectionOffset'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0000'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['OriginalEntryPoint'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0001'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['PayloadSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['DRMPDllOffset'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['DRMPDllSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['SteamAppId'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Flags'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['BindSectionVirtualSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0002'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['CodeSectionVirtualAddress'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['CodeSectionRawSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['AES_Key'] = bytes(header[off:off + 32])
        off += 32
        hdr['AES_IV'] = bytes(header[off:off + 16])
        off += 16
        hdr['CodeSectionStolenData'] = bytes(header[off:off + 16])
        off += 16
        hdr['EncryptionKeys'] = [struct.unpack_from('<I', header, off + i * 4)[0] for i in range(4)]
        off += 16
        if off + 4 <= len(header):
            hdr['HasTlsCallback'] = struct.unpack_from('<I', header, off)[0]
            off += 4
        else:
            hdr['HasTlsCallback'] = 0
        return hdr
    def process(self, pe: PEFile, opts: Options) -> bool:
        bind = pe.get_section_by_name('.bind')
        if bind is None:
            return False
        bind_bytes = bytes(bind.raw)
        if self.__class__.HEADER_SIZES == (240,):
            header_size = 240
        else:
            header_size = _detect_v3_header_size(bind_bytes, self.__class__.HEADER_SIZE_PATTERNS)
            if header_size not in self.HEADER_SIZES:
                header_size = 208
        tls_as_oep_rva = None
        header = None
        xor_key = 0
        hdr = None
        candidate_rvas = [pe.address_of_entry_point] + [cb - pe.image_base for cb in pe.tls_callbacks]
        for loc_rva in candidate_rvas:
            loc_off = pe.rva_to_file_offset(loc_rva)
            if loc_off < 0 or loc_off - header_size < 0:
                continue
            h = bytearray(pe.file_data[loc_off - header_size:loc_off])
            k = steam_xor(h, len(h), 0)
            hd = self._parse_header(h)
            if hd['Signature'] == self.SIGNATURE:
                if loc_rva != pe.address_of_entry_point:
                    tls_as_oep_rva = loc_rva
                header = h
                xor_key = k
                hdr = hd
                header_file_off = loc_off - header_size
                break
        if hdr is None:
            print(f'[!] {self.name}: bad signature (tried EP + {len(pe.tls_callbacks)} TLS callback(s))')
            return False
        payload_size = hdr['PayloadSize'] + 15 & 4294967280
        ref_rva = tls_as_oep_rva if tls_as_oep_rva is not None else pe.address_of_entry_point
        payload_rva = ref_rva - hdr['BindSectionOffset']
        payload_off = pe.rva_to_file_offset(payload_rva)
        if payload_off < 0:
            payload_off = header_file_off + header_size
        payload = bytearray(pe.file_data[payload_off:payload_off + payload_size])
        if len(payload):
            steam_xor(payload, len(payload), xor_key)
            if opts.dump_payload:
                with open(pe.path + '.payload', 'wb') as fh:
                    fh.write(payload)
            drmp = bytearray(payload[hdr['DRMPDllOffset']:hdr['DRMPDllOffset'] + hdr['DRMPDllSize']])
            xtea_decrypt_pass1(drmp, len(drmp), hdr['EncryptionKeys'])
            if opts.dump_drmp:
                with open(pe.path + '.SteamDRMP.dll', 'wb') as fh:
                    fh.write(drmp)
        if not hdr['Flags'] & STUB_FLAG_NO_ENCRYPTION and hdr['CodeSectionRawSize']:
            code_va = hdr['CodeSectionVirtualAddress']
            code_raw = hdr['CodeSectionRawSize']
            code_file_off = pe.rva_to_file_offset(code_va)
            if code_file_off >= 0:
                cipher_data = bytes(pe.file_data[code_file_off:code_file_off + code_raw])
                aes = AesHelper(hdr['AES_Key'], hdr['AES_IV'])
                aes.rebuild_iv()
                combined = hdr['CodeSectionStolenData'] + cipher_data
                combined_trim = combined[:len(combined) // 16 * 16]
                dec = aes.decrypt(combined_trim)
                owner = pe.get_owner_section(code_va)
                if owner:
                    rel = code_va - owner.virtual_address
                    owner.raw[rel:rel + len(dec)] = dec
        if tls_as_oep_rva is not None and hdr.get('HasTlsCallback') == 1 and pe.tls_callbacks:
            tls_va, _ = pe.data_directory(IMAGE_DIRECTORY_ENTRY_TLS)
            tls_off_in_file = pe.rva_to_file_offset(tls_va)
            cb_table_va = struct.unpack_from('<Q', pe.file_data, tls_off_in_file + 24)[0]
            cb_table_rva = cb_table_va - pe.image_base
            cb_owner = pe.get_owner_section(cb_table_rva)
            if cb_owner is not None:
                rel = cb_table_rva - cb_owner.virtual_address
                new_cb_va = pe.image_base + hdr['OriginalEntryPoint']
                struct.pack_into('<Q', cb_owner.raw, rel, new_cb_va)
            ep_off2 = pe.rva_to_file_offset(pe.address_of_entry_point)
            scan = bytes(pe.file_data[ep_off2:ep_off2 + 256])
            res = pattern_find(scan, '48 81 EA ?? ?? ?? ?? 8B 12 81 F2')
            if res != -1:
                k = hdr['XorKey'] ^ struct.unpack_from('<i', scan, res + 11)[0]
                tls_oep_override = pe.address_of_entry_point + k & 4294967295
                pe.address_of_entry_point = tls_oep_override
            else:
                pe.address_of_entry_point = hdr['OriginalEntryPoint'] & 4294967295
        else:
            pe.address_of_entry_point = hdr['OriginalEntryPoint'] & 4294967295
        if bind and (not opts.keep_bind_section):
            pe.remove_section(bind)
        if not opts.dont_realign_sections:
            pe.rebuild_sections()
        return True
class Variant31x86(Variant30x86):
    name = 'SteamStub Variant 3.1 (x86)'
    SIGNATURE = STEAMSTUB_V3_1_SIGNATURE
    HEADER_SIZES = (240,)
    HEADER_SIZE_PATTERNS = V3_HEADER_SIZE_PATTERNS_X86
    BIND_PATTERN = V3_BIND_PATTERN_X86
    def _parse_header(self, header: bytes):
        hdr = {}
        off = 0
        hdr['XorKey'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Signature'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['ImageBase'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['AddressOfEntryPoint'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['BindSectionOffset'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0000'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['OriginalEntryPoint'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['Unknown0001'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['PayloadSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['DRMPDllOffset'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['DRMPDllSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['SteamAppId'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Flags'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['BindSectionVirtualSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0002'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['CodeSectionVirtualAddress'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['CodeSectionRawSize'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['AES_Key'] = bytes(header[off:off + 32])
        off += 32
        hdr['AES_IV'] = bytes(header[off:off + 16])
        off += 16
        hdr['CodeSectionStolenData'] = bytes(header[off:off + 16])
        off += 16
        hdr['EncryptionKeys'] = [struct.unpack_from('<I', header, off + i * 4)[0] for i in range(4)]
        off += 16
        return hdr
    def process(self, pe: PEFile, opts: Options) -> bool:
        return super().process(pe, opts)
class Variant31x64(Variant30x64):
    name = 'SteamStub Variant 3.1 (x64)'
    SIGNATURE = STEAMSTUB_V3_1_SIGNATURE
    HEADER_SIZES = (240,)
    HEADER_SIZE_PATTERNS = V3_HEADER_SIZE_PATTERNS_X64
    BIND_PATTERN = V3_BIND_PATTERN_X64
    def _parse_header(self, header: bytes):
        hdr = {}
        off = 0
        hdr['XorKey'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Signature'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['ImageBase'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['AddressOfEntryPoint'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['BindSectionOffset'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0000'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['OriginalEntryPoint'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['Unknown0001'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['PayloadSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['DRMPDllOffset'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['DRMPDllSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['SteamAppId'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Flags'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['BindSectionVirtualSize'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['Unknown0002'] = struct.unpack_from('<I', header, off)[0]
        off += 4
        hdr['CodeSectionVirtualAddress'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['CodeSectionRawSize'] = struct.unpack_from('<Q', header, off)[0]
        off += 8
        hdr['AES_Key'] = bytes(header[off:off + 32])
        off += 32
        hdr['AES_IV'] = bytes(header[off:off + 16])
        off += 16
        hdr['CodeSectionStolenData'] = bytes(header[off:off + 16])
        off += 16
        hdr['EncryptionKeys'] = [struct.unpack_from('<I', header, off + i * 4)[0] for i in range(4)]
        off += 16
        if off + 4 <= len(header):
            hdr['HasTlsCallback'] = struct.unpack_from('<I', header, off)[0]
            off += 4
        else:
            hdr['HasTlsCallback'] = 0
        return hdr
class DSSProtector:
    name = 'DSS Protector (outer, wraps SteamStub)'
    EP_XOR_PATTERN = '8B 40 24 35 ?? ?? ?? ?? 03 45 ?? 89 45'
    @classmethod
    def detect(cls, pe: PEFile) -> Optional[dict]:
        if pe.is_64:
            return None
        if pe.get_section_by_name('.dsstext') is None:
            return None
        dos_key = struct.unpack_from('<I', pe.file_data, 36)[0]
        if dos_key == 0:
            return None
        ep_rva = pe.address_of_entry_point
        ep_sec = pe.get_owner_section(ep_rva)
        if ep_sec is None or ep_sec.section_name != '.dsstext':
            return None
        ep_off = pe.rva_to_file_offset(ep_rva)
        if ep_off < 0:
            return None
        scan_len = min(1024, len(pe.file_data) - ep_off)
        scan = bytes(pe.file_data[ep_off:ep_off + scan_len])
        match = pattern_find(scan, cls.EP_XOR_PATTERN)
        xor_const = None
        header_va = None
        if match != -1:
            xor_const = struct.unpack_from('<I', scan, match + 4)[0]
            header_va = pe.image_base + ((xor_const ^ dos_key) & 4294967295) & 4294967295
        return {'dos_key': dos_key, 'xor_const': xor_const, 'header_va': header_va, 'dsstext_section': pe.get_section_by_name('.dsstext'), 'bind_section': pe.get_section_by_name('.bind')}
    @classmethod
    def report(cls, pe: PEFile, info: dict) -> None:
        print('[!] DSS Protector (outer wrapper) detected')
        print(f"    .dsstext   VA=0x{info['dsstext_section'].virtual_address:X} size=0x{info['dsstext_section'].size_of_raw_data:X}")
        if info['bind_section'] is not None:
            b = info['bind_section']
            print(f'    .bind      VA=0x{b.virtual_address:X} size=0x{b.size_of_raw_data:X} (zlib-packed on disk - SteamStub only visible post-DSS)')
        print(f"    DOS[0x24]  = 0x{info['dos_key']:08X}  (DSS XOR key)")
        if info['xor_const'] is not None:
            print(f"    EP XOR const = 0x{info['xor_const']:08X}")
            print(f"    Runtime header VA = 0x{info['header_va']:X} (populated at runtime, zero on disk)")
        else:
            print('    [!] EP XOR constant pattern not found - newer DSS revision?')
        print('    DSS does not encrypt .bind/.text (only reroutes the PE entry point);')
        print('    static unwrap strips .dsstext and restores the SteamStub EP.')
    @classmethod
    def try_unwrap(cls, pe: PEFile, info: dict, opts: 'Options') -> bool:
        bind = info.get('bind_section')
        if bind is None:
            print('[DSS] no .bind section - cannot locate SteamStub EP')
            return False
        bind_bytes = bytes(bind.raw)
        if pe.is_64:
            pat = V3_BIND_PATTERN_X64
        else:
            pat = V3_BIND_PATTERN_X86
        stub_off = pattern_find(bind_bytes, pat)
        if stub_off < 0:
            print(f"[DSS] SteamStub v3 bind-stub pattern not found in .bind - can't determine original EP")
            return False
        steamstub_ep_rva = bind.virtual_address + stub_off
        print(f'[DSS] SteamStub EP located at RVA 0x{steamstub_ep_rva:X} (bind offset 0x{stub_off:X})')
        if not pe.is_64:
            patterns = V3_HEADER_SIZE_PATTERNS_X86
        else:
            patterns = V3_HEADER_SIZE_PATTERNS_X64
        hs = _detect_v3_header_size(bind_bytes, patterns)
        if hs:
            hdr_off = stub_off - hs
            if hdr_off >= 0 and hdr_off + hs <= len(bind_bytes):
                raw = bytearray(bind_bytes[hdr_off:hdr_off + hs])
                steam_xor(raw, len(raw), 0)
                sig = struct.unpack_from('<I', raw, 4)[0]
                if not pe.is_64:
                    ib = struct.unpack_from('<Q', raw, 8)[0]
                    oep = struct.unpack_from('<I', raw, 28)[0]
                else:
                    ib = pe.image_base
                    oep = 0
                if sig == STEAMSTUB_V3_0_SIGNATURE:
                    print(f'[DSS] Sanity-check: header @ bind+0x{hdr_off:X} size=0x{hs:X} decodes cleanly (Sig=OK, ImageBase=0x{ib:X}, OEP=0x{oep:X})')
                else:
                    print(f'[DSS] Warning: header decode at bind+0x{hdr_off:X} failed (sig=0x{sig:08X}) - will try anyway')
        print(f'[DSS] Rewriting AddressOfEntryPoint: 0x{pe.address_of_entry_point:X} -> 0x{steamstub_ep_rva:X}')
        pe.address_of_entry_point = steamstub_ep_rva
        rebuilt_import = cls._rebuild_import_directory(pe)
        dsstext = info.get('dsstext_section')
        if dsstext is not None:
            strip = getattr(opts, 'strip_dsstext', False) or rebuilt_import
            if strip:
                print(f'[DSS] Removing .dsstext (RVA 0x{dsstext.virtual_address:X} size 0x{dsstext.size_of_raw_data:X})')
                pe.remove_section(dsstext)
            else:
                print(f'[DSS] Retaining .dsstext (import machinery lives there). Use --strip-dsstext at your own risk.')
        return True
    @classmethod
    def _rebuild_import_directory(cls, pe: 'PEFile') -> bool:
        rdata = pe.get_section_by_name('.rdata')
        if rdata is None:
            print('[DSS] no .rdata - cannot rebuild import table')
            return False
        iat_rva, iat_size = pe.data_directory(12)
        if iat_rva == 0 or iat_size == 0:
            print('[DSS] no IAT data directory - cannot rebuild import table')
            return False
        iat_lo, iat_hi = (iat_rva, iat_rva + iat_size)
        def _read_rdata(rva: int, n: int) -> bytes:
            off = rva - rdata.virtual_address
            if off < 0 or off + n > len(rdata.raw):
                return b''
            return bytes(rdata.raw[off:off + n])
        def _read_cstr(rva: int, maxlen: int=64) -> Optional[str]:
            b = _read_rdata(rva, maxlen)
            if not b:
                return None
            nul = b.find(b'\x00')
            if nul < 0:
                return None
            try:
                return b[:nul].decode('ascii')
            except UnicodeDecodeError:
                return None
        rd = bytes(rdata.raw)
        rd_base = rdata.virtual_address
        DESC_SIZE = 20
        best: tuple = (0, 0, [])
        for off in range(0, len(rd) - DESC_SIZE, 4):
            names: list = []
            p = off
            while p + DESC_SIZE <= len(rd):
                oft, tds, fc, nm, ft = struct.unpack_from('<IIIII', rd, p)
                if oft == 0 and tds == 0 and (fc == 0) and (nm == 0) and (ft == 0):
                    break
                if tds != 0 or fc != 0:
                    names = []
                    break
                if not iat_lo <= ft < iat_hi:
                    names = []
                    break
                if not rd_base <= nm < rd_base + len(rd):
                    names = []
                    break
                name = _read_cstr(nm)
                if not name or not name.lower().endswith('.dll'):
                    names = []
                    break
                if oft and (not rd_base <= oft < rd_base + len(rd)):
                    names = []
                    break
                names.append(name)
                p += DESC_SIZE
            if len(names) >= 3 and len(names) > best[0]:
                best = (len(names), rd_base + off, names)
        if best[0] < 3:
            print('[DSS] Could not locate original IMPORT descriptor table in .rdata; leaving IMPORT directory pointed at .dsstext')
            return False
        count, imp_rva, names = best
        imp_size = (count + 1) * DESC_SIZE
        print(f"[DSS] Rebuilt IMPORT directory: {count} descriptors at RVA 0x{imp_rva:X} size 0x{imp_size:X}  ({', '.join(names)})")
        pe.set_data_directory(1, imp_rva, imp_size)
        return True
VARIANTS = [Variant10x86(), Variant20x86(), Variant21x86(), Variant31x64(), Variant31x86(), Variant30x64(), Variant30x86()]
def unpack(path: str, opts: Options, out_path: Optional[str]=None, in_place: bool=False) -> int:
    """
    Unpack SteamStub DRM from a PE executable.
    Returns:
        0 - Successfully unpacked
        1 - No SteamStub DRM detected (clean executable)
        2 - SteamStub DRM detected, but unpacking failed
    """
    pe = PEFile(path)
    dss_info = DSSProtector.detect(pe)
    dss_present = dss_info is not None
    if dss_present:
        DSSProtector.report(pe, dss_info)
        if DSSProtector.try_unwrap(pe, dss_info, opts):
            print('[+] DSS unwrapped in memory - proceeding to SteamStub detection.')
        else:
            print('[!] Cannot continue: SteamStub layer is inaccessible until DSS is stripped.')
            return 2
    attempted = set()
    found_variant = False
    for variant in VARIANTS:
        if not variant.detect(pe):
            continue
        found_variant = True
        cls = variant.__class__.__name__
        if cls in attempted:
            continue
        attempted.add(cls)
        print(f'[+] Trying {variant.name}...')
        try:
            pe_try = PEFile(path)
            if dss_present:
                dss_info_try = DSSProtector.detect(pe_try)
                if dss_info_try is None or not DSSProtector.try_unwrap(pe_try, dss_info_try, opts):
                    print(f'[!] DSS unwrap failed on reparse - skipping {variant.name}')
                    continue
            ok = variant.process(pe_try, opts)
            if ok:
                if in_place:
                    backup = path + '.original'
                    if not os.path.exists(backup):
                        os.replace(path, backup)
                        print(f'[+] Backed up original -> {backup}')
                    else:
                        print(f'[!] Backup already exists, skipping rename: {backup}')
                        os.remove(path)
                    out = path
                else:
                    out = out_path or os.path.splitext(path)[0] + '.unpacked' + os.path.splitext(path)[1]
                pe_try.write(out, zero_dos_stub=opts.zero_dos_stub, recalc_checksum=opts.recalculate_checksum)
                print(f'[+] Unpacked with {variant.name} -> {out}')
                return 0
        except Exception as e:
            print(f'[!] {variant.name} failed: {e}')
            if opts.use_experimental:
                import traceback
                traceback.print_exc()
            continue
    if found_variant:
        print('[!] SteamStub DRM detected, but unpacking failed.')
        return 2
    else:
        print('[!] No SteamStub DRM detected in executable.')
        return 1

def main():
    ap = argparse.ArgumentParser(description='Steamless Python port — SteamStub DRM unpacker')
    ap.add_argument('file', help='Path to the packed executable')
    ap.add_argument('-o', '--output', help='Output path. Default: in-place (renames original to <file>.original)')
    ap.add_argument('--no-inplace', action='store_true', help='Do not overwrite the original; write to <name>.unpacked.exe instead')
    ap.add_argument('--keep-bind', action='store_true', help='Keep the .bind section')
    ap.add_argument('--zero-dos-stub', action='store_true', help='Zero the DOS stub data')
    ap.add_argument('--no-realign', action='store_true', help='Do not realign sections')
    ap.add_argument('--checksum', action='store_true', help='Recalculate PE checksum (Windows only)')
    ap.add_argument('--dump-payload', action='store_true', help='Dump payload to disk')
    ap.add_argument('--dump-drmp', action='store_true', help='Dump SteamDRMP.dll to disk')
    ap.add_argument('--experimental', action='store_true', help='Use experimental features / verbose errors')
    ap.add_argument('--strip-dsstext', action='store_true', help='(DSS-wrapped PEs only) Remove the .dsstext section. WARNING: the IMPORT directory lives inside .dsstext; the resulting PE will not load until the import table is reconstructed in .rdata (not yet implemented).')
    args = ap.parse_args()
    opts = Options(keep_bind_section=args.keep_bind, zero_dos_stub=args.zero_dos_stub, dont_realign_sections=args.no_realign, recalculate_checksum=args.checksum, dump_payload=args.dump_payload, dump_drmp=args.dump_drmp, use_experimental=args.experimental, strip_dsstext=args.strip_dsstext)
    in_place = args.output is None and (not args.no_inplace)
    try:
        code = unpack(args.file, opts, args.output, in_place=in_place)
        sys.exit(code)
    except Exception as e:
        print(f'[!] Fatal error processing executable: {e}')
        sys.exit(3)
if __name__ == '__main__':
    main()
