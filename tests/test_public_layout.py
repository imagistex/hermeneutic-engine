"""Relocated entry points and the public builder's local dependencies; no model calls."""
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_board_registered_in_public_cli(capsys):
    from hermeneutic_engine import cli
    with pytest.raises(SystemExit) as result:
        cli.main(["board", "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "--notebooks" in help_text and "--no-call" in help_text


@pytest.mark.parametrize("name", ["board_page", "run_lenses", "candidates", "standout", "divergence_queue", "reconcile"])
def test_moved_view_entry_points(name, capsys):
    module = importlib.import_module(f"hermeneutic_engine.views.{name}")
    assert ROOT in Path(module.__file__).resolve().parents
    with pytest.raises(SystemExit) as result:
        module.main(["--help"])
    assert result.value.code == 0
    assert "--out" in capsys.readouterr().out


def test_site_builder_dependencies_and_terms(monkeypatch):
    folder = ROOT / "site-builder"
    # Match python site-builder/build_site.py, whose directory is on sys.path.
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location("public_site_builder", folder / "build_site.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    checks = builder.load_checks()
    assert checks.norm("Mar", "04") == "Mar4"
    assert checks.body_of("words — a name", "a name") == "words"
    copy_path = folder / "site.json"
    copy = json.loads(copy_path.read_text(encoding="utf-8"))
    terms = builder.find_file(copy["terms_file"], copy_path).resolve()
    assert ROOT in terms.parents and terms.is_file()
    assert builder.parse_terms(terms.read_text(encoding="utf-8"))
    for plate in copy.get("plates", {}).values():
        assert (folder / "images" / plate["file"]).is_file()
        assert (folder / "images/prompts" / plate["prompt"]).is_file()


def test_public_resources_exist():
    assert (ROOT / "prompts/board/board-round-1.md").is_file()
    assert isinstance(json.loads((ROOT / "prompts/board/board-round-1.priors.json").read_text()), list)
    assert json.loads((ROOT / "codebooks/codebook-v1d.source.json").read_text())
    assert (ROOT / "codebooks/wiki-loaded-terms-v0.txt").is_file()
    assert (ROOT / "scripts/morning.sh").is_file()
    assert "emma x lirette" in (ROOT / "LICENSE").read_text()
    assert (ROOT / "LICENSE").read_text().startswith("MIT License")
