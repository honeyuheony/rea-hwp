"""WSL에서 Windows 한글 2022 COM 자동화로 코퍼스 문서를 PDF로 출력해 정답 렌더링을 만든다.

문서마다 PowerShell을 새로 띄우고, 시간 안에 끝나지 않으면 Hwp.exe를 종료한 뒤 다음 문서로 넘어간다.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from corpus import CORPUS_ROOT, load_corpus

#: 한글 COM은 사용자 Temp 아래 파일은 바로 열고, C:\Users\Public 아래 파일은 숨은 보안 창에서 멈췄다(2026-10-17).
WINDOWS_WORK = Path("/mnt/c/Users/kuhys/AppData/Local/Temp/rea-hwp")
WINDOWS_WORK_WIN = r"C:\Users\kuhys\AppData\Local\Temp\rea-hwp"
OUTPUT_ROOT = CORPUS_ROOT / "ref2022"
#: 한 문서 변환 제한 시간(초). 큰 공문서도 30초 안에 끝나는 것을 보고 여유를 둔 값이다(검증 안 됨: 일부 표본 기준).
TIMEOUT_SECONDS = 180

CONVERT_PS1 = r"""
param([string]$src, [string]$dst, [string]$password)
$ErrorActionPreference = 'Stop'
$hwp = New-Object -ComObject HWPFrame.HwpObject
try {
  $null = $hwp.RegisterModule('FilePathCheckDLL', 'FilePathCheckerModule')
  $hwp.XHwpWindows.Item(0).Visible = $false
  $null = $hwp.SetMessageBoxMode(0x00020021)
  $options = 'forceopen:true;versionwarning:false'
  if ($password) { $options = "$options;password:$password" }
  if (-not $hwp.Open($src, 'HWP', $options)) { throw 'Open returned False' }
  $pset = $hwp.HParameterSet.HFileOpenSave
  $null = $hwp.HAction.GetDefault('FileSaveAs_S', $pset.HSet)
  $pset.filename = $dst
  $pset.Format = 'PDF'
  $pset.Attributes = 0
  $saved = $hwp.HAction.Execute('FileSaveAs_S', $pset.HSet)
  if (-not $saved) { $saved = $hwp.SaveAs($dst, 'PDF', '') }
  if (-not $saved) { throw 'SaveAs returned False' }
  Write-Output "pages=$($hwp.PageCount)"
} finally {
  try { $hwp.Clear(1) } catch {}
  try { $hwp.Quit() } catch {}
}
"""


#: DOC_DATA 파라미터 셋의 인쇄 방식 아이템 ID(0 기본, 1 용지에 맞춤, 4 2쪽 모아찍기). KTX·exam_kor로 확인.
PRINT_METHOD_ITEM = 0x4006


def reset_print_method(source: Path, target: Path) -> bool:
    """저장된 인쇄 방식이 기본이 아니면 0으로 바꾼 사본을 만든다(한글 PDF 저장이 이 값으로 쪽을 모으거나 줄인다)."""
    import struct
    import zlib

    import olefile

    from rea_hwp import tags
    from rea_hwp.container import HwpContainer, HwpFormatError
    from rea_hwp.record import parse_records

    try:
        container = HwpContainer.open(source)
    except HwpFormatError:  # 우리가 못 여는 문서(옛 암호 방식 등)는 고치지 않고 한글에 그대로 넘긴다.
        return False
    if container.header.password_protected:
        return False
    stream = parse_records(container.record_stream("DocInfo"))
    patched = False
    for record in stream.records:
        if record.tag != tags.DOC_DATA:
            continue
        payload = bytearray(record.payload)
        # 실물: 셋 ID·개수·빈 2바이트, 하위 셋(0x0207) 헤더 6바이트, 그 뒤 [ID u16, 종류 u16, 값 u32] 아이템.
        for offset in range(16, len(payload) - 7, 8):
            item_id = struct.unpack_from("<H", payload, offset)[0]
            if item_id == PRINT_METHOD_ITEM and struct.unpack_from("<i", payload, offset + 4)[0] != 0:
                struct.pack_into("<i", payload, offset + 4, 0)
                patched = True
        record.payload = bytes(payload)
    if not patched:
        return False
    raw_size = len(container.raw_streams["DocInfo"])
    plain = stream.to_bytes()
    if container.header.compressed:
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = compressor.compress(plain) + compressor.flush()
    else:
        packed = plain
    if len(packed) > raw_size:
        return False
    shutil.copyfile(source, target)
    with olefile.OleFileIO(str(target), write_mode=True) as ole:
        ole.write_stream("DocInfo", packed + bytes(raw_size - len(packed)))
    return True


def hancom_pids() -> set[int]:
    """COM 자동화로 뜬 한글(-Automation)만 센다. 사용자가 직접 연 한글 창은 대상이 아니다."""
    query = (
        "Get-CimInstance Win32_Process -Filter \"Name='Hwp.exe'\" | "
        "Where-Object { $_.CommandLine -like '*-Automation*' } | ForEach-Object { $_.ProcessId }"
    )
    listing = subprocess.run(["powershell.exe", "-NoProfile", "-Command", query], capture_output=True).stdout
    return {int(line) for line in listing.decode("ascii", "replace").split() if line.isdigit()}


def kill_hancom(keep: set[int]) -> None:
    """변환 전부터 떠 있던 자동화 인스턴스는 남기고 이번에 새로 뜬 것만 종료한다."""
    for pid in hancom_pids() - keep:
        subprocess.run(["taskkill.exe", "/F", "/PID", str(pid)], capture_output=True)


_SEQUENCE = iter(range(1, 1 << 30))


def convert(source: Path, target: Path, password: str | None) -> dict:
    # 프로세스마다 작업 폴더를 따로 써서 동시에 돌려도 입력·출력 파일이 섞이지 않게 한다.
    work = WINDOWS_WORK / str(os.getpid())
    work_win = WINDOWS_WORK_WIN + "\\" + str(os.getpid())
    work.mkdir(parents=True, exist_ok=True)
    script = work / "convert.ps1"
    script.write_text(CONVERT_PS1, encoding="utf-8-sig")
    # 시간 초과로 종료한 한글이 파일을 잠근 채 남을 수 있어 문서마다 다른 이름을 쓴다.
    serial = next(_SEQUENCE)
    work_src = work / f"input{serial}.hwp"
    work_dst = work / f"output{serial}.pdf"
    if not reset_print_method(source, work_src):
        shutil.copyfile(source, work_src)
    work_dst.unlink(missing_ok=True)
    existing = hancom_pids()
    started = time.monotonic()
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        work_win + r"\convert.ps1",
        work_win + "\\" + work_src.name,
        work_win + "\\" + work_dst.name,
        password or "",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=TIMEOUT_SECONDS)
        output = completed.stdout.decode("utf-8", "replace").replace("\r", "").strip()
        error = completed.stderr.decode("utf-8", "replace").replace("\r", "").strip()
    except subprocess.TimeoutExpired:
        kill_hancom(existing)
        return {"status": "timeout", "seconds": TIMEOUT_SECONDS}
    if not work_dst.exists() or work_dst.stat().st_size == 0:
        kill_hancom(existing)
        return {"status": "failed", "stdout": output[-500:], "stderr": error[-500:]}
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(work_dst, target)
    pages = next((line.split("=", 1)[1] for line in output.splitlines() if line.startswith("pages=")), None)
    return {"status": "ok", "pages": int(pages) if pages else None, "seconds": round(time.monotonic() - started, 1)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--redo", action="store_true", help="이미 만든 PDF도 다시 만든다")
    args = parser.parse_args()
    log_path = OUTPUT_ROOT / "convert-log.jsonl"
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    entries = load_corpus()
    if args.limit:
        entries = entries[: args.limit]
    with open(log_path, "a", encoding="utf-8") as log:
        for index, entry in enumerate(entries, 1):
            relative = entry.path.relative_to(CORPUS_ROOT)
            target = OUTPUT_ROOT / relative.with_suffix(".pdf")
            if target.exists() and not args.redo:
                continue
            try:
                outcome = convert(entry.path, target, entry.password)
            except OSError as error:  # 한 문서의 파일 오류로 전체 변환을 멈추지 않는다.
                outcome = {"status": "failed", "stderr": f"{type(error).__name__}: {error}"}
            outcome["path"] = str(relative)
            log.write(json.dumps(outcome, ensure_ascii=False) + "\n")
            log.flush()
            print(f"[{index}/{len(entries)}] {outcome['status']} {relative}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
