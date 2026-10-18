"""수식이 든 코퍼스 문서를 한글 2022 COM으로 열어 HWP로 다시 저장한다(한글이 다시 계산한 수식 크기를 얻는다).

결과는 resave2022/에 두고 evaluate.py가 equation_oracle로 수식 엔진 오차를 따로 센다.
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
from hancom_pdf import TIMEOUT_SECONDS, WINDOWS_WORK, WINDOWS_WORK_WIN, hancom_pids, kill_hancom, needs_plain_copy
from plain_copy import plain_streams, write_cfb

from rea_hwp import tags
from rea_hwp.document import HwpDocument

RESAVE_ROOT = CORPUS_ROOT / "resave2022"

RESAVE_PS1 = r"""
param([string]$src, [string]$dst, [string]$password)
$ErrorActionPreference = 'Stop'
$hwp = New-Object -ComObject HWPFrame.HwpObject
try {
  $null = $hwp.RegisterModule('FilePathCheckDLL', 'FilePathCheckerModule')
  $hwp.XHwpWindows.Item(0).Visible = $false
  $null = $hwp.SetMessageBoxMode(0x00021021)
  $options = 'forceopen:true;versionwarning:false'
  if ($password) { $options = "$options;password:$password" }
  if (-not $hwp.Open($src, 'HWP', $options)) { throw 'Open returned False' }
  # PageCount를 읽어야 배치를 끝까지 마친다. 먼저 저장하면 LINE_SEG가 원본 그대로 남았다(3-09월_교육_통합_2022).
  Write-Output "pages=$($hwp.PageCount)"
  if (-not $hwp.SaveAs($dst, 'HWP', '')) { throw 'SaveAs returned False' }
} finally {
  try { $hwp.Clear(1) } catch {}
  try { $hwp.Quit() } catch {}
}
"""


def has_equation(entry) -> bool:
    document = HwpDocument.open(entry.path, entry.password)
    return any(
        node.tag == tags.CTRL_HEADER and int.from_bytes(node.record.payload[:4], "little") == tags.EQUATION_CTRL
        for section in document.sections
        for node in section.walk()
    )


def resave(source: Path, target: Path, password: str | None, timeout: int) -> dict:
    work = WINDOWS_WORK / f"resave{os.getpid()}"
    work_win = WINDOWS_WORK_WIN + f"\\resave{os.getpid()}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "resave.ps1").write_text(RESAVE_PS1, encoding="utf-8-sig")
    stamp = time.monotonic_ns()
    work_src, work_dst = work / f"input{stamp}.hwp", work / f"output{stamp}.hwp"
    if needs_plain_copy(source, password):
        # 배포용은 다른 이름 저장이 막히고 암호 문서는 입력 창에서 멈춘다. 복호화한 일반 사본을 넘긴다.
        write_cfb(plain_streams(source, password), work_src)
        password = None
    else:
        shutil.copyfile(source, work_src)
    existing = hancom_pids()
    started = time.monotonic()
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", work_win + r"\resave.ps1"]
    command += [work_win + "\\" + work_src.name, work_win + "\\" + work_dst.name, password or ""]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_hancom(existing)
        return {"status": "timeout", "seconds": timeout}
    if not work_dst.exists() or work_dst.stat().st_size == 0:
        kill_hancom(existing)
        error = completed.stderr.decode("utf-8", "replace").replace("\r", "").strip()
        return {"status": "failed", "stderr": error[-500:]}
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(work_dst, target)
    return {"status": "ok", "seconds": round(time.monotonic() - started, 1)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--redo", action="store_true", help="이미 만든 재저장본도 다시 만든다")
    parser.add_argument("--match", default="", help="경로에 이 글자가 든 문서만 다시 저장한다")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS, help="한 문서 제한 시간(초)")
    args = parser.parse_args()
    RESAVE_ROOT.mkdir(parents=True, exist_ok=True)
    entries = [entry for entry in load_corpus() if args.match in str(entry.path)]
    with open(RESAVE_ROOT / "resave-log.jsonl", "a", encoding="utf-8") as log:
        for index, entry in enumerate(entries, 1):
            relative = entry.path.relative_to(CORPUS_ROOT)
            target = RESAVE_ROOT / relative.with_suffix(".hwp")
            if target.exists() and not args.redo or not has_equation(entry):
                continue
            outcome = resave(entry.path, target, entry.password, args.timeout)
            outcome["path"] = str(relative)
            log.write(json.dumps(outcome, ensure_ascii=False) + "\n")
            log.flush()
            print(f"[{index}/{len(entries)}] {outcome['status']} {relative}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
