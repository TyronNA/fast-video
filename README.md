# fast-video

AI video generation service powered by **Google Vertex AI (Veo)**.

## Project structure

```
fast-video/
  app/
    main.py               ← FastAPI app init + router registration
    api/
      routes.py           ← POST /generate-one
    core/
      config.py           ← Env var validation (pydantic-settings)
      logger.py           ← Logging setup
    services/
      vertex_service.py   ← All Vertex AI SDK logic
    schemas/
      video_schema.py     ← Request / Response Pydantic models
    models/
      task_model.py       ← VideoTask dataclass (future queue/DB extension)
    utils/
      file_utils.py       ← UUID filename + exports directory management
  exports/                ← Generated videos saved here
  requirements.txt
  Makefile
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | **Yes** | Path to your GCP service account JSON key |
| `GCP_PROJECT` | **Yes** | Your GCP project ID |
| `GCP_LOCATION` | No | Vertex AI region (default: `us-central1`) |

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
export GCP_PROJECT="your-project-id"
export GCP_LOCATION="us-central1"   # optional
```

## Quick start

```bash
make install   # create venv + install dependencies
make run       # start server on port 8000
make debug     # start server with --reload (hot-reload)
```

## API

### `POST /generate-one`

```bash
curl -X POST http://localhost:8000/generate-one \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A lone surfer riding a massive wave at sunset",
    "duration": 5
  }'
```

With optional image reference:

```bash
curl -X POST http://localhost:8000/generate-one \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Slow dramatic lighting over the scene",
    "image_reference_uri": "gs://your-bucket/reference.jpg",
    "duration": 8
  }'
```

### Success response

```json
{
  "status": "success",
  "file_path": "/abs/path/to/exports/<uuid>.mp4",
  "message": "Video generated successfully"
}
```

### Error codes

| HTTP | Cause |
|---|---|
| `400` | Invalid input or safety filter rejection |
| `500` | Missing `GCP_PROJECT` env var |
| `502` | Unexpected Vertex AI API error |
| `504` | Request timed out |

Interactive docs: `http://localhost:8000/docs`

## Output

Generated videos are saved to `./exports/` as `<uuid>.mp4`.
