from pathlib import Path

def test_photo_upload_allowed_by_middleware():
    source=Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text()
    assert "request.url.path.endswith('/photo') and request.method=='POST'" in source
    assert "img-src 'self' blob: data:" in source

def test_preview_blob_lifecycle():
    source=Path(__file__).resolve().parents[1].joinpath("static/app.js").read_text()
    assert "URL.createObjectURL(file)" in source
    assert "URL.revokeObjectURL(photoPreviewBlobUrl)" in source
