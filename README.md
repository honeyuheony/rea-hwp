# rea-hwp

rea-hwp는 한글 문서(HWP 5.0)를 읽어 쪽마다 SVG로 그리는 파이썬 라이브러리이자 명령줄 도구입니다. 쪽 나눔과 글자 위치를
한컴오피스 한글 2022가 인쇄하는 모양에 맞춰 고정 배치하므로, 원본 파일 없이도 브라우저에서 문서를 원래 모양대로 보여 주고
글을 골라 복사할 수 있습니다.

할 수 있는 것은 다음과 같습니다.

- HWP 5.0 문서(암호를 건 문서와 배포용 문서 포함)를 열어 레코드와 문단 글을 읽습니다.
- 쪽마다 SVG를 만들고, 쪽 SVG를 넘겨 보며 글을 고르고 복사할 수 있는 HTML 뷰어를 만듭니다.
- 쪽마다 줄 글과 그 위치(pt)를 JSON으로 쓸 수 있는 목록으로 내고, 문단·표가 몇 쪽에 놓였는지 알려 줍니다.
- 읽은 레코드를 다시 같은 바이트로 되돌릴 수 있는지(손실 없이 읽었는지) 점검합니다.

할 수 없는 것도 있습니다.

- HWPX, HWP 3.0, DRM으로 보호한 문서는 열지 못하고 오류를 냅니다.
- Markdown 변환은 제공하지 않습니다. LLM에 넘길 글은 아래 「문단 글 읽기」와 「쪽별 줄 글」에서 설명하는 방법으로 얻습니다.
- 문서를 고쳐 저장하는 기능은 없습니다.

## 설치

Python 3.12 이상이 필요합니다. PyPI에는 올리지 않았으므로 GitHub 저장소에서 바로 설치합니다.

```sh
pip install "git+https://github.com/honeyuheony/rea-hwp.git@main"
# uv 프로젝트라면
uv add "rea-hwp @ git+https://github.com/honeyuheony/rea-hwp.git@main"
```

선택 묶음(extras, 필요할 때만 함께 설치하는 의존성 묶음)은 두 가지입니다.

| 묶음 | 설치하는 것 | 쓰는 곳 |
|---|---|---|
| `fonts` | `fonttools` | 글맵시(글자를 모양 틀에 맞춰 늘이고 휘게 꾸민 개체)를 글꼴 외곽선으로 그립니다. 없으면 글맵시를 그리지 않고 `Page.missing`에 남깁니다. |
| `bench` | `pymupdf` | 저장소의 `bench/` 평가 도구가 한글 PDF와 비교할 때 씁니다. 라이브러리 사용자는 필요 없습니다. |

```sh
pip install "rea-hwp[fonts] @ git+https://github.com/honeyuheony/rea-hwp.git@main"
```

## 명령줄 도구

설치하면 `rea-hwp` 명령이 생깁니다. 하위 명령은 `render`와 `check` 두 가지입니다.

### render: 쪽 SVG와 HTML 뷰어 만들기

```sh
rea-hwp render example.hwp -o out
# 6 pages -> out/index.html
```

`-o`로 준 폴더(없으면 만듭니다)에 다음 파일이 생깁니다.

| 파일 | 내용 |
|---|---|
| `page-0001.svg`, `page-0002.svg`, … | 쪽마다 하나씩 만드는 SVG입니다. 크기 단위는 pt입니다. |
| `page-0001.eq.svg`, … | 수식이 있는 쪽에만 생깁니다. 수식 글꼴이 없는 PC를 위해 수식 글자를 유니코드 글자와 공개 수학 글꼴로 바꿔 그린 쪽입니다. |
| `index.html` | 쪽 SVG를 차례로 보여 주는 단독 HTML 뷰어입니다. |
| `glyphs.json` | `--glyphs`를 줄 때만 생깁니다. 쪽마다 `[글자, x pt, y pt]` 목록을 담은 배열입니다. |

`index.html`은 같은 폴더의 SVG를 `<img>`로 불러오므로 웹 서버 없이 브라우저에서 파일을 바로 열면 됩니다. 쪽 위에 투명한
글 층을 깔아 두었기 때문에 마우스로 글을 골라 복사하거나 브라우저의 찾기 기능으로 글을 찾을 수 있습니다. 수식 글꼴이 없는
브라우저에서는 뷰어가 `.eq.svg` 쪽으로 바꿔 보여 줍니다.

암호를 건 문서는 `--password`로 열기 암호를 줍니다. 암호를 주지 않으면 `PasswordRequiredError`로 끝납니다.

```sh
rea-hwp render secret.hwp -o out --password 123456
```

`render`는 환경 변수 세 개를 읽습니다. `REA_HWP_FONT_DIRS`는 글맵시 글꼴을 찾을 폴더 목록(Linux에서는 `:`, Windows에서는
`;`로 잇습니다)이고, `REA_HWP_TABLE_ENGINE=0`과 `REA_HWP_ENGINE_RECOMPOSE=0`은 표 배치를 예전 방식으로 되돌리는 비교용
스위치입니다.

### check: 손실 없이 읽었는지 점검하기

```sh
rea-hwp check example.hwp
# DocInfo: roundtrip ok
# BodyText/Section0: roundtrip ok
# records kept as raw bytes: 0
```

스트림마다 레코드를 모델로 읽었다가 다시 바이트로 만들어 원본과 같은지 비교합니다. 다른 스트림이 하나라도 있으면
`ROUNDTRIP MISMATCH`를 찍고 종료 코드 1을 돌려줍니다. 마지막 줄은 타입 모델로 해석하지 못해 원본 바이트로 남긴 레코드 수입니다.

## 파이썬 API

공개 이름은 모두 `rea_hwp`에서 바로 가져옵니다. 목록은 `rea_hwp.__all__`에 있습니다.

### 문서 열기

```python
from rea_hwp import HwpDocument

document = HwpDocument.open("example.hwp")          # 경로, Path, bytes를 모두 받습니다
secret = HwpDocument.open("secret.hwp", "123456")   # 열기 암호
print(document.header.version, document.section_numbers)
# (5, 0, 1, 7) [0]
```

`document.header`에는 `version`, `compressed`, `password_protected`, `distributed`(배포용 문서), `drm` 같은 파일 머리 정보가
있습니다.

### 문단 글 읽기

`HwpDocument.paragraph(section, index)`는 구역 `section`의 `index`번째 레코드가 문단 머리(`PARA_HEADER`)일 때 그 문단을
돌려줍니다. `Paragraph.text`는 글자만 이은 문자열이며 탭은 `"\t"`로 들어갑니다. 아래 예시는 문서의 모든 문단 글을 차례대로
모아 평문 하나로 만듭니다. 표 칸 안의 문단도 문서에 나온 차례대로 들어갑니다. LLM에 문서 내용을 넘길 때는 이렇게 얻은 평문을
씁니다.

```python
from rea_hwp import HwpDocument
from rea_hwp.tags import PARA_HEADER

document = HwpDocument.open("example.hwp")
paragraphs = []
for section in document.section_numbers:
    for record in document.section_records(section):
        if record.tag == PARA_HEADER:
            paragraph = document.paragraph(section, record.index)
            if paragraph.text.strip():
                paragraphs.append(paragraph.text)
text = "\n".join(paragraphs)
```

`section_records(section)`은 구역 스트림의 레코드를 나온 차례 그대로 담은 `RecordView` 목록입니다. `RecordView.index`와
원본 위치 문자열 `"구역:index"`(예: `"0:15"`)는 공개한 뒤 바꾸지 않기로 한 규칙이므로, 바깥 시스템에 문단 식별자로 저장해도
됩니다. `Paragraph.id`가 이 문자열을 돌려줍니다.

### 쪽 렌더와 SVG

```python
from pathlib import Path

from rea_hwp import HwpDocument, page_to_svg, render_pages

pages = render_pages(HwpDocument.open("example.hwp"))
out = Path("svg")
out.mkdir(exist_ok=True)
for page in pages:
    (out / f"page-{page.number:04d}.svg").write_text(page_to_svg(page), encoding="utf-8")
```

`render_pages`는 쪽마다 그릴 항목을 담은 `Page` 목록을 돌려주고, `page_to_svg`는 쪽 하나를 SVG 문자열로 바꿉니다.
`Page.number`는 1부터 셉니다.

그림은 기본으로 SVG 안에 `data:` URI로 들어갑니다. 그림을 따로 저장해 주소로 걸고 싶으면 `image_href`에 `(바이트, mime)`을
받아 주소를 돌려주는 함수를 줍니다. 같은 그림은 같은 바이트로 넘어옵니다.

```python
saved = {}

def store(data: bytes, mime: str) -> str:
    name = f"img-{len(saved)}"
    saved[name] = (data, mime)
    return f"/images/{name}"

svg = page_to_svg(pages[0], image_href=store)
```

렌더 설정은 `RenderOptions`로 줍니다. 라이브러리 경로는 환경 변수를 읽지 않고 이 객체만 봅니다. Linux 서버에서 글맵시를
그리려면 `fonts` 묶음을 설치하고 글꼴 폴더를 `font_dirs`로 알려 줍니다. 기본값은 Windows 글꼴 폴더와 한컴 공용 글꼴 폴더입니다.

```python
from pathlib import Path

from rea_hwp import HwpDocument, RenderOptions, render_pages

options = RenderOptions(font_dirs=(Path("/usr/share/fonts/truetype"),))
pages = render_pages(HwpDocument.open("example.hwp"), options=options)
for page in pages:
    for missing in page.missing:      # 그리지 못한 개체: kind, reason, box
        print(page.number, missing)
```

### 쪽별 줄 글

`page_text_lines(page)`는 쪽에 놓인 줄 글을 JSON으로 바로 쓸 수 있는 dict 목록으로 돌려줍니다. 좌표는 SVG와 같은 pt이고,
`text`는 HTML 뷰어의 글 층과 같은 복사용 글입니다. 쪽 단위로 끊어 LLM에 넘기거나 검색 결과에 쪽 번호를 붙일 때 씁니다.

```python
from rea_hwp import page_text_lines

lines = page_text_lines(pages[0])
print(lines[0])
# {'text': '통 합 재 정 통 계', 'x_pt': 212.615, 'baseline_pt': 94.935, 'width_pt': 170.0, 'size_pt': 20.0, 'paragraph': '0:15'}

for page in pages:
    print(f"--- {page.number}쪽 ---")
    print("\n".join(line["text"] for line in page_text_lines(page)))
```

`paragraph`는 그 줄이 속한 문단의 원본 위치이고, 바탕쪽 글과 쪽 번호처럼 문단에 속하지 않는 글은 `None`입니다.

### 문단·표가 놓인 쪽 찾기

`paragraph_pages(pages)`는 원본 위치마다 그것이 처음 놓인 쪽 번호를 돌려줍니다. 문단 글 검색 결과에 쪽 번호를 붙일 때 씁니다.
글줄이 있는 문단과 놓인 표·그림이 키로 들어가고, 빈 문단과 바탕쪽 글은 들어가지 않습니다.
`table_row_pages(pages)`는 표마다 행 번호(0부터)별로 그 행이 처음 놓인 쪽 번호를 돌려줍니다.

```python
from rea_hwp import paragraph_pages, table_row_pages

where = paragraph_pages(pages)
print(where["0:15"])        # 1
rows = table_row_pages(pages)
```

### 기호 글꼴 글자를 유니코드로 바꾸기 (0.2.15부터)

한글 문서에는 Symbol·Wingdings 같은 기호 글꼴로 찍은 글자가 사용자 정의 영역(PUA, 표준 글자가 배정되지 않아 글꼴마다 뜻이
다른 코드 영역)의 코드로 저장된 경우가 많습니다. `symbol_unicode(char, font)`는 이런 글자와 한컴 전용 문자, C1 제어 문자를
한글이 그리는 모양의 유니코드 글자로 바꿔 줍니다. 바꾸지 않는 글자는 `None`을 돌려줍니다.

```python
from rea_hwp import symbol_unicode

symbol_unicode("\uf0b7", "Symbol")     # '•'
symbol_unicode("\uf0e0", "Wingdings")  # '🡪'
symbol_unicode("\uf0b7")               # None: 글꼴을 모르면 기호 영역은 바꾸지 않습니다
symbol_unicode("\x85")                 # '□': C1 제어 문자는 글꼴과 상관없이 빈 글자 상자로 바꿉니다
```

0.2.15부터 `Paragraph.text`는 글꼴과 상관없는 규칙(제어 문자 지우기, 한컴 전용 문자, 옛한글 등)만 적용한 글을 돌려줍니다.
기호 글꼴 글자는 글꼴을 알아야 바꿀 수 있으므로, 글꼴을 아는 쪽에서 `symbol_unicode`를 씁니다. 원래 글자는
`Paragraph.items`에 그대로 있습니다.

## 업로드 파일을 다룰 때

믿을 수 없는 파일을 열 때는 오류 자료형으로 사용자 안내를 고릅니다. 모든 오류는 `ReaHwpError`를 상속합니다.

| 오류 | 뜻 |
|---|---|
| `UnsupportedFormatError` | HWP 3.0, HWPX, OLE 복합 파일이 아닌 파일, 지원하지 않는 암호 방식입니다. |
| `DrmProtectedError` | DRM으로 보호한 문서입니다. |
| `CorruptFileError` | 잘린 레코드·스트림, 빠진 스트림, 깨진 OLE 그릇, 풀 수 없는 압축입니다. |
| `CorruptEncryptedStreamError` | 배포용 문서의 본문을 풀지 못했습니다. `CorruptFileError`이면서 `HwpDecryptError`입니다. |
| `PasswordRequiredError` | 열기 암호가 걸렸는데 암호를 주지 않았습니다. |
| `WrongPasswordError` | 준 암호가 틀렸습니다. |
| `LimitExceededError` | 푼 크기나 레코드 수가 `Limits` 상한을 넘었습니다. |

`HwpFormatError`는 앞의 세 형식 오류의 공통 상위 자료형이고, `HwpDecryptError`는 암호 오류의 공통 상위 자료형입니다. 두
자료형은 `ValueError`도 상속하지만 `LimitExceededError`는 `ValueError`가 아니므로 `except ValueError`에 묻히지 않습니다.
레코드가 지나치게 깊이 겹친 문서는 `render_pages`가 `CorruptFileError`로 알립니다.

```python
from rea_hwp import HwpDocument, PasswordRequiredError, ReaHwpError, WrongPasswordError

try:
    document = HwpDocument.open(uploaded_bytes, password)
except PasswordRequiredError:
    ...  # 암호를 물어봅니다
except WrongPasswordError:
    ...  # 암호가 틀렸다고 알립니다
except ReaHwpError as error:
    ...  # 열 수 없는 파일이라고 알립니다
```

`HwpDocument.open(..., limits=Limits(...))`로 상한을 바꿀 수 있습니다. 기본 상한은 다음과 같습니다.

| 필드 | 기본값 | 뜻 |
|---|---|---|
| `max_stream_bytes` | 100MB | DocInfo와 본문 스트림을 풀어 둔 크기의 합 |
| `max_records` | 1,000,000 | 한 문서의 레코드 수 |
| `max_bin_data_bytes` | 64MB | 그림 등 BinData 항목 하나를 풀어 둔 크기 |
| `max_total_bin_data_bytes` | 256MB | 한 문서의 BinData 항목을 풀어 둔 크기의 합 |

```python
from rea_hwp import HwpDocument, LimitExceededError, Limits

try:
    HwpDocument.open(data, limits=Limits(max_records=100))
except LimitExceededError as error:
    print(error)  # document has more than 100 records; raise Limits.max_records
```

## 글꼴

SVG는 글자마다 위치를 고정하고 글꼴은 이름(예: `font-family="휴먼명조"`)으로만 지정합니다. 글꼴 파일을 SVG에 넣지 않으므로,
보는 PC에 그 글꼴이 없으면 브라우저가 다른 글꼴로 대신 그립니다. 이때 글자 자리와 줄 나눔은 그대로이고 글자 모양만 달라집니다.
한글 문서에서 쓰는 글꼴(함초롬체, 휴먼명조 등)을 설치해 두면 원본에 가깝게 보입니다.

`fonts` 묶음은 화면에 보이는 글꼴이 아니라 글맵시를 그릴 때만 씁니다. 글맵시는 글꼴 파일에서 외곽선을 읽어 도형으로 그리므로
`fonttools`와 글꼴 파일(`RenderOptions.font_dirs`)이 모두 있어야 합니다.

## 판 번호와 변경 기록

판 번호는 `rea_hwp.__version__`과 `pyproject.toml`에 같게 적습니다. 공개 API나 읽기·렌더 결과가 바뀌는 커밋마다 판 번호를
올리므로, 저장해 둔 SVG나 줄 글을 다시 만들지 판단할 때 쓰도록 이 값을 함께 저장해 두면 됩니다. 판마다 바뀐 내용은
[CHANGELOG.md](CHANGELOG.md)에 있습니다.

## 개발

`bench/`는 한컴 한글이 만든 PDF와 렌더 결과를 비교해 깨진 쪽 수를 세는 평가 도구입니다. 시험은 `uv run pytest`로 돌리며,
말뭉치 문서를 쓰는 시험은 환경 변수 `REA_HWP_CORPUS`가 없으면 건너뜁니다.

## 라이선스

MIT 라이선스입니다. [LICENSE](LICENSE)와 [NOTICE](NOTICE)를 보세요.
