from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_both_release_workflows_use_shared_public_notes():
    notes = ROOT / ".github/release-notes.md"
    assert notes.is_file()
    assert "VB-Audio" in notes.read_text()

    for workflow in ("ci.yml", "publish-tested-candidates.yml"):
        content = (ROOT / ".github/workflows" / workflow).read_text()
        assert content.count("gh release create") == 1
        assert 'notes="$(cat .github/release-notes.md)"' in content
        assert '--notes "$notes"' in content
        assert "--generate-notes" in content
        assert "if [ ! -s .github/release-notes.md ]; then" in content


def test_manual_release_retains_build_provenance():
    content = (ROOT / ".github/workflows/publish-tested-candidates.yml").read_text()
    assert "Packages from [verified CI run $RUN_ID]" in content
    assert "actions/runs/$RUN_ID" in content
