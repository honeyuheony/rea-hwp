# rea-hwp

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

HWP 5.0 문서를 쪽 단위 SVG로 렌더링하는 파이썬 라이브러리·CLI. 쪽 나눔과 글자 위치는 한컴오피스 한글 2022 인쇄 결과 기준.

## 특징

- 쪽당 SVG 한 장, 글자마다 위치 고정
- 쪽 SVG를 이어 보는 단독 HTML 뷰어 (글 선택·복사·브라우저 찾기 지원)
- 암호 문서·배포용 문서 열기
- 그림은 SVG 안 `data:` URI로 넣거나, 따로 저장한 뒤 주소로 연결(`image_href`)
- 업로드 파일 대비: 크기·레코드 수 상한(`Limits`), 원인별 오류 자료형
- 런타임 의존성 두 개: `olefile`, `cryptography`

## 설치

- Python 3.12 이상

```sh
pip install "git+https://github.com/honeyuheony/rea-hwp.git@main"
# uv
uv add "rea-hwp @ git+https://github.com/honeyuheony/rea-hwp.git@main"
```

- 글맵시까지 렌더링하려면 `fonts` 묶음(`fonttools`) 추가

```sh
pip install "rea-hwp[fonts] @ git+https://github.com/honeyuheony/rea-hwp.git@main"
```

## 빠른 시작

```sh
rea-hwp render example.hwp -o out
# 6 pages -> out/index.html
```

- `out/index.html`을 브라우저로 바로 열면 됨 (웹 서버 불필요)
- 암호 문서: `--password 123456` 추가
- `rea-hwp check example.hwp`: 레코드를 다시 인코딩해 원본 스트림과 비교, 다르면 종료 코드 1

## 라이브러리로 쓰기

```python
from rea_hwp import HwpDocument, page_to_svg, render_pages

document = HwpDocument.open("example.hwp")      # 경로나 bytes, 암호는 두 번째 인자
for page in render_pages(document):
    svg = page_to_svg(page)                      # str
    print(page.number, len(svg))
```

- `Page.number`는 1부터 시작
- 전체 예시는 [`examples/`](examples) 참고

| 파일 | 내용 |
|---|---|
| [`examples/convert.py`](examples/convert.py) | 파일 하나 → 쪽 SVG 묶음(`page-NNNN.svg`) |
| [`examples/upload.py`](examples/upload.py) | 업로드 bytes → 쪽 SVG 문자열 목록. 암호·오류별 분기, 그림은 디스크에 따로 저장 |
| [`examples/batch.py`](examples/batch.py) | 폴더 안 `*.hwp` 일괄 변환. 열 수 없는 문서는 건너뜀 |

### 파일 하나 변환

```python
from pathlib import Path

from rea_hwp import HwpDocument, page_to_svg, render_pages


def convert(path, out_dir, password=None) -> int:
    pages = render_pages(HwpDocument.open(path, password))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for page in pages:
        (out / f"page-{page.number:04d}.svg").write_text(page_to_svg(page), encoding="utf-8")
    return len(pages)
```

- 결과 SVG는 `rea-hwp render`의 `page-NNNN.svg`와 동일
- 뷰어(`index.html`)와 수식 대체 쪽(`.eq.svg`)이 필요하면 `rea-hwp render` 사용

### 업로드 파일 처리

```python
import hashlib
from pathlib import Path

from rea_hwp import HwpDocument, PasswordRequiredError, ReaHwpError, WrongPasswordError, page_to_svg, render_pages

IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
}


class UploadRejected(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        #: "password_required" | "wrong_password" | "unreadable"
        self.reason = reason


def render_upload(upload: bytes, image_dir: Path, image_url: str = "/images", password: str | None = None) -> list[str]:
    try:
        document = HwpDocument.open(upload, password)
        pages = render_pages(document)
    except PasswordRequiredError as error:
        raise UploadRejected("password_required", "이 문서는 열기 암호가 필요합니다.") from error
    except WrongPasswordError as error:
        raise UploadRejected("wrong_password", "암호가 맞지 않습니다.") from error
    except ReaHwpError as error:
        raise UploadRejected("unreadable", f"HWP 5.0 문서로 열 수 없습니다: {error}") from error

    image_dir.mkdir(parents=True, exist_ok=True)

    def store_image(image: bytes, mime: str) -> str:
        # 같은 그림은 같은 bytes로 넘어오므로 내용 해시를 파일 이름으로 쓰면 한 번만 저장된다.
        name = hashlib.sha256(image).hexdigest()[:16] + IMAGE_EXTENSIONS.get(mime, ".bin")
        target = image_dir / name
        if not target.exists():
            target.write_bytes(image)
        return f"{image_url}/{name}"

    return [page_to_svg(page, image_href=store_image) for page in pages]
```

- `image_href`를 생략하면 그림은 `data:` URI로 SVG에 포함
- 같은 그림은 항상 같은 bytes로 전달
- `mime` 값: `image/png`, `image/jpeg`, `image/gif`, `image/bmp`, `image/svg+xml` 등

오류는 모두 `ReaHwpError` 하위 자료형.

| 자료형 | 상황 |
|---|---|
| `PasswordRequiredError` | 암호 없이 암호 문서를 엶 |
| `WrongPasswordError` | 암호 틀림 |
| `UnsupportedFormatError` | HWP 3.0, HWPX, OLE 형식이 아닌 파일 |
| `DrmProtectedError` | DRM 문서 |
| `CorruptFileError` | 잘리거나 깨진 파일 |
| `LimitExceededError` | `Limits` 상한 초과. `HwpDocument.open(..., limits=Limits(max_records=...))`로 조정 |

### 렌더 설정

```python
from pathlib import Path

from rea_hwp import RenderOptions

options = RenderOptions(font_dirs=(Path("/usr/share/fonts/truetype"),))
pages = render_pages(document, options=options)
```

- `font_dirs`: 글맵시 글꼴(TTF·TTC) 검색 폴더
- 기본값: Windows 글꼴 폴더, 한컴 공용 글꼴 폴더
- 렌더링하지 못한 글맵시는 `page.missing`에 기록
- 라이브러리는 환경 변수를 읽지 않음 (설정은 `RenderOptions`로만)

## 출력 형식

`rea-hwp render -o out`의 출력 파일:

| 파일 | 내용 |
|---|---|
| `page-NNNN.svg` | 쪽 하나. 번호는 1부터 네 자리, `viewBox`는 쪽 크기(pt) |
| `page-NNNN.eq.svg` | 수식이 있는 쪽에만 생성. 수식 글꼴 글자를 유니코드로 바꾸고 OFL 수학 글꼴로 렌더링한 쪽 |
| `index.html` | 쪽 SVG를 `<img>`로 이어 보는 뷰어. 수식 글꼴이 없는 브라우저에서는 `.eq.svg`를 대신 표시 |

### SVG

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="595.28pt" height="841.88pt" viewBox="0 0 595.28 841.88">
<rect width="595.28" height="841.88" fill="#ffffff"/>
<text x="212.64" y="94.935" font-size="20" font-family="HY헤드라인M" >통</text>
```

- `<text>` 하나에 글자 하나
- 좌표 단위 pt, 원점은 쪽 왼쪽 위
- `x`, `y`는 글자의 기준선 왼쪽 끝

### HTML 뷰어

- 같은 폴더의 `page-NNNN.svg`를 `<img>`로 불러오므로 파일로 바로 열림
- 쪽마다 투명한 글 층을 겹쳐 글 선택·복사, 브라우저 찾기(Ctrl+F) 가능
- 출력 폴더를 그대로 정적 파일로 배포 가능

## 제한 사항

- HWP 5.0만 지원. HWPX·HWP 3.0은 `UnsupportedFormatError`, DRM 문서는 `DrmProtectedError`
- SVG에 글꼴 파일은 넣지 않고 이름만 지정. 보는 PC에 그 글꼴이 없으면 브라우저가 다른 글꼴로 대체 (글자 위치는 그대로, 모양만 달라짐)
- 글맵시는 `fonttools`와 글꼴 파일이 모두 있어야 렌더링
- 문서 편집·저장 기능 없음

## 기여

```sh
uv sync
uv run pytest
uv run ruff check
```

- 말뭉치 문서를 쓰는 시험은 환경 변수 `REA_HWP_CORPUS`가 없으면 건너뜀
- `bench/`: 한글이 만든 PDF와 렌더링 결과를 비교하는 평가 도구 (`bench` 묶음의 `pymupdf` 필요)
- 렌더링 결과나 공개 API가 바뀌면 `rea_hwp.__version__`을 올리고 [CHANGELOG.md](CHANGELOG.md)에 기록

## 라이선스

[MIT](LICENSE). [NOTICE](NOTICE)도 함께 참고.
