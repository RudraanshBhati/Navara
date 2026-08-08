from __future__ import annotations

import pytest

from app.config import CELL_SIZE_M, DELHI_BBOX
from app.grid import Grid, GridSpec, haversine_m


def test_grid_covers_delhi_at_expected_scale(grid: Grid):
    # Sanity check on the whole premise of a 500 m grid: it should land in the
    # low thousands of cells. An order of magnitude either way means the degree
    # conversion is wrong.
    assert 3_000 < grid.n_cells < 15_000
    assert grid.spec.cell_size_m == CELL_SIZE_M


def test_cells_are_roughly_square_on_the_ground(grid: Grid):
    cell = grid.cell_id(28.6139, 77.2090)
    min_lon, min_lat, max_lon, max_lat = grid.cell_bounds(cell)

    height = haversine_m(min_lat, min_lon, max_lat, min_lon)
    width = haversine_m(min_lat, min_lon, min_lat, max_lon)

    assert height == pytest.approx(CELL_SIZE_M, rel=0.02)
    assert width == pytest.approx(CELL_SIZE_M, rel=0.02)


def test_roundtrip_encode_decode(grid: Grid):
    for row, col in [(0, 0), (7, 13), (grid.n_rows - 1, grid.n_cols - 1)]:
        assert Grid.decode(Grid.encode(row, col)) == (row, col)


def test_lookup_is_stable_within_a_cell(grid: Grid):
    cell = grid.cell_id(28.6139, 77.2090)
    lat, lon = grid.cell_center(cell)
    # Nudging around the centre by a fraction of a cell must not change the cell.
    for dlat in (-grid.deg_lat / 4, 0, grid.deg_lat / 4):
        for dlon in (-grid.deg_lon / 4, 0, grid.deg_lon / 4):
            assert grid.cell_id(lat + dlat, lon + dlon) == cell


def test_adjacent_points_land_in_adjacent_cells(grid: Grid):
    cell = grid.cell_id(28.6139, 77.2090)
    lat, lon = grid.cell_center(cell)
    row, col = Grid.decode(cell)

    assert Grid.decode(grid.cell_id(lat + grid.deg_lat, lon)) == (row + 1, col)
    assert Grid.decode(grid.cell_id(lat, lon + grid.deg_lon)) == (row, col + 1)


def test_points_outside_delhi_return_none(grid: Grid):
    # Mumbai, and a point just past the northern edge.
    assert grid.cell_id(19.0760, 72.8777) is None
    assert grid.cell_id(DELHI_BBOX[3] + 1.0, 77.2090) is None


def test_corners_are_inside_the_grid(grid: Grid):
    min_lon, min_lat, max_lon, max_lat = DELHI_BBOX
    # The north-east corner is the one that trips off-by-one errors: without
    # clamping it indexes one row and column past the end of the lattice.
    assert grid.cell_id(max_lat, max_lon) is not None
    assert grid.cell_id(min_lat, min_lon) == Grid.encode(0, 0)


def test_manifest_roundtrip(tmp_path):
    spec = GridSpec(*DELHI_BBOX, cell_size_m=250.0)
    path = tmp_path / "grid.json"
    path.write_text(spec.to_json(), encoding="utf-8")

    loaded = GridSpec.from_file(path)
    assert loaded == spec
    # Halving the cell size should roughly quadruple the cell count.
    assert Grid(loaded).n_cells > Grid().n_cells * 3


def test_haversine_against_a_known_distance():
    # Connaught Place to Qutub Minar, ~13 km as the crow flies.
    d = haversine_m(28.6315, 77.2167, 28.5245, 77.1855)
    assert 12_000 < d < 14_000
