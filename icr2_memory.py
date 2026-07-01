"""
icr2_memory.py — Minimal, efficient typed memory reader for ICR2 inside DOSBox.

What this module does:
  • Attaches to DOSBox by window-title keywords and computes the ICR2 EXE base via signature scan.
  • Provides one unified, typed read API: read(offset, type, count=1).
  • Provides BulkReader to prefetch a contiguous region once and slice many fields (zero extra syscalls).
  • Provides read_blocks() for N×K table layouts with optional stride/padding.
  • Cleans up process handles and supports `with ICR2Memory(...) as mem:`.

New: optional `verbose=True` prints confirmation when the target process is found and when
the signature match is located (with addresses), plus the computed EXE base.

Design choices:
  • All parsing is little-endian ('<') to match x86 ICR2.
  • Signature scan reads memory in fixed chunks with overlap, no arbitrary 2 GiB cap.
  • Parameter validation is strict to catch mistakes early.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import struct
from typing import List, Optional, Tuple

from icr2_versions import ICR2_VERSION_CONFIGS, normalize_version

import pymem
import win32gui
import win32process


# ----------------------------
# Win32 virtual memory basics
# ----------------------------

# We only care about committed, readable regions.
MEM_COMMIT = 0x1000

# Consider these protection flags "readable".
# PAGE_READONLY(0x02), PAGE_READWRITE(0x04), PAGE_WRITECOPY(0x08),
# PAGE_EXECUTE_READ(0x20), PAGE_EXECUTE_READWRITE(0x40)
PAGE_READABLE = (0x02 | 0x04 | 0x08 | 0x20 | 0x40)


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    """Layout returned by VirtualQueryEx; only a subset is used."""
    _fields_ = [
        ('BaseAddress', ctypes.wintypes.LPVOID),
        ('AllocationBase', ctypes.wintypes.LPVOID),
        ('AllocationProtect', ctypes.wintypes.DWORD),
        ('RegionSize', ctypes.c_size_t),
        ('State', ctypes.wintypes.DWORD),
        ('Protect', ctypes.wintypes.DWORD),
        ('Type', ctypes.wintypes.DWORD),
    ]


# ----------------------------
# Window discovery + signature
# ----------------------------

def find_pid_by_window_title(keywords: List[str]) -> Optional[dict]:
    """
    Return {'pid': int, 'title': str} for the first visible window whose title contains
    ALL given keywords (case-insensitive). Returns None if not found.
    """
    result = {'pid': None, 'title': None}

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if all(k.lower() in title.lower() for k in keywords):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                result['pid'] = pid
                result['title'] = title
                # Early exit by throwing a sentinel exception
                raise StopIteration
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except StopIteration:
        pass

    return result if result['pid'] else None


def _scan_region_chunked(pm: pymem.Pymem, start: int, size: int,
                         needle: bytes, chunk_size: int = 64 * 1024) -> Optional[int]:
    """
    Chunked scan of a single region [start, start+size) for 'needle'.
    Overlaps chunks by len(needle)-1 bytes so matches across boundaries are found.
    Returns absolute address of first match in this region, or None.
    """
    if size <= 0 or not needle:
        return None

    end = start + size
    overlap = max(0, len(needle) - 1)
    leftover = b""
    pos = start

    while pos < end:
        to_read = min(chunk_size, end - pos)
        try:
            chunk = pm.read_bytes(pos, to_read)
        except Exception:
            # Skip unreadable subrange inside a committed region (rare but possible).
            pos += to_read
            leftover = b""
            continue

        data = leftover + chunk
        idx = data.find(needle)
        if idx != -1:
            return (pos - len(leftover)) + idx

        leftover = data[-overlap:] if overlap else b""
        pos += to_read

    return None


def find_pattern_address(pm: pymem.Pymem, pattern_bytes: bytes) -> Optional[int]:
    """
    Walk all memory with VirtualQueryEx; scan only committed, readable regions.
    Uses chunked reads to avoid huge allocations. Returns absolute address of the first match or None.
    """
    if not pattern_bytes:
        return None

    mbi = MEMORY_BASIC_INFORMATION()
    addr = 0
    VirtualQueryEx = ctypes.windll.kernel32.VirtualQueryEx

    while True:
        ok = VirtualQueryEx(pm.process_handle,
                            ctypes.c_void_p(addr),
                            ctypes.byref(mbi),
                            ctypes.sizeof(mbi))
        if not ok:
            break  # reached end of address space

        region_size = int(mbi.RegionSize) or 0
        if (mbi.State == MEM_COMMIT) and (mbi.Protect & PAGE_READABLE) and (region_size > 0):
            hit = _scan_region_chunked(pm, addr, region_size, pattern_bytes)
            if hit is not None:
                return hit

        # Step to next region. If RegionSize is 0 (shouldn't happen), advance by a page.
        addr += region_size if region_size else 0x1000

    return None


# ----------------------------
# Main reader
# ----------------------------

class ICR2Memory:
    """
    Attach to DOSBox, compute the ICR2 EXE base via signature scan, and provide typed reads.

    Usage:
        with ICR2Memory("DOS", verbose=True) as mem:
            rpm = mem.read(0x000BB6F2, 'i16')
            temps = mem.read(0x000EE7A0, 'i16', count=4)

            with ICR2Memory.BulkReader(mem, 0x000EE780, 0x100) as br:
                lf = br.read(0x000EE7A0, 'i16')
                arr = br.read(0x000EE7B0, 'i16', 4)
    """

    # type_name -> (struct format, element size in bytes)
    TYPE_MAP: dict[str, Tuple[str, int]] = {
        'u8':  ('<B', 1),
        'i8':  ('<b', 1),
        'u16': ('<H', 2),
        'i16': ('<h', 2),
        'u32': ('<I', 4),
        'i32': ('<i', 4),
        'f32': ('<f', 4),
        'f64': ('<d', 8),
        # 'bytes' handled specially
    }

    def __init__(self,
                 version: str,
                 signature_bytes: Optional[bytes] = None,
                 signature_offset: Optional[int] = None,
                 window_keywords: Optional[List[str]] = None,
                 verbose: bool = True):
        """
        Construct and attach.

        :param version: one of the known ICR2 version identifiers
        :param signature_bytes: optional override of sentinel bytes (defaults chosen per version)
        :param signature_offset: optional override of static offset to EXE base
        :param window_keywords: optional override of window title keywords
        :param verbose: if True, print confirmations for process attach and signature discovery
        """
        self.pm: Optional[pymem.Pymem] = None
        self.exe_base: Optional[int] = None
        self.pid: Optional[int] = None
        self.window_title: Optional[str] = None
        self.verbose = bool(verbose)

        v = normalize_version(version)
        version_config = ICR2_VERSION_CONFIGS[v]
        if signature_bytes is None:
            signature_bytes = version_config.signature_bytes
        if signature_offset is None:
            signature_offset = version_config.signature_offset
        if window_keywords is None:
            window_keywords = list(version_config.window_keywords)

        info = find_pid_by_window_title(window_keywords)
        if not info:
            raise RuntimeError(f"Target window not found for keywords {window_keywords}")

        if self.verbose:
            print(f"[ICR2Memory] Target window found: '{info['title']}' (PID {info['pid']}). Attaching...")

        self.pm = pymem.Pymem()
        self.pm.open_process_from_id(info['pid'])

        if self.verbose:
            print("[ICR2Memory] Scanning memory for signature...")

        hit = find_pattern_address(self.pm, signature_bytes)
        if not hit:
            raise RuntimeError("Signature not found in process memory")

        self.exe_base = hit - int(signature_offset)
        self.pid = info['pid']
        self.window_title = info['title']

        if self.verbose:
            print(f"[ICR2Memory] Signature found at 0x{hit:08X}. "
                  f"Using offset 0x{int(signature_offset):X} -> EXE base 0x{self.exe_base:08X}")

    # --- lifecycle / context management ---

    def close(self) -> None:
        """Close the process handle (idempotent)."""
        if self.pm:
            try:
                self.pm.close_process()
            finally:
                self.pm = None
            if self.verbose:
                print("[ICR2Memory] Detached from process.")

    def __enter__(self) -> "ICR2Memory":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False  # don't suppress exceptions

    # --- typed read ---

    def read(self, exe_offset: int, type_name: str, count: int = 1):
        """
        Typed read at EXE-relative offset.

        :param exe_offset: EXE-relative offset (int).
        :param type_name: one of {'u8','i8','u16','i16','u32','i32','f32','f64','bytes'}.
        :param count: element count; for 'bytes' this is a byte length.
        :return: scalar for count==1 (except 'bytes' -> bytes), list for count>1.
        """
        if self.exe_base is None or self.pm is None:
            raise RuntimeError("Process not attached")

        addr = self.exe_base + int(exe_offset)

        if type_name == 'bytes':
            if count < 0:
                raise ValueError("bytes count must be >= 0")
            return self.pm.read_bytes(addr, count)

        try:
            fmt, size = self.TYPE_MAP[type_name]
        except KeyError:
            raise ValueError(f"Unsupported type_name '{type_name}'")

        if count < 1:
            raise ValueError("count must be >= 1 for typed reads")

        if count == 1:
            raw = self.pm.read_bytes(addr, size)
            return struct.unpack(fmt, raw)[0]

        # Array path: one bulk read, unpack repeated element code.
        base_code = fmt[1:]  # strip '<'
        raw = self.pm.read_bytes(addr, size * count)
        full_fmt = "<" + (base_code * count)
        return list(struct.unpack(full_fmt, raw))

    # ----------------------------
    # Bulk prefetch (contiguous)
    # ----------------------------

    class BulkReader:
        """
        Prefetch a contiguous region once; subsequent reads are buffer slices.
        No further system calls after construction.

        Example:
            with ICR2Memory.BulkReader(mem, base, span) as br:
                x = br.read(off_x, 'i16')
                arr = br.read(off_arr, 'i32', 12)
        """
        def __init__(self, icr2mem: "ICR2Memory", base_exe_offset: int, length: int):
            if icr2mem.exe_base is None or icr2mem.pm is None:
                raise RuntimeError("Process not attached")
            self._m = icr2mem
            self._base = int(base_exe_offset)
            self._len = int(length)
            # Optional: preflight the span to ensure all pages are readable.
            if not _span_is_readable(icr2mem.pm.process_handle,
                                     icr2mem.exe_base + self._base,
                                     self._len):
                raise RuntimeError("BulkReader span contains unreadable pages")
            # Single OS read:
            self._buf = icr2mem.pm.read_bytes(icr2mem.exe_base + self._base, self._len)

        def __enter__(self) -> "ICR2Memory.BulkReader":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def _slice(self, exe_offset: int, size: int) -> bytes:
            rel = int(exe_offset) - self._base
            if rel < 0 or rel + size > self._len:
                raise ValueError(
                    f"BulkReader slice out of range: off={hex(int(exe_offset))} "
                    f"base={hex(self._base)} size={size} len={self._len}"
                )
            return self._buf[rel:rel + size]

        def read(self, exe_offset: int, type_name: str, count: int = 1):
            """Typed read within the prefetched buffer (same contract as ICR2Memory.read)."""
            if type_name == 'bytes':
                if count < 0:
                    raise ValueError("bytes count must be >= 0")
                return self._slice(exe_offset, count)

            try:
                fmt, size = ICR2Memory.TYPE_MAP[type_name]
            except KeyError:
                raise ValueError(f"Unsupported type_name '{type_name}'")

            if count < 1:
                raise ValueError("count must be >= 1 for typed reads")

            if count == 1:
                raw = self._slice(exe_offset, size)
                return struct.unpack(fmt, raw)[0]

            raw = self._slice(exe_offset, size * count)
            base_code = fmt[1:]
            full_fmt = "<" + (base_code * count)
            return list(struct.unpack(full_fmt, raw))


# ----------------------------
# Utilities
# ----------------------------

def _span_is_readable(pm_handle, base_addr: int, length: int) -> bool:
    """
    Verify that [base_addr, base_addr+length) is entirely composed of committed,
    readable pages. This avoids BulkReader failures due to guard/no-access pages.
    """
    mbi = MEMORY_BASIC_INFORMATION()
    VirtualQueryEx = ctypes.windll.kernel32.VirtualQueryEx
    addr = base_addr
    end = base_addr + length

    while addr < end:
        if not VirtualQueryEx(pm_handle,
                              ctypes.c_void_p(addr),
                              ctypes.byref(mbi),
                              ctypes.sizeof(mbi)):
            return False
        region_size = int(mbi.RegionSize) or 0
        if not (mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_READABLE)):
            return False
        addr += region_size if region_size else 0x1000
    return True


def read_blocks(mem: ICR2Memory,
                base_exe_offset: int,
                n_blocks: int,
                values_per_block: int,
                type_name: str = 'i32',
                stride_bytes: Optional[int] = None) -> List[List[int]]:
    """
    Read N blocks × K values (typed) from a continuous region, with optional padding.

    Layout:
      block_j base = base_exe_offset + j * stride
      block_size   = values_per_block * sizeof(type)
      if stride_bytes is None => stride == block_size (back-to-back)

    Performs exactly one process read (via BulkReader) and slices per block.

    Returns: list of N lists, each of length K.
    """
    if mem.exe_base is None or mem.pm is None:
        raise RuntimeError("Process not attached")

    try:
        _, tsize = ICR2Memory.TYPE_MAP[type_name]
    except KeyError:
        raise ValueError(f"Unsupported type_name '{type_name}'")

    if n_blocks < 1 or values_per_block < 1:
        raise ValueError("n_blocks and values_per_block must be >= 1")

    block_size = values_per_block * tsize
    stride = block_size if stride_bytes is None else int(stride_bytes)
    if stride < block_size:
        raise ValueError("stride_bytes must be >= block_size to avoid overlapping blocks")

    total_span = (n_blocks - 1) * stride + block_size

    out: List[List[int]] = []
    with ICR2Memory.BulkReader(mem, base_exe_offset, total_span) as br:
        for j in range(n_blocks):
            off = base_exe_offset + j * stride
            out.append(br.read(off, type_name, values_per_block))
    return out
