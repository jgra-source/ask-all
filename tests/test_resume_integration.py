"""
Tests for resume generation integration:
- Company detection & text cleaning (guarding against em dashes)
- Resume generation API permissions and safety
- Proper PDF and selection JSON output creation
"""
import json
import sys
import pytest
from pathlib import Path
import integrations.resume as resume
from integrations.resume import (
    ResumeGenerationError,
    clean_text,
    detect_company,
    extract_selection_from_text,
    generate_resume_for_run,
)


def test_resume_bridge_does_not_pollute_module_search_path():
    assert str(resume.ASSETS_DIR) not in sys.path


def test_clean_text_normalizes_em_and_en_dashes():
    raw = "AI Solutions Engineer — RAG Pipelines – Systems"
    cleaned = clean_text(raw)
    assert "—" not in cleaned
    assert "–" not in cleaned
    assert cleaned == "AI Solutions Engineer - RAG Pipelines - Systems"


def test_detect_company():
    jd1 = "Life at CAKE.com\nWe are looking for..."
    assert detect_company(jd1) == "CAKE.com"

    jd2 = "Company: Stripe, Inc.\nJob Title:..."
    assert detect_company(jd2) == "Stripe, Inc"

    jd3 = "About Acme Corp\nWe build widgets..."
    assert detect_company(jd3) == "Acme Corp"


def test_extract_selection_from_text():
    sample = """
Here is my recommendation:
```json
{
  "company": "CAKE.com",
  "role_line": "AI Systems Engineer",
  "skills": ["sk-ai", "sk-automation"],
  "projects": ["prj-handler", "prj-chief-of-staff"],
  "experience": {"exp-cnx": ["cnx-translate"]}
}
```
Good luck!
"""
    sel = extract_selection_from_text(sample)
    assert sel is not None
    assert sel["company"] == "CAKE.com"
    assert "sk-ai" in sel["skills"]
    assert sel["experience"]["exp-cnx"] == ["cnx-translate"]


def test_generate_resume_for_run_builds_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    run_dir = tmp_path / "20261111-120000-job-fit"
    run_dir.mkdir()
    jd_content = (
        "Life at AcmeTech\n"
        "We need a backend and AI automation engineer to design pipelines, LLM agents, and cloud systems.\n"
        "Requirements:\n"
        "Python, REST APIs, CI/CD, agentic workflows, automation."
    )
    (run_dir / "input.txt").write_text(jd_content, encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps({"task": "job-fit", "results": []}), encoding="utf-8")

    res = generate_resume_for_run(run_dir, company="AcmeTech", role_line="AI Solutions Engineer")
    assert res["ok"] is True
    assert res["selection_source"] == "deterministic"
    assert (run_dir / "resume.pdf").exists()
    assert (run_dir / "resume.selection.json").exists()
    assert (run_dir / "resume.pdf").stat().st_size > 1000
    assert (tmp_path / "Downloads" / res["downloads_filename"]).is_file()
    state = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert state["resume"]["selection_source"] == "deterministic"


def valid_model_selection(factbase):
    safe = lambda block: not (isinstance(block.get("note"), str) and block["note"].strip())
    return {
        "company": "Model Company",
        "role_line": "Model Role",
        "summary_facts": [next(block["id"] for block in factbase["summary_facts"] if safe(block))],
        "summary": "This model-written summary must be discarded.",
        "skills": [block["id"] for block in factbase["skills"] if safe(block)][:3],
        "projects": [next(block["id"] for block in factbase["projects"] if safe(block))],
        "experience": {
            job["id"]: [next(block["id"] for block in job["bullets"] if safe(block))]
            for job in factbase["experience"]
        },
        "overrides": {"not-a-block": "This override must be discarded."},
    }


def test_rejected_model_selection_uses_deterministic_builder(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    run_dir = tmp_path / "20261111-120000-job-fit"
    run_dir.mkdir()
    (run_dir / "input.txt").write_text("Python automation and cloud systems", encoding="utf-8")
    factbase = resume.load_factbase()
    summary_fact = next(
        block for block in factbase["summary_facts"]
        if not (isinstance(block.get("note"), str) and block["note"].strip())
    )
    invalid_selection = valid_model_selection(factbase)
    invalid_selection["skills"][0] = "missing-skill"
    (run_dir / "results.json").write_text(json.dumps({
        "task": "job-fit",
        "results": [{
            "status": "ok",
            "text": "```json\n" + json.dumps(invalid_selection) + "\n```",
        }],
    }), encoding="utf-8")

    def fake_tailor(jd, company, role_line, out=None):
        pdf_path = Path(out)
        pdf_path.write_bytes(b"test pdf")
        pdf_path.with_suffix(".selection.json").write_text(json.dumps({
            "company": company, "role_line": role_line, "skills": [], "projects": [],
        }), encoding="utf-8")
        return pdf_path, {}, {"pages": 1}

    monkeypatch.setattr(resume, "tailor_generate", fake_tailor)
    result = generate_resume_for_run(
        run_dir, company="Chosen Co", role_line="Truthful Role"
    )

    assert result["selection_source"] == "deterministic"
    assert result["selection"]["company"] == "Chosen Co"
    assert result["selection"]["role_line"] == "Truthful Role"
    assert any("model selection was rejected" in warning for warning in result["warnings"])
    assert (run_dir / "resume.pdf").read_bytes() == b"test pdf"


def test_model_selection_build_uses_explicit_company_and_role_line(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    run_dir = tmp_path / "20261111-120000-job-fit"
    run_dir.mkdir()
    (run_dir / "input.txt").write_text("Python automation and cloud systems", encoding="utf-8")
    factbase = resume.load_factbase()
    selection = valid_model_selection(factbase)
    summary_fact = next(
        block for block in factbase["summary_facts"]
        if block["id"] == selection["summary_facts"][0]
    )
    invalid_selection = {
        **selection,
        "skills": ["missing-skill", *selection["skills"][1:]],
    }
    (run_dir / "results.json").write_text(json.dumps({
        "task": "job-fit",
        "results": [
            {"status": "ok", "text": f"```json\n{json.dumps(invalid_selection)}\n```"},
            {"status": "ok", "text": f"```json\n{json.dumps(selection)}\n```"},
        ],
    }), encoding="utf-8")
    built = {}

    def fake_build(selection_path, out_arg):
        built.update(json.loads(Path(selection_path).read_text(encoding="utf-8")))
        pdf_path = Path(out_arg)
        pdf_path.write_bytes(b"test pdf")
        pdf_path.with_suffix(".selection.json").write_text(
            json.dumps(built), encoding="utf-8"
        )
        return pdf_path, {"pages": 1}

    monkeypatch.setattr(resume, "build", fake_build)
    result = generate_resume_for_run(
        run_dir, company="Chosen Co", role_line="Truthful Role"
    )

    assert result["selection_source"] == "model"
    assert built["company"] == "Chosen Co"
    assert built["role_line"] == "Truthful Role"
    assert built["summary"] == summary_fact["text"].replace("—", "-")
    assert built["overrides"] == {}
    assert built["summary_facts"] == [summary_fact["id"]]
    assert result["selection"]["projects"] == selection["projects"]
    assert any("model selection was rejected" in warning for warning in result["warnings"])


def test_model_selection_cannot_use_note_bearing_blocks():
    factbase = resume.load_factbase()
    selection = valid_model_selection(factbase)
    restricted_project = next(
        block for block in factbase["projects"]
        if isinstance(block.get("note"), str) and block["note"].strip()
    )
    selection["projects"] = [restricted_project["id"]]
    with pytest.raises(resume.FactBaseError, match="restriction note"):
        resume._validate_and_canonicalize_selection(selection, factbase)


def test_build_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    run_dir = tmp_path / "20261111-120000-job-fit"
    run_dir.mkdir()
    (run_dir / "input.txt").write_text("Python automation and cloud systems", encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps({
        "task": "job-fit", "results": [],
    }), encoding="utf-8")

    def fail_build(*args, **kwargs):
        raise ValueError("no safe fact blocks matched")

    monkeypatch.setattr(resume, "tailor_generate", fail_build)
    with pytest.raises(ResumeGenerationError, match="no safe fact blocks matched"):
        generate_resume_for_run(run_dir)
    assert not (run_dir / "resume.pdf").exists()


def test_resume_generation_rejects_non_job_fit_run(tmp_path):
    run_dir = tmp_path / "20261111-120000-job-post"
    run_dir.mkdir()
    (run_dir / "input.txt").write_text("A freelance project", encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps({
        "task": "job-post", "results": [],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="only for job-fit"):
        generate_resume_for_run(run_dir)
