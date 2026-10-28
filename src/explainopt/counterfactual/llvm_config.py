from pathlib import Path
import os

OPT = Path(
    os.environ.get(
        "EXPLAINOPT_OPT",
        r"C:\Users\Kamesh Shivan\Downloads\clang+llvm-23.1.4-x86_64-pc-windows-msvc\bin\opt.exe",
    )
)

def get_opt_path():
    if OPT.is_file():
        return str(OPT)
    return None
