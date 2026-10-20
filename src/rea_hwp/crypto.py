"""배포용 문서(ViewText)와 열기 암호 문서(EncryptVersion 4는 AES, 0~2는 DES)의 스트림 복호화."""

from __future__ import annotations

import hashlib
import struct

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, CipherAlgorithm, algorithms, modes

from .record import parse_records
from .tags import DISTRIBUTE_DOC_DATA

DISTRIBUTE_HEADER_SIZE = 4 + 256
#: 열기 암호 키 유도에서 첫 바이트 앞에 두는 값(출처: rhwp src/parser/crypto.rs, MIT).
PASSWORD_KEY_SEED = 236
SUPPORTED_ENCRYPT_VERSION = 4
#: 한글 2022가 같은 DES 경로로 푸는 옛 암호 버전. 0은 password-12345(5.0.1.7) 문서로 확인, 1·2는 같은 분기라 표본 없음.
LEGACY_ENCRYPT_VERSIONS = (0, 1, 2)
LEGACY_KEY_BYTES = 7
_CFB1_CHUNK_BYTES = 1 << 16
#: 비트 위치 j마다 ECB 출력 첫 바이트의 맨 위 비트를 평문 바이트의 (7 - j)번 비트 자리로 옮기는 표.
_KEYSTREAM_BIT_TABLES = [bytes((value >> 7) << (7 - bit) for value in range(256)) for bit in range(8)]


class HwpDecryptError(ValueError):
    """암호 해제에 실패했다(암호가 틀렸거나 스트림이 손상됐다)."""


class _MsvcRand:
    """MSVC CRT의 srand/rand와 같은 수열(배포용 문서 명세 rev1.2 3절)."""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFF

    def next(self) -> int:
        self.state = (self.state * 214013 + 2531011) & 0xFFFFFFFF
        return (self.state >> 16) & 0x7FFF


def distribute_key(doc_data: bytes) -> tuple[bytes, int]:
    """256바이트 DISTRIBUTE_DOC_DATA에서 AES 키와 옵션 플래그를 꺼낸다."""
    if len(doc_data) < 256:
        raise HwpDecryptError(f"DISTRIBUTE_DOC_DATA is {len(doc_data)} bytes, expected 256")
    (seed,) = struct.unpack_from("<I", doc_data, 0)
    rand = _MsvcRand(seed)
    plain = bytearray(doc_data[:256])
    position = 0
    while position < 256:
        key_byte = rand.next() & 0xFF
        run = (rand.next() & 0x0F) + 1
        for index in range(position, min(position + run, 256)):
            # 앞 4바이트(seed)는 XOR하지 않지만 난수는 그대로 소비한다.
            if index >= 4:
                plain[index] ^= key_byte
        position += run
    hash_offset = 4 + (seed & 0x0F)
    key = bytes(plain[hash_offset : hash_offset + 16])
    (options,) = struct.unpack_from("<H", plain, hash_offset + 80)
    return key, options


def decrypt_distribute_stream(stream: bytes) -> bytes:
    """ViewText/Scripts 스트림에서 DISTRIBUTE_DOC_DATA를 떼고 나머지를 AES-128-ECB로 푼다."""
    head = parse_records(stream[:DISTRIBUTE_HEADER_SIZE]).records
    if not head or head[0].tag != DISTRIBUTE_DOC_DATA or len(head[0].payload) != 256:
        raise HwpDecryptError("stream does not start with a 256-byte DISTRIBUTE_DOC_DATA record")
    key, _ = distribute_key(head[0].payload)
    body = stream[DISTRIBUTE_HEADER_SIZE:]
    aligned = len(body) - len(body) % 16
    return _ecb(algorithms.AES(key)).decryptor().update(body[:aligned]) + body[aligned:]


def password_key(password: str) -> bytes:
    secret = password.encode("utf-8")
    interleaved = bytearray()
    previous = PASSWORD_KEY_SEED
    for byte in secret:
        interleaved.append(((previous << 1) | (previous >> 7)) & 0xFF)
        interleaved.append(byte)
        previous = byte
    return hashlib.sha1(interleaved).digest()[:16]


def legacy_password_key(password: str) -> bytes:
    """EncryptVersion 0~2의 DES 키. 출처: 한글 2022 HncFoundation.dll CHncEncryption 키 준비(0x1009a660)."""
    folded = bytearray(LEGACY_KEY_BYTES + 1)
    for index, byte in enumerate(password.encode("utf-16-le")):
        mixed = byte ^ folded[index % LEGACY_KEY_BYTES]
        folded[index % LEGACY_KEY_BYTES] = ((mixed << 1) | (mixed >> 7)) & 0xFF
    # 56비트를 앞에서부터 각 바이트의 위 7비트에 나눠 넣는다. 맨 아래 비트(DES 패리티)는 접은 값 그대로 둔다.
    key = bytearray(folded)
    target = 0
    for source in range(LEGACY_KEY_BYTES * 8):
        if target % 8 == 7:
            target += 1
        mask = 0x80 >> (target & 7)
        if folded[source >> 3] & (0x80 >> (source & 7)):
            key[target >> 3] |= mask
        else:
            key[target >> 3] &= ~mask & 0xFF
        target += 1
    return bytes(key)


def decrypt_legacy_password_stream(stream: bytes, password: str) -> bytes:
    """DES-ECB 복호화. 8바이트에 못 미치는 꼬리는 한글처럼 그대로 둔다."""
    aligned = len(stream) - len(stream) % 8
    # 같은 8바이트 키를 세 번 이은 TripleDES는 단일 DES와 같다.
    decryptor = _ecb(TripleDES(legacy_password_key(password) * 3)).decryptor()
    return decryptor.update(stream[:aligned]) + stream[aligned:]


def decrypt_stream(stream: bytes, password: str, encrypt_version: int) -> bytes:
    if encrypt_version in LEGACY_ENCRYPT_VERSIONS:
        return decrypt_legacy_password_stream(stream, password)
    return decrypt_password_stream(stream, password)


def decrypt_password_stream(stream: bytes, password: str) -> bytes:
    """AES-128 CFB-1(IV 0) 복호화. 암호문을 알기 때문에 레지스터를 미리 만들어 ECB로 한 번에 돌린다."""
    encryptor = _ecb(algorithms.AES(password_key(password))).encryptor()
    padded = bytes(16) + stream
    plain = bytearray()
    for start in range(0, len(stream), _CFB1_CHUNK_BYTES):
        end = min(start + _CFB1_CHUNK_BYTES, len(stream))
        window = padded[start : end + 17]
        window_value = int.from_bytes(window, "big")
        keystream = 0
        for bit in range(8):
            # 바이트 k의 j번 비트를 푸는 레지스터는 그 비트 앞 128비트, 곧 (8 - j)비트 민 값의 [k + 1, k + 17) 바이트다.
            shifted = (window_value >> (8 - bit)).to_bytes(len(window), "big")
            registers = b"".join(shifted[index + 1 : index + 17] for index in range(end - start))
            tops = encryptor.update(registers)[::16].translate(_KEYSTREAM_BIT_TABLES[bit])
            keystream |= int.from_bytes(tops, "big")
        cipher_value = int.from_bytes(stream[start:end], "big")
        plain += (cipher_value ^ keystream).to_bytes(end - start, "big")
    return bytes(plain)


def _ecb(algorithm: CipherAlgorithm) -> Cipher:
    return Cipher(algorithm, modes.ECB())
