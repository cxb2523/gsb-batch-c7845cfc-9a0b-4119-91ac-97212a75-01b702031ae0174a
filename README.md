# Google Cloud Data Engineering Hub — 55 GCP Data Engineering Projects in Python

**55 production-grade, live-tested Google Cloud (GCP) data engineering projects in Python** — BigQuery, Dataflow / Apache Beam, Cloud Composer / Airflow, Pub/Sub, Dataproc Serverless PySpark, Cloud Storage, Vertex AI, Gemini, RAG, Document AI and MCP. Real code, architecture diagrams and `deploy.sh` for every project — ideal for GCP data engineer portfolio projects, Professional Data Engineer exam prep and interview practice.

<div align="center">

[![Typing SVG](https://readme-typing-svg.demolab.com?font=Fira+Code&weight=700&size=32&duration=3000&pause=1000&color=4285F4&center=true&vCenter=true&width=900&lines=☁️+Google+Cloud+Data+Engineering+Hub;55+Production-Grade+GCP+Projects;BigQuery+%7C+Dataflow+%7C+Beam+%7C+Composer;Built+%26+Battle-Tested+by+TechTrapture)](https://techtrapture.com)

<br/>

![BigQuery](https://img.shields.io/badge/BigQuery-669DF6?style=for-the-badge&logo=googlebigquery&logoColor=white)
![Dataflow](https://img.shields.io/badge/Dataflow-4285F4?style=for-the-badge&logo=googlecloud&logoColor=white)
![Apache Beam](https://img.shields.io/badge/Apache%20Beam-E25A1C?style=for-the-badge&logo=apachebeam&logoColor=white)
![Composer](https://img.shields.io/badge/Cloud%20Composer-017CEE?style=for-the-badge&logo=apacheairflow&logoColor=white)
![Pub/Sub](https://img.shields.io/badge/Pub%2FSub-EA4335?style=for-the-badge&logo=googlecloud&logoColor=white)
![Dataproc](https://img.shields.io/badge/Dataproc-FF6D00?style=for-the-badge&logo=apachespark&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white)
![Vertex AI](https://img.shields.io/badge/Vertex%20AI-34A853?style=for-the-badge&logo=googlecloud&logoColor=white)

<br/>

[![Projects](https://img.shields.io/badge/Projects-55-brightgreen?style=for-the-badge)](https://github.com/vishal-bulbule/google-cloud-data-engineering-hub)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue?style=for-the-badge)](LICENSE)
[![Live Tested](https://img.shields.io/badge/Live--Tested%20on%20GCP-✓-success?style=for-the-badge)](https://cloud.google.com/)

<br/>

<a href="https://www.linkedin.com/in/vishal-bulbule/"><img src="https://img.shields.io/badge/LinkedIn-Vishal%20Bulbule-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white"/></a>
<a href="https://www.youtube.com/@techtrapture?sub_confirmation=1"><img src="https://img.shields.io/badge/YouTube-TechTrapture-FF0000?style=for-the-badge&logo=youtube&logoColor=white"/></a>
<a href="https://techtrapture.com"><img src="https://img.shields.io/badge/Website-techtrapture.com-2563EB?style=for-the-badge&logo=googlechrome&logoColor=white"/></a>
<a href="https://academy.techtrapture.com"><img src="https://img.shields.io/badge/Academy-Enroll-34A853?style=for-the-badge&logo=googleclassroom&logoColor=white"/></a>
<a href="mailto:vishal.bulbule@techtrapture.com"><img src="https://img.shields.io/badge/Enterprise-Enquiry-EA4335?style=for-the-badge&logo=gmail&logoColor=white"/></a>

</div>

---

## What is this?

A free, open-source collection of **end-to-end Google Cloud data engineering projects** — real code, real architecture, tested against live GCP environments.

Unlike snippet libraries, every project is a complete runnable system:
- Modular Python code (not scripts)
- ASCII architecture diagram
- Sample data fixture
- `deploy.sh` with step-by-step GCP setup

Built by **[Vishal Bulbule](https://www.linkedin.com/in/vishal-bulbule/)** — Google Developer Expert, 12x GCP Certified, Founder @ [TechTrapture](https://techtrapture.com).

---

## 🎬 Data Engineering on YouTube

<div align="center">

### 📺 Full Playlist → Data Engineering on Google Cloud

<a href="https://www.youtube.com/playlist?list=PLLrA_pU9-Gz18_NlZrVA8GVR39FUo-Fnk">
<img src="https://img.shields.io/badge/▶%20Watch%20Full%20Playlist%20%E2%80%94%20Data%20Engineering%20on%20GCP-FF0000?style=for-the-badge&logo=youtube&logoColor=white" height="50"/>
</a>

<br/><br/>

[![Production ELT Pipeline | IMDB Data Project](https://ytcards.demolab.com/?id=2rBNLd9ucBs&title=Production+ELT+Pipeline+%7C+IMDB+Data+Project&lang=en&background_color=%230A1F44&title_color=%23F2F3F8&stats_color=%2394A3B8&max_title_lines=1&width=250&border_radius=8 "Production ELT Pipeline | IMDB Data Project")](https://www.youtube.com/watch?v=2rBNLd9ucBs)
[![BigQuery MERGE | UPSERT Explained](https://ytcards.demolab.com/?id=M2VTwbqdoI8&title=BigQuery+MERGE+%7C+UPSERT+Explained&lang=en&background_color=%230A1F44&title_color=%23F2F3F8&stats_color=%2394A3B8&max_title_lines=1&width=250&border_radius=8 "BigQuery MERGE | UPSERT Explained")](https://www.youtube.com/watch?v=M2VTwbqdoI8)
[![First Airflow DAG in Cloud Composer](https://ytcards.demolab.com/?id=9rX3YEcNvXc&title=First+Airflow+DAG+in+Cloud+Composer&lang=en&background_color=%230A1F44&title_color=%23F2F3F8&stats_color=%2394A3B8&max_title_lines=1&width=250&border_radius=8 "First Airflow DAG in Cloud Composer")](https://www.youtube.com/watch?v=9rX3YEcNvXc)
[![Streaming Pipeline | Pub/Sub to BigQuery](https://ytcards.demolab.com/?id=Unbx59eHL5I&title=Streaming+Pipeline+%7C+Pub%2FSub+to+BigQuery&lang=en&background_color=%230A1F44&title_color=%23F2F3F8&stats_color=%2394A3B8&max_title_lines=1&width=250&border_radius=8 "Streaming Pipeline | Pub/Sub to BigQuery")](https://www.youtube.com/watch?v=Unbx59eHL5I)

➡️ **[View all Data Engineering videos →](https://www.youtube.com/playlist?list=PLLrA_pU9-Gz18_NlZrVA8GVR39FUo-Fnk)**

</div>

---

## 🛠️ Stack Covered

<div align="center">

### Data Processing & Orchestration
<img src="https://img.shields.io/badge/BigQuery-669DF6?style=for-the-badge&logo=googlebigquery&logoColor=white"/>
<img src="https://img.shields.io/badge/Cloud%20Dataflow-4285F4?style=for-the-badge&logo=googlecloud&logoColor=white"/>
<img src="https://img.shields.io/badge/Apache%20Beam-E25A1C?style=for-the-badge&logo=apachebeam&logoColor=white"/>
<img src="https://img.shields.io/badge/Cloud%20Composer-017CEE?style=for-the-badge&logo=apacheairflow&logoColor=white"/>
<img src="https://img.shields.io/badge/Apache%20Airflow-017CEE?style=for-the-badge&logo=apacheairflow&logoColor=white"/>
<img src="https://img.shields.io/badge/Pub%2FSub-EA4335?style=for-the-badge&logo=googlecloud&logoColor=white"/>
<img src="https://img.shields.io/badge/Dataproc%20Serverless-FF6D00?style=for-the-badge&logo=apachespark&logoColor=white"/>

### Storage & Formats
<img src="https://img.shields.io/badge/Cloud%20Storage-AECBFA?style=for-the-badge&logo=googlecloud&logoColor=black"/>
<img src="https://img.shields.io/badge/Apache%20Parquet-50ABF1?style=for-the-badge"/>
<img src="https://img.shields.io/badge/Apache%20Avro-E25A1C?style=for-the-badge"/>
<img src="https://img.shields.io/badge/JSON%2FJSONL-000000?style=for-the-badge"/>
<img src="https://img.shields.io/badge/CSV%2FPandas-150458?style=for-the-badge&logo=pandas&logoColor=white"/>

### AI & ML on GCP
<img src="https://img.shields.io/badge/Gemini-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white"/>
<img src="https://img.shields.io/badge/Vertex%20AI-34A853?style=for-the-badge&logo=googlecloud&logoColor=white"/>
<img src="https://img.shields.io/badge/Document%20AI-4285F4?style=for-the-badge&logo=googlecloud&logoColor=white"/>
<img src="https://img.shields.io/badge/Embeddings-FF6D00?style=for-the-badge&logo=googlecloud&logoColor=white"/>
<img src="https://img.shields.io/badge/RAG%20Pipelines-8E75B2?style=for-the-badge"/>
<img src="https://img.shields.io/badge/MCP-0B0F19?style=for-the-badge"/>

### Ops & Governance
<img src="https://img.shields.io/badge/Cloud%20Asset%20Inventory-34A853?style=for-the-badge&logo=googlecloud&logoColor=white"/>
<img src="https://img.shields.io/badge/IAM%20%26%20Security-EA4335?style=for-the-badge&logo=googlecloud&logoColor=white"/>
<img src="https://img.shields.io/badge/GCP%20FinOps-FBBC05?style=for-the-badge&logo=googlecloud&logoColor=black"/>
<img src="https://img.shields.io/badge/Recommender%20API-4285F4?style=for-the-badge&logo=googlecloud&logoColor=white"/>

</div>

---

## ⚡ Quick Start

```bash
git clone https://github.com/vishal-bulbule/google-cloud-data-engineering-hub.git
cd google-cloud-data-engineering-hub

# Pick any project
cd 01-bq-csv-ingestion-pipeline

# Set up Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure your GCP project
export GOOGLE_CLOUD_PROJECT=your-project-id   # ← replace
export GOOGLE_CLOUD_LOCATION=us-central1

# Run GCP setup (enable APIs, create resources)
chmod +x deploy.sh && ./deploy.sh

# Run the project
python main.py
```

---

## 📁 Projects

### BigQuery

| # | Project | What it builds |
|---|---------|----------------|
| 01 | [bq-csv-ingestion-pipeline](01-bq-csv-ingestion-pipeline/) | Load CSV from local disk, GCS, and multi-format (JSON/Parquet) |
| 02 | [bq-analytics-reporting](02-bq-analytics-reporting/) | Queries, DataFrames, dataset/table metadata listing |
| 03 | [bq-upsert-merge-pattern](03-bq-upsert-merge-pattern/) | Partitioned + clustered tables, MERGE upsert, snapshots |
| 04 | [bq-finops-cost-visibility](04-bq-finops-cost-visibility/) | Dry-run cost estimates, INFORMATION_SCHEMA FinOps, scheduled queries |
| 05 | [bq-ml-train-predict](05-bq-ml-train-predict/) | Train + predict with BigQuery ML (BQML logistic regression) |
| 06 | [bq-external-tables-ai](06-bq-external-tables-ai/) | External tables over GCS, BigQuery AI embeddings via remote model |
| 07 | [bq-streaming-inserts](07-bq-streaming-inserts/) | Real-time streaming inserts with deduplication |
| 55 | [bq-scd-slowly-changing-dimensions](55-bq-scd-slowly-changing-dimensions/) | SCD Type 1, 2 & 3 — MERGE SQLs, hash-based change detection, point-in-time queries, Dataform/dbt/Airflow integration notes |

### Cloud Storage

| # | Project | What it builds |
|---|---------|----------------|
| 08 | [gcs-file-management](08-gcs-file-management/) | Full object lifecycle — create, upload, list, copy, rename, delete |
| 09 | [gcs-signed-urls-lifecycle](09-gcs-signed-urls-lifecycle/) | Signed URL v4, lifecycle rules for storage cost optimization |
| 10 | [gcs-high-throughput-transfer](10-gcs-high-throughput-transfer/) | Parallel transfer manager, in-memory streaming, pandas over `gs://` |

### Data Engineering

| # | Project | What it builds |
|---|---------|----------------|
| 29 | [data-format-processing](29-data-format-processing/) | CSV, JSON/JSONL, pandas transform, Parquet read/write with benchmarks |
| 30 | [bigquery-etl-pipeline](30-bigquery-etl-pipeline/) | End-to-end ETL: extract → transform → BQ load → incremental MERGE |
| 31 | [pubsub-streaming-pipeline](31-pubsub-streaming-pipeline/) | Pub/Sub publish + pull subscribe with flow control |
| 32 | [dataflow-template-launch](32-dataflow-template-launch/) | Dataflow managed template job launch (DRY_RUN safe) |
| 33 | [dataproc-serverless-pyspark](33-dataproc-serverless-pyspark/) | Dataproc Serverless PySpark batch job submission |
| 34 | [data-quality-validation-framework](34-data-quality-validation-framework/) | Null, type, range, uniqueness, referential integrity checks |

### Cloud Composer / Airflow

| # | Project | What it builds |
|---|---------|----------------|
| 51 | [cloud-composer-dag-basics](51-cloud-composer-dag-basics/) | First DAG, operators, XComs, BranchPythonOperator, TaskGroups, retries |
| 52 | [composer-bigquery-pipeline](52-composer-bigquery-pipeline/) | GCS sensor → BQ load → SQL transform → validation → GCS export |
| 53 | [composer-gcs-sensor-trigger](53-composer-gcs-sensor-trigger/) | Event-driven pipeline on file drop, archive pattern, Pub/Sub notify |
| 54 | [composer-dataflow-orchestration](54-composer-dataflow-orchestration/) | DAG launching Dataflow Flex Template → monitoring → BQ validation |

### Apache Beam / Dataflow

| # | Project | What it builds |
|---|---------|----------------|
| 47 | [beam-data-transformation](47-beam-data-transformation/) | WordCount + CSV transform pipelines (DirectRunner) |
| 48 | [beam-csv-to-bigquery-load](48-beam-csv-to-bigquery-load/) | CSV → BigQuery Beam pipeline (live-tested load) |
| 49 | [beam-advanced-patterns](49-beam-advanced-patterns/) | Branching, side outputs, dead-letter, event-time windowing |
| 50 | [beam-dataflow-production-submit](50-beam-dataflow-production-submit/) | Submit Beam job to Dataflow with cost controls |

### Gemini

| # | Project | What it builds |
|---|---------|----------------|
| 11 | [gemini-text-generation-basics](11-gemini-text-generation-basics/) | First API call, streaming, token counting, generation config |
| 12 | [gemini-multimodal-analysis](12-gemini-multimodal-analysis/) | Image, PDF, video/audio understanding |
| 13 | [gemini-function-calling-tools](13-gemini-function-calling-tools/) | Function calling, structured JSON output, multi-turn chat |
| 14 | [gemini-enterprise-features](14-gemini-enterprise-features/) | Grounding, context caching, batch prediction (50% cost) |
| 15 | [gemini-safety-thinking-budget](15-gemini-safety-thinking-budget/) | Safety settings, thinking budget (Gemini 2.5) |

### Embeddings & RAG

| # | Project | What it builds |
|---|---------|----------------|
| 16 | [text-embeddings-fundamentals](16-text-embeddings-fundamentals/) | Batch embeddings, task types, output dimensionality |
| 17 | [semantic-search-engine](17-semantic-search-engine/) | Cosine similarity, product catalog semantic search |
| 18 | [text-clustering-multimodal-embeddings](18-text-clustering-multimodal-embeddings/) | Text clustering, multimodal embeddings, BQ vector storage |
| 19 | [rag-quickstart-in-memory](19-rag-quickstart-in-memory/) | Minimal in-memory RAG, 4 chunking strategies |
| 20 | [pdf-rag-with-citations](20-pdf-rag-with-citations/) | PDF RAG with source citations + hallucination detection |
| 21 | [vertex-rag-engine-faiss](21-vertex-rag-engine-faiss/) | Vertex RAG Engine, FAISS, BigQuery `VECTOR_SEARCH` |
| 22 | [hybrid-rag-rerank](22-hybrid-rag-rerank/) | BM25 + dense vector hybrid search, cross-encoder reranking |

### Document AI

| # | Project | What it builds |
|---|---------|----------------|
| 23 | [document-ocr-pipeline](23-document-ocr-pipeline/) | OCR processor creation, PDF text extraction |
| 24 | [form-invoice-extraction](24-form-invoice-extraction/) | Form key-value extraction, invoice entity parsing |
| 25 | [batch-document-processing](25-batch-document-processing/) | Batch processing over GCS, layout-aware chunking for RAG |

### Imagen & Vision

| # | Project | What it builds |
|---|---------|----------------|
| 26 | [imagen-image-generation](26-imagen-image-generation/) | Imagen 4 Fast, aspect ratio, seed, watermark config |
| 27 | [gemini-image-creation-editing](27-gemini-image-creation-editing/) | Gemini native image generation, editing, analysis |
| 28 | [vision-api-object-detection](28-vision-api-object-detection/) | Bounding boxes, Vision API labels + OCR |

### Resource Management & FinOps

| # | Project | What it builds |
|---|---------|----------------|
| 35 | [gcp-resource-inventory](35-gcp-resource-inventory/) | All projects, Asset Inventory sweep, org-level resource search |
| 36 | [iam-security-audit](36-iam-security-audit/) | Enabled API audit, IAM policy review, SA key age |
| 37 | [compute-data-estate-inventory](37-compute-data-estate-inventory/) | Compute, BQ/GCS/Bigtable data estate inventory |
| 38 | [billing-cost-recommendations](38-billing-cost-recommendations/) | Billing accounts, Recommender API cost savings |

### Vertex AI Platform

| # | Project | What it builds |
|---|---------|----------------|
| 39 | [vertex-model-catalog-explorer](39-vertex-model-catalog-explorer/) | List and filter all publisher models |
| 40 | [vector-search-index-pipeline](40-vector-search-index-pipeline/) | Vector Search index, upsert, k-NN query |
| 41 | [vertex-ml-training-evaluation](41-vertex-ml-training-evaluation/) | Batch prediction, fine-tuning, GenAI evaluation |
| 42 | [vertex-pipelines-endpoint-finops](42-vertex-pipelines-endpoint-finops/) | Vertex Pipelines, endpoint FinOps audit |

### MCP (Model Context Protocol)

| # | Project | What it builds |
|---|---------|----------------|
| 43 | [connect-gcp-remote-mcp-servers](43-connect-gcp-remote-mcp-servers/) | GCP native MCP servers (Developer Knowledge + BigQuery) |
| 44 | [gemini-adk-with-mcp-tools](44-gemini-adk-with-mcp-tools/) | Gemini + MCP, ADK agent with MCPToolset |
| 45 | [build-custom-mcp-server](45-build-custom-mcp-server/) | FastMCP server with BQ + GCS tools |
| 46 | [deploy-mcp-server-cloud-run](46-deploy-mcp-server-cloud-run/) | Deploy MCP server to Cloud Run, MCP Toolbox |

---

## 🗂️ Project Structure

Every project follows the same layout so you always know where to look:

```
<project-name>/
├── README.md          ← architecture diagram (ASCII) + setup + run instructions
├── main.py            ← entry point — imports and orchestrates modules
├── <module>.py        ← focused logic (ingest / transform / load / etc.)
├── data/              ← sample fixture (CSV, JSON, text)
└── deploy.sh          ← gcloud commands with your-* placeholders
```

---

## 🔒 Cost Safety

Projects that create billable GCP infrastructure default to `DRY_RUN=True`:

| Service | Default | To run for real |
|---------|---------|-----------------|
| Dataflow jobs | `DRY_RUN=true` | `export DRY_RUN=false` |
| Dataproc Serverless | `DRY_RUN=true` | `export DRY_RUN=false` |
| Vertex AI fine-tuning | `DRY_RUN=true` | `export DRY_RUN=false` |
| Vector Search index | `DRY_RUN=true` | `export DRY_RUN=false` |
| Vertex Pipelines | `DRY_RUN=true` | `export DRY_RUN=false` |
| Cloud Composer env | Manual step | See `deploy.sh` |

---

## 🏢 Building Enterprise Data Platforms on GCP?

These projects are the starting point. If your team is designing production systems — data pipelines, agentic AI, streaming platforms, FinOps automation — **we architect and build them.**

**[TechTrapture](https://techtrapture.com)** works with enterprise teams on:

- **Data platform architecture** — BigQuery, Dataflow, Pub/Sub, Composer, event-driven at scale
- **Agentic AI on GCP** — ADK agents, MCP-connected tools, multi-agent orchestration
- **Gemini Enterprise & Vertex AI** — RAG, fine-tuning, evaluation, production deployments
- **GCP FinOps** — cost visibility, BigQuery optimization, resource governance
- **Cloud architecture** — Cloud Run, GKE, migration, greenfield builds

<div align="center">
<a href="mailto:vishal.bulbule@techtrapture.com"><img src="https://img.shields.io/badge/📩%20Start%20a%20Conversation-vishal.bulbule%40techtrapture.com-EA4335?style=for-the-badge"/></a>
</div>

---

## 👤 About the Author

<div align="center">

<a href="https://www.linkedin.com/in/vishal-bulbule/"><img src="https://img.shields.io/badge/Vishal%20Bulbule-LinkedIn-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white"/></a>
<a href="https://www.youtube.com/@techtrapture?sub_confirmation=1"><img src="https://img.shields.io/badge/TechTrapture-YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white"/></a>
<a href="https://vishalbulbule.medium.com/"><img src="https://img.shields.io/badge/Medium-Blog-000000?style=for-the-badge&logo=medium&logoColor=white"/></a>

</div>

**Vishal Bulbule** — Google Developer Expert (GDE), 12x GCP Certified, AWS Community Builder.
Founder @ [TechTrapture](https://techtrapture.com) — building production-grade AI + data systems on Google Cloud and teaching engineers from real deployments.

[![GDE](https://img.shields.io/badge/Google%20Developer%20Expert-4285F4?style=flat-square&logo=google&logoColor=white)]()
[![12x GCP](https://img.shields.io/badge/12x%20GCP%20Certified-4285F4?style=flat-square&logo=googlecloud&logoColor=white)]()
[![AWS Community Builder](https://img.shields.io/badge/AWS%20Community%20Builder-FF9900?style=flat-square&logo=amazonwebservices&logoColor=white)]()

---

## 🎓 TechTrapture Academy

Structured, hands-on courses on ADK, Gemini Enterprise, Vertex AI, BigQuery, and cloud architecture. Built for practitioners — cohort programs from real deployments, not toy demos.

<div align="center">
<a href="https://academy.techtrapture.com"><img src="https://img.shields.io/badge/Explore%20Courses-academy.techtrapture.com-34A853?style=for-the-badge&logo=googleclassroom&logoColor=white"/></a>
</div>

---

## 📝 Data Engineering Interview Questions

Preparing for a GCP Data Engineering interview? Curated real-world questions covering BigQuery, Dataflow, Pub/Sub, Composer, Dataproc, Beam, and more — built by the same team behind this repo.

<div align="center">
<a href="https://academy.techtrapture.com/qa"><img src="https://img.shields.io/badge/📝%20Data%20Engineering%20Interview%20Questions-academy.techtrapture.com%2Fqa-8E75B2?style=for-the-badge"/></a>
</div>

---

## Contributing

PRs welcome for:
- Bug fixes in existing projects
- New GCP services not yet covered
- Better placeholder defaults or setup instructions

Keep each project self-contained with its own `requirements.txt`.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

---

<div align="center">

Built with ☁️ by **[TechTrapture](https://techtrapture.com)**

[LinkedIn](https://www.linkedin.com/in/vishal-bulbule/) &nbsp;·&nbsp; [YouTube](https://www.youtube.com/@techtrapture) &nbsp;·&nbsp; [Academy](https://academy.techtrapture.com) &nbsp;·&nbsp; [Website](https://techtrapture.com) &nbsp;·&nbsp; [vishal.bulbule@techtrapture.com](mailto:vishal.bulbule@techtrapture.com)

<img src="https://komarev.com/ghpvc/?username=vishal-bulbule-gcp-data-eng-hub&label=Repo%20Views&color=1E3A8A&style=for-the-badge" alt="repo views"/>

*"Learn, work and share knowledge!"*

</div>
