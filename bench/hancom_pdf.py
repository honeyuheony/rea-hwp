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
from plain_copy import plain_streams, write_cfb

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
  # 0x1000: 예/아니요/취소 창은 「예」. 숨은 설명 문서가 보안 설정 [높음] 창(열기/보안 설정/취소)에서 멈췄다.
  $null = $hwp.SetMessageBoxMode(0x00021021)
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
  if (-not $saved) {
    # 배포용 문서는 다른 형식 저장이 막혀 PDF 프린터로 인쇄한다(글자 좌표는 PDF 저장과 0.05pt 안, 2026-10-17 실측).
    $print = $hwp.HParameterSet.HPrint
    $null = $hwp.HAction.GetDefault('Print', $print.HSet)
    $print.PrinterName = 'Microsoft Print to PDF'
    $print.PrintToFile = 1
    $print.FileName = $dst
    $print.Range = 0
    $print.NumCopy = 1
    $print.PrintMethod = 0
    if (-not $hwp.HAction.Execute('Print', $print.HSet)) { throw 'SaveAs and Print returned False' }
    # 인쇄는 스풀러가 파일을 나중에 다 쓰므로 크기가 멈출 때까지 기다린다.
    $deadline = (Get-Date).AddSeconds(120); $last = -1
    while ((Get-Date) -lt $deadline) {
      if (Test-Path $dst) {
        $size = (Get-Item $dst).Length
        # 스풀러가 1MiB 단위로 쓰다 멈출 때가 있어 크기와 함께 PDF 끝 표지도 본다(hwpspec 1048576바이트에서 잘림).
        # 스풀러가 쓰는 동안은 파일이 잠겨 읽기가 실패한다. 그때는 아직 쓰는 중으로 본다.
        try { $tail = [IO.File]::ReadAllText($dst).TrimEnd() } catch { $tail = '' }
        if ($size -gt 0 -and $size -eq $last -and $tail.EndsWith('%%EOF')) { $saved = $true; break }
        $last = $size
      }
      Start-Sleep -Seconds 2
    }
    if (-not $saved) { throw 'Print did not finish' }
    Write-Output 'method=print'
  }
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
    from rea_hwp.crypto import HwpDecryptError
    from rea_hwp.record import parse_records

    try:
        container = HwpContainer.open(source)
    except (HwpFormatError, HwpDecryptError):  # 우리가 못 여는 문서(옛 암호 방식, 암호 문서)는 한글에 그대로 넘긴다.
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


def needs_plain_copy(source: Path, password: str | None) -> bool:
    """배포용이거나 우리가 풀 수 있는 암호 문서인가."""
    from rea_hwp.container import HwpContainer, HwpFormatError
    from rea_hwp.crypto import HwpDecryptError

    try:
        header = HwpContainer.open(source, password).header
    except (HwpFormatError, HwpDecryptError):  # 옛 암호 방식(EncryptVersion 0 등)은 사본을 만들 수 없다.
        return False
    return header.distributed or header.password_protected


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


def convert(source: Path, target: Path, password: str | None, timeout: int = TIMEOUT_SECONDS) -> dict:
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
    if needs_plain_copy(source, password):
        # 인쇄까지 막힌 배포용 문서와 암호 입력 창에서 멈추는 암호 문서는 복호화한 일반 사본을 한글에 넘긴다.
        plain = work / f"plain{serial}.hwp"
        write_cfb(plain_streams(source, password), plain)
        source = plain
        password = None
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
        completed = subprocess.run(command, capture_output=True, timeout=timeout)
        output = completed.stdout.decode("utf-8", "replace").replace("\r", "").strip()
        error = completed.stderr.decode("utf-8", "replace").replace("\r", "").strip()
    except subprocess.TimeoutExpired:
        kill_hancom(existing)
        return {"status": "timeout", "seconds": timeout}
    if not work_dst.exists() or work_dst.stat().st_size == 0:
        kill_hancom(existing)
        return {"status": "failed", "stdout": output[-500:], "stderr": error[-500:]}
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(work_dst, target)
    pages = next((line.split("=", 1)[1] for line in output.splitlines() if line.startswith("pages=")), None)
    method = "print" if "method=print" in output else "save"
    seconds = round(time.monotonic() - started, 1)
    return {"status": "ok", "method": method, "pages": int(pages) if pages else None, "seconds": seconds}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--redo", action="store_true", help="이미 만든 PDF도 다시 만든다")
    parser.add_argument("--match", default="", help="경로에 이 글자가 든 문서만 변환한다")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS, help="한 문서 변환 제한 시간(초)")
    args = parser.parse_args()
    log_path = OUTPUT_ROOT / "convert-log.jsonl"
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    entries = load_corpus()
    entries = [entry for entry in entries if args.match in str(entry.path)]
    if args.limit:
        entries = entries[: args.limit]
    with open(log_path, "a", encoding="utf-8") as log:
        for index, entry in enumerate(entries, 1):
            relative = entry.path.relative_to(CORPUS_ROOT)
            target = OUTPUT_ROOT / relative.with_suffix(".pdf")
            if target.exists() and not args.redo:
                continue
            try:
                outcome = convert(entry.path, target, entry.password, args.timeout)
            except OSError as error:  # 한 문서의 파일 오류로 전체 변환을 멈추지 않는다.
                outcome = {"status": "failed", "stderr": f"{type(error).__name__}: {error}"}
            outcome["path"] = str(relative)
            log.write(json.dumps(outcome, ensure_ascii=False) + "\n")
            log.flush()
            print(f"[{index}/{len(entries)}] {outcome['status']} {relative}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
