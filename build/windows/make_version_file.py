"""Write the version resource PyInstaller stamps into the exe, from ``sentinel.__version__``.

One number in one place. The built file then answers
``(Get-Item SystemSentinel.exe).VersionInfo.ProductVersion`` with it, which is how Explorer's
Properties page and a downloaded copy deciding whether it is an update both read the version of a
copy without starting it (``sentinel.launcher.file_version``).

The spec calls this while it builds, so the resource cannot be left behind at an older version
than the code it is stamped onto. Running it by hand prints the file it wrote.
"""

from pathlib import Path

from sentinel import __version__

COMPANY = "MainThread"
PRODUCT = "System Sentinel"
DESCRIPTION = "System Sentinel, a stethoscope for a Windows computer"
COPYRIGHT = "MIT License"
FILENAME = "SystemSentinel.exe"

# PyInstaller reads this file as Python: a VSVersionInfo tree, exactly as its own utilities write
# one. 0x40004 is VOS_NT_WINDOWS32, 0x1 is VFT_APP; 1033 and 1200 are US English and Unicode, and
# '040904B0' is the same pair as the name of the block the strings are filed under.
TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', {company!r}),
        StringStruct('FileDescription', {description!r}),
        StringStruct('FileVersion', {version!r}),
        StringStruct('InternalName', 'SystemSentinel'),
        StringStruct('LegalCopyright', {copyright!r}),
        StringStruct('OriginalFilename', {filename!r}),
        StringStruct('ProductName', {product!r}),
        StringStruct('ProductVersion', {version!r}),
      ]),
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""


def numbers(version: str) -> tuple[int, int, int, int]:
    """The version as the four numbers Windows keeps alongside the string, padded with zeros."""
    parts = [int(part) for part in version.split(".") if part.isdigit()][:4]
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def write_version_file(path: str | Path, version: str = __version__) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        TEMPLATE.format(
            numbers=numbers(version),
            version=version,
            company=COMPANY,
            product=PRODUCT,
            description=DESCRIPTION,
            copyright=COPYRIGHT,
            filename=FILENAME,
        ),
        encoding="utf-8",
    )
    return destination


if __name__ == "__main__":
    print(write_version_file(Path(__file__).with_name("version.txt")))
