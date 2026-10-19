"""OLE 개체 BinData에서 한글이 그리는 미리 그린 그림(표현 스트림의 메타파일)을 꺼낸다.

표현 스트림 배치는 MS-OLEDS 2.3.4 OLEPresentationStream을 따른다.
"""

from __future__ import annotations

import io
import struct

import olefile

#: BinData 앞 4바이트는 뒤따르는 복합 파일 길이다.
_LENGTH_PREFIX = 4
_CF_METAFILEPICT = 3
_DVASPECT_CONTENT = 1
#: 클립보드 형식(8) + TargetDeviceSize(4, 장치 정보 없음) + Aspect·Lindex·Advf·Reserved1(16) + 폭·높이·크기(12).
_HEADER_SIZE = 40


def content_metafile(data: bytes) -> bytes | None:
    """내용 aspect 메타파일 표현의 WMF 바이트. 한글도 저장할 때 OlePres000을 이 머리로 쓴다(HwpApp 0x10220250)."""
    body = io.BytesIO(data[_LENGTH_PREFIX:])
    if not olefile.isOleFile(body):
        return None
    with olefile.OleFileIO(body) as storage:
        for entry in sorted(storage.listdir()):
            if len(entry) != 1 or not entry[0].startswith("\x02OlePres"):
                continue
            stream = storage.openstream(entry).read()
            if len(stream) < _HEADER_SIZE:
                continue
            marker, clip_format, device_size, aspect = struct.unpack_from("<iIII", stream)
            if marker != -1 or clip_format != _CF_METAFILEPICT or device_size != 4 or aspect != _DVASPECT_CONTENT:
                continue
            (size,) = struct.unpack_from("<I", stream, 36)
            return stream[_HEADER_SIZE : _HEADER_SIZE + size]
    return None


def has_ooxml_chart(data: bytes) -> bool:
    """새 차트(OOXMLChartContents)를 담은 OLE인가. 한글은 이 차트를 표현 스트림이 아니라 차트 엔진으로 다시 그린다."""
    body = io.BytesIO(data[_LENGTH_PREFIX:])
    if not olefile.isOleFile(body):
        return False
    with olefile.OleFileIO(body) as storage:
        return storage.exists("OOXMLChartContents")
