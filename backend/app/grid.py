"""Delhi spatial grid and lat/lng -> cell lookup.

The grid is a plain equirectangular lattice over the Delhi bounding box. At
Delhi's latitude (~28.6 N) the distortion across the ~50 km width of the city is
under half a percent, which is well inside the noise floor of the data layers we
put on top of it. This keeps cell lookup to pure arithmetic — no spatial index,
no PostGIS — which matters because we do one lookup per sampled route point.

Cell ids are stable strings of the form ``r0123c0045`` (row, then column,
counting from the south-west corner). They are stable as long as the bbox and
cell size do not change; both are pinned in ``config.py`` and written into the
grid manifest so a rebuild that changes them is visible in review.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import CELL_SIZE_M, DELHI_BBOX, PROCESSED_DIR

# Metres per degree of latitude. Constant enough for our purposes.
M_PER_DEG_LAT = 111_320.0

GRID_MANIFEST_PATH = PROCESSED_DIR / "grid.json"


@dataclass(frozen=True)
class GridSpec:
    """Everything needed to reproduce a grid exactly."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    cell_size_m: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @staticmethod
    def from_file(path: Path) -> GridSpec:
        return GridSpec(**json.loads(Path(path).read_text(encoding="utf-8")))


class Grid:
    """A fixed lattice of square-ish cells covering Delhi."""

    def __init__(self, spec: GridSpec | None = None) -> None:
        self.spec = spec or GridSpec(*DELHI_BBOX, cell_size_m=CELL_SIZE_M)

        mean_lat = (self.spec.min_lat + self.spec.max_lat) / 2.0
        m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(mean_lat))

        self.deg_lat = self.spec.cell_size_m / M_PER_DEG_LAT
        self.deg_lon = self.spec.cell_size_m / m_per_deg_lon

        self.n_rows = math.ceil((self.spec.max_lat - self.spec.min_lat) / self.deg_lat)
        self.n_cols = math.ceil((self.spec.max_lon - self.spec.min_lon) / self.deg_lon)

    # -- core lookup ------------------------------------------------------

    def cell_id(self, lat: float, lon: float) -> str | None:
        """Return the cell containing this point, or None if outside the grid.

        Callers must handle None: a route can clip the edge of the NCT boundary,
        and silently treating that as cell (0,0) would be a real scoring bug.
        """
        rc = self.row_col(lat, lon)
        if rc is None:
            return None
        return self.encode(*rc)

    def row_col(self, lat: float, lon: float) -> tuple[int, int] | None:
        if not (self.spec.min_lat <= lat <= self.spec.max_lat):
            return None
        if not (self.spec.min_lon <= lon <= self.spec.max_lon):
            return None
        row = int((lat - self.spec.min_lat) / self.deg_lat)
        col = int((lon - self.spec.min_lon) / self.deg_lon)
        # A point exactly on the north/east edge would land one cell past the end.
        row = min(row, self.n_rows - 1)
        col = min(col, self.n_cols - 1)
        return row, col

    @staticmethod
    def encode(row: int, col: int) -> str:
        return f"r{row:04d}c{col:04d}"

    @staticmethod
    def decode(cell_id: str) -> tuple[int, int]:
        return int(cell_id[1:5]), int(cell_id[6:10])

    # -- geometry ---------------------------------------------------------

    def cell_center(self, cell_id: str) -> tuple[float, float]:
        """(lat, lon) of the cell's centre."""
        row, col = self.decode(cell_id)
        lat = self.spec.min_lat + (row + 0.5) * self.deg_lat
        lon = self.spec.min_lon + (col + 0.5) * self.deg_lon
        return lat, lon

    def cell_bounds(self, cell_id: str) -> tuple[float, float, float, float]:
        """(min_lon, min_lat, max_lon, max_lat) of the cell."""
        row, col = self.decode(cell_id)
        min_lat = self.spec.min_lat + row * self.deg_lat
        min_lon = self.spec.min_lon + col * self.deg_lon
        return min_lon, min_lat, min_lon + self.deg_lon, min_lat + self.deg_lat

    def all_cell_ids(self) -> list[str]:
        return [
            self.encode(r, c) for r in range(self.n_rows) for c in range(self.n_cols)
        ]

    @property
    def n_cells(self) -> int:
        return self.n_rows * self.n_cols

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Grid {self.n_rows}x{self.n_cols} = {self.n_cells} cells "
            f"@ {self.spec.cell_size_m:.0f}m>"
        )


# ---------------------------------------------------------------------------
# Distance helpers — used to cut polylines into segments and weight them.
# ---------------------------------------------------------------------------

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def interpolate(
    lat1: float, lon1: float, lat2: float, lon2: float, t: float
) -> tuple[float, float]:
    """Linear interpolation between two nearby points. Fine at street scale."""
    return lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t


# ---------------------------------------------------------------------------
# Module-level singleton. Built once, reused for every request.
# ---------------------------------------------------------------------------

_grid: Grid | None = None


def get_grid() -> Grid:
    """Load the grid, preferring a built manifest so scoring matches ingestion.

    If ``data/processed/grid.json`` exists we use it — that is the grid the
    per-cell layers were actually built against. Falling back to config defaults
    when they disagree would silently misalign every lookup.
    """
    global _grid
    if _grid is None:
        if GRID_MANIFEST_PATH.exists():
            _grid = Grid(GridSpec.from_file(GRID_MANIFEST_PATH))
        else:
            _grid = Grid()
    return _grid


def reset_grid() -> None:
    """Drop the cached grid. Used by tests and by scripts/build_grid.py."""
    global _grid
    _grid = None
