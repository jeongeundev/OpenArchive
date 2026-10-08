"""원본 판의 PDF 변환. 변환기는 선택 설치하며 격리 없이 실행하지 않는다 (ADR-058)."""

import io
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from pypdf import PdfReader

from openarchive.config import get_settings

PREVIEW_CONVERTIBLE_EXTENSIONS: frozenset[str] = frozenset({"hwp", "hwpx", "docx", "xlsx", "pptx"})


class ConverterUnavailable(Exception):
    """변환기·글꼴·격리가 없어 설치 뒤 다시 걸어야 하는 변환."""


class PreviewRenderFailed(Exception):
    """재시도로 고쳐지지 않는 결과 PDF 불량."""


def contains_hangul(text: str) -> bool:
    return re.search("[\uac00-\ud7a3]", text) is not None


def pdf_has_hangul(pdf: bytes) -> bool:
    return any(
        contains_hangul(page.extract_text() or "") for page in PdfReader(io.BytesIO(pdf)).pages
    )


def _executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ConverterUnavailable(f"실행 파일이 없습니다: {name}")
    return str(Path(path).resolve())


def check_converter(extension: str) -> str:
    settings = get_settings()
    return _executable(
        settings.preview_rhwp_bin if extension in {"hwp", "hwpx"} else settings.preview_soffice_bin
    )


class _ConversionProcessError(subprocess.CalledProcessError):
    def __str__(self) -> str:
        return f"변환기 종료 코드 {self.returncode}: {self.stderr}"


def _run(args: list[str], timeout: float) -> None:
    # 파이프를 계속 비우되 끝 4KB만 보관한다. 많은 stderr로 메모리·디스크를 채우지 않는다.
    process = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True
    )
    tail = bytearray()

    def drain():
        with process.stderr:
            while chunk := process.stderr.read(4096):
                tail.extend(chunk)
                del tail[:-4096]

    reader = threading.Thread(target=drain)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    finally:
        reader.join()
    if returncode:
        raise _ConversionProcessError(
            returncode, args, stderr=tail.decode("utf-8", errors="replace")
        )


def check_fonts() -> None:
    binary = _executable("fc-list")
    try:
        result = subprocess.run(
            [binary, ":lang=ko", "family"],
            capture_output=True,
            timeout=get_settings().preview_timeout_seconds,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ConverterUnavailable("한글 글꼴을 확인할 수 없습니다: fc-list") from exc
    if not result.stdout.strip():
        raise ConverterUnavailable("한글 글꼴이 없습니다: fc-list :lang=ko")


def _sandbox_args(converter: str, input_file: Path, output_dir: Path) -> list[str]:
    args = [
        _executable(get_settings().preview_bwrap_bin),
        "--unshare-net",
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-pid",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--ro-bind",
        "/usr",
        "/usr",
    ]
    for name in ("/bin", "/lib", "/lib64"):
        path = Path(name)
        if path.is_symlink():
            args += ["--symlink", os.readlink(path), name]
        elif path.exists():
            args += ["--ro-bind", name, name]
    # LO의 HTTP 그림 처리도 CA 초기화를 한다. 비밀 키 없이 공개 인증서만 노출한다.
    for name in (
        "/etc/fonts",
        "/etc/ssl/certs",
        "/etc/libreoffice/registry",
        "/etc/libreoffice/psprint.conf",
        "/etc/ld.so.cache",
        "/etc/localtime",
    ):
        if Path(name).exists():
            args += ["--ro-bind", name, name]
    args += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(input_file),
        str(input_file),
        "--bind",
        str(output_dir),
        str(output_dir),
        "--chdir",
        "/tmp",
    ]
    # /usr 안의 실행 파일은 이미 보인다. 그 밖은 디렉터리 대신 파일 하나만 노출한다.
    if not Path(converter).is_relative_to("/usr"):
        args += ["--ro-bind", converter, "/converter"]
    return args


def check_sandbox(args: list[str]) -> None:
    try:
        _run([*args, "/usr/bin/true"], get_settings().preview_timeout_seconds)
    except (subprocess.SubprocessError, OSError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise ConverterUnavailable(f"격리를 쓸 수 없습니다: bwrap: {detail[-4096:]}") from exc


def check_conversion_tools() -> None:
    """실변환 테스트도 운영과 같은 도구·글꼴·격리 점검을 사용한다."""
    converters = [check_converter("hwp"), check_converter("docx")]
    check_fonts()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "input.hwp"
        source.touch()
        output = root / "output"
        output.mkdir()
        for converter in converters:
            check_sandbox(_sandbox_args(converter, source, output))


def convert_to_pdf(data: bytes, filename: str, *, document_text: str) -> bytes:
    """원본 판 바이트를 격리된 변환기로 PDF로 바꾼다. 워커가 to_thread로 호출한다."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in PREVIEW_CONVERTIBLE_EXTENSIONS:
        raise ValueError(f"변환 대상이 아닌 확장자: {extension}")
    converter = check_converter(extension)
    # bwrap 부재도 글꼴보다 먼저 확인한다. 변환기를 직접 실행하는 폴백은 없다.
    _executable(get_settings().preview_bwrap_bin)
    check_fonts()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / f"input.{extension}"
        source.write_bytes(data)
        output = root / "output"
        output.mkdir()
        sandbox = _sandbox_args(converter, source, output)
        check_sandbox(sandbox)
        executable = converter if Path(converter).is_relative_to("/usr") else "/converter"
        if extension in {"hwp", "hwpx"}:
            command = [executable, "export-pdf", str(source), "-o", str(output / "input.pdf")]
        else:
            command = [
                executable,
                "--headless",
                "--norestore",
                "-env:UserInstallation=file:///tmp/lo-profile",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output),
                str(source),
            ]
        _run([*sandbox, *command], get_settings().preview_timeout_seconds)
        try:
            pdf = (output / "input.pdf").read_bytes()
            reader = PdfReader(io.BytesIO(pdf))
            if not reader.pages:
                raise ValueError("PDF에 쪽이 없습니다")
            hangul = pdf_has_hangul(pdf)
        except Exception as exc:
            raise PreviewRenderFailed("결과 PDF가 없거나 읽을 수 없습니다") from exc
        if contains_hangul(document_text) and not hangul:
            raise PreviewRenderFailed("한글이 그려지지 않았습니다")
        return pdf
