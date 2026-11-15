# 26. Imagen Image Generation

> Text-to-image generation on Vertex AI using Imagen 4 Fast — demonstrates default generation and key config options (aspect ratio, negative prompt, deterministic seed). **GCP Services:** Vertex AI, Imagen 4

## Architecture

```
Prompt text
    │
    ▼
Gen AI SDK Client (vertexai=True)
    │
    ├─── Step 1: Default Generation ──► 16:9 PNG  → output/imagen_default_1.png
    │           (imagen-4.0-fast-generate-001)
    │
    └─── Step 2: Config Variants
              ├── Portrait + negative_prompt ──► output/config_portrait_1.png
              └── Seeded deterministic        ──► output/config_seeded_1.png
```

## What this demonstrates

- Text-to-image generation with `imagen-4.0-fast-generate-001` via the unified Gen AI SDK
- Aspect ratio control: `16:9`, `9:16`, `1:1`, `4:3`, `3:4`
- `negative_prompt` to exclude unwanted visual elements (logos, text, people)
- `person_generation` policy: `dont_allow` | `allow_adult` | `allow_all`
- Deterministic/reproducible generation using `seed` + `add_watermark=False`
- Cost-aware design: fast tier (~$0.02/image) vs. standard/ultra tiers

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1

# Authenticate
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Generated images are saved to `output/`.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — runs default generation then config variant demos |
| `generator.py` | Core Imagen generation functions: `get_client`, `generate_image`, `save_images` |
| `config_options.py` | Config variant demos: portrait+negative_prompt, seeded deterministic |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | Enables Vertex AI APIs |

## Cost

| Run | Images | Estimated cost |
|-----|--------|---------------|
| Full `main.py` | 3 | ~$0.06 |
| Step 1 only | 1 | ~$0.02 |

Pricing: `imagen-4.0-fast-generate-001` at ~$0.02/image. Switch to `imagen-4.0-generate-001` or `-ultra` for maximum fidelity at higher cost.

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
