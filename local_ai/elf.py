"""Read ELF64 little-endian dependency/version metadata without executing it."""
import struct
from pathlib import Path


def inspect_elf(path):
    data = Path(path).read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Expected ELF64 little-endian file")
    machine = struct.unpack_from("<H", data, 18)[0]
    offset = struct.unpack_from("<Q", data, 40)[0]
    size, count = struct.unpack_from("<HH", data, 58)
    if size != 64 or not 0 < count < 4096 or offset + count * size > len(data):
        raise ValueError("Invalid ELF section table")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, offset + i * size) for i in range(count)]
    needed, versions = [], []
    for section in sections:
        kind, start, length, link = section[1], section[4], section[5], section[6]
        if kind not in (6, 0x6ffffffe):
            continue
        if link >= count or start + length > len(data):
            raise ValueError("Invalid ELF metadata")
        strings = sections[link]
        table = data[strings[4]:strings[4] + strings[5]]
        def string(index):
            end = table.index(b"\0", index)
            return table[index:end].decode("ascii")
        if kind == 6:
            for pos in range(start, start + length, 16):
                tag, value = struct.unpack_from("<qQ", data, pos)
                if tag == 1:
                    needed.append(string(value))
        else:
            pos = start
            for _ in range(section[7]):
                _, entries, _, aux, advance = struct.unpack_from("<HHIII", data, pos)
                cursor = pos + aux
                for _ in range(entries):
                    _, _, _, name, next_aux = struct.unpack_from("<IHHII", data, cursor)
                    versions.append(string(name))
                    cursor += next_aux
                if not advance:
                    break
                pos += advance
    glibc = [tuple(map(int, name[6:].split("."))) for name in versions if name.startswith("GLIBC_") and name[6:7].isdigit()]
    return {"machine": machine, "needed": needed, "required_versions": sorted(set(versions)),
            "highest_glibc": ".".join(map(str, max(glibc))) if glibc else None}
