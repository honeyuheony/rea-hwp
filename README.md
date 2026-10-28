# rea-hwp

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

HWP 5.0 문서를 쪽마다 SVG로 그리는 파이썬 라이브러리와 CLI입니다. 쪽 나눔과 글자 위치는 한컴오피스 한글 2022의 인쇄 결과에 맞춥니다.

## 특징

- 쪽마다 SVG 한 장. 글자마다 위치를 고정해 그립니다.
- 쪽 SVG를 이어 보여 주는 단독 HTML 뷰어. 투명한 글 층으로 글을 골라 복사합니다.
- 암호 문서와 배포용 문서를 엽니다.
- 그림은 SVG 안 `data:` URI나, 따로 저장한 주소로 겁니다(`image_href`).
- 믿을 수 없는 업로드 파일용 크기·레코드 수 상한(`Limits`)과 오류 자료형.
- 의존성은 `olefile`, `cryptography` 둘입니다.

## 설치

Python 3.12 이상이 필요합니다.

```sh
pip install "git+https://github.com/honeyuheony/rea-hwp.git@main"
# uv
uv add "rea-hwp @ git+https://github.com/honeyuheony/rea-hwp.git@main"
```

글맵시까지 그리려면 `fonts` 묶음(`fonttools`)을 함께 설치합니다.

```sh
pip install "rea-hwp[fonts] @ git+https://github.com/honeyuheony/rea-hwp.git@main"
```

## 빠른 시작

```sh
rea-hwp render example.hwp -o out
# 6 pages -> out/index.html
```

`out/index.html`을 브라우저에서 파일로 열면 됩니다. 암호 문서는 `--password 123456`을 덧붙입니다.

`rea-hwp check example.hwp`는 레코드를 다시 인코딩해 원본 스트림과 같은지 비교하고, 다르면 종료 코드 1을 돌려줍니다.

## 라이브러리로 쓰기

```python
from rea_hwp import HwpDocument, page_to_svg, render_pages

document = HwpDocument.open("example.hwp")      # 경로나 bytes, 암호는 두 번째 인자
for page in render_pages(document):
    svg = page_to_svg(page)                      # str
    print(page.number, len(svg))
```

`Page.number`는 1부터입니다. 예시 전체는 [`examples/`](examples)에 있습니다.

| 파일 | 내용 |
|---|---|
| [`examples/convert.py`](examples/convert.py) | 파일 하나를 쪽 SVG 묶음(`page-NNNN.svg`)으로. |
| [`examples/upload.py`](examples/upload.py) | 업로드받은 bytes를 열어 암호·오류별로 나누고, 그림은 디스크에 따로 저장한 뒤 쪽 SVG 문자열 목록을 돌려줍니다. |
| [`examples/batch.py`](examples/batch.py) | 폴더 안 `*.hwp`를 문서마다 하위 폴더로 변환하고, 열 수 없는 문서는 건너뜁니다. |

### 파일 하나 변환하기

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

만드는 SVG는 `rea-hwp render`의 `page-NNNN.svg`와 같습니다. 뷰어(`index.html`)와 수식 대체 쪽(`.eq.svg`)이 필요하면
`rea-hwp render`를 씁니다.

### 업로드 파일 처리하기

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

`image_href`를 주지 않으면 그림은 `data:` URI로 SVG에 들어갑니다. 같은 그림은 같은 bytes로 넘어옵니다. `mime`은
`image/png`, `image/jpeg`, `image/gif`, `image/bmp`, `image/svg+xml` 등입니다.

모든 오류는 `ReaHwpError`를 상속합니다. 주로 나누는 하위 자료형:

- `PasswordRequiredError`, `WrongPasswordError`: 암호 없음, 틀린 암호
- `UnsupportedFormatError`: HWP 3.0, HWPX, OLE가 아닌 파일
- `DrmProtectedError`: DRM 문서
- `CorruptFileError`: 잘리거나 깨진 파일
- `LimitExceededError`: `Limits` 상한 초과. `HwpDocument.open(..., limits=Limits(max_records=...))`로 바꿉니다.

### 렌더 설정

```python
from pathlib import Path

from rea_hwp import RenderOptions

options = RenderOptions(font_dirs=(Path("/usr/share/fonts/truetype"),))
pages = render_pages(document, options=options)
```

`font_dirs`는 글맵시 글꼴(TTF·TTC)을 찾을 폴더입니다. 기본값은 Windows 글꼴 폴더와 한컴 공용 글꼴 폴더입니다. 그리지 못한
글맵시는 `page.missing`에 남습니다. 라이브러리는 환경 변수를 읽지 않습니다.

## 출력 형식

`rea-hwp render -o out`이 만드는 파일입니다.

| 파일 | 내용 |
|---|---|
| `page-NNNN.svg` | 쪽 하나. 번호는 1부터 네 자리. `viewBox`가 쪽 크기(pt)입니다. |
| `page-NNNN.eq.svg` | 수식이 있는 쪽에만. 수식 글꼴 글자를 유니코드로 바꿔 OFL 수학 글꼴로 그린 판입니다. |
| `index.html` | 쪽 SVG를 `<img>`로 이어 보여 주는 뷰어. 수식 글꼴이 없는 브라우저에서는 `.eq.svg`로 바꿔 보여 줍니다. |

### SVG

글자는 `<text>` 하나에 글자 하나씩 들어가며, 좌표 단위는 pt입니다.

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="595.28pt" height="841.88pt" viewBox="0 0 595.28 841.88">
<rect width="595.28" height="841.88" fill="#ffffff"/>
<text x="212.64" y="94.935" font-size="20" font-family="HY헤드라인M" >통</text>
```

`x`, `y`는 글자 원점(기준선 왼쪽 끝)이고 쪽 왼쪽 위가 0입니다.

### HTML 뷰어

`index.html`은 같은 폴더의 `page-NNNN.svg`를 `<img>`로 불러오므로 웹 서버 없이 파일로 열립니다. 쪽마다 투명한 글 층을
겹쳐 두어 마우스로 글을 골라 복사하고 브라우저 찾기(Ctrl+F)로 찾습니다. 출력 폴더를 그대로 정적 파일로 올려도 됩니다.

## 제한 사항

- HWP 5.0만 엽니다. HWPX와 HWP 3.0은 `UnsupportedFormatError`, DRM 문서는 `DrmProtectedError`를 냅니다.
- SVG에는 글꼴 파일을 넣지 않고 `font-family="휴먼명조"`처럼 이름만 적습니다. 보는 PC에 그 글꼴이 없으면 브라우저가 다른
  글꼴로 그리며, 글자 위치는 그대로이고 모양만 달라집니다.
- 글맵시는 `fonttools`와 글꼴 파일이 모두 있어야 그립니다.
- 문서를 고치거나 저장하는 기능은 없습니다.

## 기여

```sh
uv sync
uv run pytest
uv run ruff check
```

말뭉치 문서를 쓰는 시험은 환경 변수 `REA_HWP_CORPUS`가 없으면 건너뜁니다. `bench/`는 한글이 만든 PDF와 렌더 결과를 비교하는
평가 도구이며 `bench` 묶음(`pymupdf`)이 필요합니다.

`rea_hwp.__version__`은 렌더 결과나 공개 API가 바뀔 때마다 올리고, 바뀐 내용은 [CHANGELOG.md](CHANGELOG.md)에 적습니다.

## 라이선스

[MIT](LICENSE). [NOTICE](NOTICE)도 함께 보세요.
