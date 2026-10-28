# rea-hwp

HWP 5.0 문서를 쪽마다 SVG로 그립니다. 쪽 나눔과 글자 위치는 한컴오피스 한글 2022의 인쇄 결과에 맞춥니다.

## 설치

Python 3.12 이상이 필요합니다.

```sh
pip install "git+https://github.com/honeyuheony/rea-hwp.git@main"
uv add "rea-hwp @ git+https://github.com/honeyuheony/rea-hwp.git@main"
```

글맵시까지 그리려면 `fonts` 묶음(`fonttools`)을 함께 설치합니다: `pip install "rea-hwp[fonts] @ git+https://github.com/honeyuheony/rea-hwp.git@main"`.

## 렌더

```sh
rea-hwp render example.hwp -o out --glyphs
# 6 pages -> out/index.html
```

```
out/
  index.html
  page-0001.svg
  page-0002.svg
  ...
  glyphs.json
```

| 파일 | 내용 |
|---|---|
| `page-NNNN.svg` | 쪽 하나. 번호는 1부터, 네 자리. 단위는 pt(`viewBox`가 쪽 크기 pt)입니다. |
| `page-NNNN.eq.svg` | 수식이 있는 쪽에만 생깁니다. 수식 글꼴 글자를 유니코드로 바꿔 OFL 수학 글꼴로 그린 판입니다. |
| `index.html` | 쪽 SVG를 `<img>`로 이어 보여 주는 뷰어입니다. 서버 없이 파일로 열면 되고, 투명한 글 층이 있어 글을 골라 복사할 수 있습니다. 수식 글꼴이 없는 브라우저에서는 `.eq.svg`로 바꿔 보여 줍니다. |
| `glyphs.json` | `--glyphs`를 줄 때만 생깁니다. |

암호가 걸린 문서는 `--password`로 엽니다.

```sh
rea-hwp render secret.hwp -o out --password 123456
```

`rea-hwp check example.hwp`는 레코드를 다시 인코딩해 원본 스트림과 같은지 비교하고, 다르면 종료 코드 1을 돌려줍니다.

## glyphs.json

쪽마다 `[글자, x, y]` 목록을 담은 배열입니다.

```json
[
  [["통", 212.64, 94.935], ["합", 242.64, 94.935], ["재", 272.64, 94.935], ...],
  [...]
]
```

| 항목 | 뜻 |
|---|---|
| 바깥 배열 | 쪽 차례. `glyphs[0]`이 `page-0001.svg`입니다. |
| 글자 | 쪽에 찍은 글자 하나. 공백 글자는 뺍니다. 기호 글꼴 PUA(U+F020~F0FF)는 cp1252 글자로, 한컴 전용 기호(평면 15 PUA)는 U+F000으로 바꿔 적습니다. 수식 글꼴 글자는 그 글꼴의 코드 그대로입니다. |
| `x`, `y` | 글자 원점(기준선 왼쪽 끝). 쪽 왼쪽 위가 0이고 단위는 pt, 소수 셋째 자리까지입니다. SVG 좌표와 같습니다. |

순서는 SVG에 그린 차례입니다. 읽는 차례와 다를 수 있습니다. 본문·표·머리말·꼬리말·수식 글자가 모두 들어갑니다.

## 파이썬

```python
from pathlib import Path

from rea_hwp import HwpDocument, page_to_svg, render_pages

document = HwpDocument.open("example.hwp")   # 경로나 bytes. 암호는 두 번째 인자: open("secret.hwp", "123456")
pages = render_pages(document)
out = Path("svg")
out.mkdir(exist_ok=True)
for page in pages:
    (out / f"page-{page.number:04d}.svg").write_text(page_to_svg(page), encoding="utf-8")
```

그림은 기본으로 `data:` URI로 SVG에 들어갑니다. 따로 저장하려면 `image_href`에 `(bytes, mime) -> 주소` 함수를 넘깁니다.

```python
saved = {}

def store(data: bytes, mime: str) -> str:
    name = f"img-{len(saved)}"
    saved[name] = (data, mime)
    return f"/images/{name}"

svg = page_to_svg(pages[0], image_href=store)
```

글맵시 글꼴 폴더는 `RenderOptions.font_dirs`로 줍니다. 기본값은 Windows 글꼴 폴더와 한컴 공용 글꼴 폴더입니다. 그리지 못한
글맵시는 `page.missing`에 남습니다.

```python
from rea_hwp import RenderOptions

options = RenderOptions(font_dirs=(Path("/usr/share/fonts/truetype"),))
pages = render_pages(document, options=options)
```

열거나 그리다 나는 오류는 모두 `rea_hwp.ReaHwpError`를 상속합니다.

## 글꼴

SVG는 글꼴 파일을 넣지 않고 `font-family="휴먼명조"`처럼 이름만 적습니다. 보는 PC에 그 글꼴이 없으면 브라우저가 다른 글꼴로
그리며, 글자 위치는 그대로이고 모양만 달라집니다.

## 판 번호

`rea_hwp.__version__`은 렌더 결과나 공개 API가 바뀔 때마다 올립니다. 바뀐 내용은 [CHANGELOG.md](CHANGELOG.md)에 있습니다.

## 라이선스

MIT. [LICENSE](LICENSE), [NOTICE](NOTICE).
