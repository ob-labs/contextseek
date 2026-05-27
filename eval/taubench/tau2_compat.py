"""Python 3.13 compatibility patch for tau2-bench.

tau2-bench imports ``audioop`` which was removed in Python 3.13.
We only use text (half-duplex) mode, so we inject a dummy ``audioop``
module before any tau2 imports happen. Import this module first.
"""

import sys
import types

if "audioop" not in sys.modules:
    _dummy = types.ModuleType("audioop")
    _dummy_fns = [
        "add", "mul", "ratecv", "lin2lin", "tomono", "tostereo",
        "rms", "findmax", "maxpp", "avgpp", "max", "min",
        "findfactor", "bias", "cross", "getsample", "reverse",
        "byteswap", "alaw2lin", "lin2alaw", "ulaw2lin", "lin2ulaw",
        "adpcm2lin", "lin2adpcm",
    ]
    for _name in _dummy_fns:
        setattr(_dummy, _name, lambda *a, **kw: b"")
    _dummy.error = Exception  # audioop.error
    sys.modules["audioop"] = _dummy


# Also provide the tau2 environment setup
def setup_tau2_data_dir():
    """Ensure tau2 data directory is configured."""
    import os
    from pathlib import Path

    candidates = []

    configured = os.environ.get("TAU2_DATA_DIR")
    if configured:
        candidates.append(Path(configured))

    repo_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            repo_root / ".tau2-bench" / "data",
            Path("/tmp/tau2-bench/data"),
            Path("/tmp/tau-bench/data"),
        ]
    )

    for data_dir in candidates:
        if data_dir.exists():
            os.environ.setdefault("TAU2_DATA_DIR", str(data_dir))
            return
