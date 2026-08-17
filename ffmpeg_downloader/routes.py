from __future__ import annotations

from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    current_app,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

from . import probe as _probe
from . import ytdlp as _ytdlp
from .ffmpeg_command import UnsupportedCodecError, UnsupportedSchemeError
from .filesystem import InvalidNameError, PathTraversalError, RootedFS
from .jobs import JobSpec


def _fs() -> RootedFS:
    return current_app.extensions["fs"]


def register(app: Flask) -> None:
    from . import __version__ as _ver

    @app.get("/")
    def index():
        return render_template("index.html", version=_ver)

    @app.get("/api/browse")
    def browse():
        path = request.args.get("path", "")
        try:
            return jsonify(_fs().browse(path))
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400
        except FileNotFoundError:
            return jsonify({"error": "path not found"}), 404

    @app.post("/api/mkdir")
    def mkdir():
        if not request.is_json:
            return jsonify({"error": "json body required"}), 400
        body = request.get_json(silent=True) or {}
        path = body.get("path", "")
        name = body.get("name", "")
        try:
            created = _fs().mkdir(path, name)
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400
        except InvalidNameError as e:
            return jsonify({"error": str(e)}), 400
        except FileNotFoundError:
            return jsonify({"error": "path not found"}), 404
        return jsonify({"path": created})

    @app.get("/api/validate")
    def validate():
        path = request.args.get("path", "")
        try:
            return jsonify(_fs().validate(path))
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/autocomplete")
    def autocomplete():
        prefix = request.args.get("prefix", "")
        try:
            matches = _fs().autocomplete(prefix)
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"matches": matches})

    @app.get("/api/search")
    def search():
        q = request.args.get("q", "")
        limit_raw = request.args.get("limit", "50")
        try:
            limit = int(limit_raw)
        except ValueError:
            limit = 50
        cfg = current_app.extensions["config"]
        limit = min(limit, cfg.search_result_limit)
        return jsonify(_fs().search(q, limit=limit))

    @app.post("/api/probe")
    def probe():
        if not request.is_json:
            return jsonify({"error": "json body required"}), 400
        body = request.get_json(silent=True) or {}
        url = body.get("url", "")
        if not url:
            return jsonify({"error": "url is required"}), 400
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            return jsonify({"error": "only http(s) URLs are allowed"}), 400

        cfg = current_app.extensions["config"]
        if not _ytdlp.looks_like_direct_media(url):
            ext = _ytdlp.probe_extractor(
                url,
                ytdlp_bin=cfg.ytdlp_bin,
                js_runtime=cfg.ytdlp_js_runtime,
            )
            if ext.get("type") == "extractor":
                return jsonify(ext)
            if ext.get("type") == "unsupported":
                return jsonify(ext)
            # type == "none" → fall through to HLS/direct probe

        result = _probe.probe_url(url)
        return jsonify(
            {
                "type": result.type,
                "variants": result.variants,
                "audio_tracks": result.audio_tracks,
                "subtitle_tracks": result.subtitle_tracks,
                "duration_seconds": result.duration_seconds,
                "message": result.message,
            }
        )

    @app.post("/api/downloads")
    def create_download():
        if not request.is_json:
            return jsonify({"error": "json body required"}), 400
        body = request.get_json(silent=True) or {}
        try:
            url = body["url"]
            filename = body["filename"]
            extension = body["extension"]
            codec = body["codec"]
            output_folder = body.get("output_folder", "")
        except KeyError as e:
            return jsonify({"error": f"missing field: {e.args[0]}"}), 400

        audio_urls = body.get("audio_urls") or []
        subtitle_urls = body.get("subtitle_urls") or []
        if not isinstance(audio_urls, list) or not isinstance(subtitle_urls, list):
            return jsonify({"error": "audio_urls and subtitle_urls must be arrays"}), 400

        backend = body.get("backend") or "ffmpeg"
        if backend not in ("ffmpeg", "ytdlp"):
            return jsonify({"error": "backend must be ffmpeg or ytdlp"}), 400

        # Scheme guard for every URL the server will hand to the downloader.
        urls_to_check = [url]
        if backend == "ffmpeg":
            urls_to_check.extend(
                [body.get("selected_variant_url") or "", *audio_urls, *subtitle_urls]
            )
        for u in urls_to_check:
            if not u:
                continue
            scheme = urlparse(u).scheme.lower()
            if scheme not in ("http", "https"):
                return jsonify({"error": f"unsupported scheme: {scheme}"}), 400

        try:
            _fs().safe_path(output_folder or "")  # traversal pre-check
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400

        jm = current_app.extensions["jobs"]
        spec = JobSpec(
            url=url,
            selected_variant_url=body.get("selected_variant_url") or None,
            selected_variant_label=body.get("selected_variant_label") or None,
            filename=filename,
            extension=extension,
            codec=codec,
            output_folder=output_folder,
            audio_urls=audio_urls,
            subtitle_urls=subtitle_urls,
            backend=backend,
            format_selector=body.get("format_selector") or None,
            format_label=body.get("format_label") or None,
        )
        try:
            job = jm.submit(spec)
        except (UnsupportedCodecError, UnsupportedSchemeError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(job), 201

    @app.get("/api/downloads")
    def list_downloads():
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            limit = 50
        limit = min(max(limit, 1), 200)
        db = current_app.extensions["db"]
        return jsonify(db.list_jobs(limit=limit))

    @app.get("/api/downloads/<job_id>")
    def get_download(job_id: str):
        db = current_app.extensions["db"]
        job = db.get_job(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        return jsonify(job)

    @app.delete("/api/downloads/<job_id>")
    def delete_download(job_id: str):
        db = current_app.extensions["db"]
        jm = current_app.extensions["jobs"]
        job = db.get_job(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        if job["status"] in ("queued", "running"):
            jm.cancel(job_id)
            return jsonify({"ok": True})
        db.delete_job(job_id)
        return jsonify({"ok": True})

    # ---- SSE ----
    import json as _json
    import queue as _queue

    TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

    def _format_event(event: str, data) -> str:
        return f"event: {event}\ndata: {_json.dumps(data)}\n\n"

    @app.get("/api/downloads/<job_id>/events")
    def stream_job_events(job_id: str):
        db = current_app.extensions["db"]
        jm = current_app.extensions["jobs"]
        job = db.get_job(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404

        def gen():
            q = jm.subscribe(job_id)
            try:
                yield _format_event("status", job)
                if job["status"] in TERMINAL_STATUSES:
                    return
                while True:
                    try:
                        ev = q.get(timeout=30)
                    except _queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield _format_event(ev["event"], ev["data"])
                    if ev["event"] == "status" and ev["data"].get("status") in TERMINAL_STATUSES:
                        return
            finally:
                jm.unsubscribe(job_id, q)

        return Response(stream_with_context(gen()), mimetype="text/event-stream")

    @app.get("/api/events")
    def stream_global_events():
        jm = current_app.extensions["jobs"]

        def gen():
            q = jm.subscribe_global()
            try:
                # Emit an initial keepalive so clients (and the WSGI test
                # client) see headers/body immediately rather than blocking on
                # the first queue.get().
                yield ": keepalive\n\n"
                while True:
                    try:
                        ev = q.get(timeout=30)
                    except _queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield _format_event(ev["event"], ev["data"])
            finally:
                jm.unsubscribe_global(q)

        return Response(stream_with_context(gen()), mimetype="text/event-stream")
