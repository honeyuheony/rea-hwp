"""업로드받은 HWP bytes를 쪽 SVG 문자열 목록으로 바꾼다. 그림은 SVG에 넣지 않고 image_dir에 따로 저장한다.

python upload.py example.hwp images [password]
"""

from __future__ import annotations

import hashlib
import sys
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


if __name__ == "__main__":
    source, images, *rest = sys.argv[1:]
    try:
        svgs = render_upload(Path(source).read_bytes(), Path(images), password=rest[0] if rest else None)
    except UploadRejected as rejected:
        print(f"{rejected.reason}: {rejected}")
        sys.exit(1)
    print(f"{len(svgs)} pages, {len(list(Path(images).iterdir()))} images in {images}")
