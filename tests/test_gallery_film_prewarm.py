from services.gallery_film_prewarm import previews_base_from_artifacts


def test_previews_base_from_analysis_json_path(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    json_path = previews / "analysis_results.json"
    json_path.write_text("[]", encoding="utf-8")
    assert previews_base_from_artifacts(str(json_path)) == str(previews.resolve())


def test_previews_base_from_source_dir_with_previews_subfolder(tmp_path):
    root = tmp_path / "session"
    previews = root / "Previews"
    previews.mkdir(parents=True)
    (previews / "analysis_results.json").write_text("[]", encoding="utf-8")
    assert previews_base_from_artifacts(None, str(root)) == str(previews.resolve())
