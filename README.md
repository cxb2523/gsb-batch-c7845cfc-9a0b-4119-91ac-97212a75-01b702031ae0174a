<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:0F2027,100:2C5364&height=200&section=header&text=ETL%20Pipeline%20Framework&fontSize=40&fontColor=E6EEF3&animation=fadeIn&fontAlignY=40" />
</p>

<p align="center">
  ⚙️ Data Engineering &nbsp;|&nbsp; 🔄 ETL Pipelines &nbsp;|&nbsp; 📊 Data Quality & Monitoring
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square"/>
  <img src="https://img.shields.io/badge/Orchestration-Apache%20Airflow-orange?style=flat-square"/>
  <img src="https://img.shields.io/badge/Data-Pandas%20%7C%20Polars-lightgrey?style=flat-square"/>
  <img src="https://img.shields.io/badge/Quality-Great%20Expectations-green?style=flat-square"/>
  <img src="https://img.shields.io/badge/DB-SQLAlchemy-yellow?style=flat-square"/>
  <img src="https://img.shields.io/badge/Deployment-Docker-blue?style=flat-square"/>
</p>

---

# ⚙️ ETL Pipeline Framework

A **modular, production-grade ETL framework** for **data extraction, transformation, and loading**, enhanced with **data quality monitoring, lineage tracking, and automated orchestration**.

This project reflects how **modern data engineering systems are built in real-world pipelines**.

---

## 🧠 Overview

The framework enables:

- Multi-source data ingestion  
- Configurable transformation pipelines  
- Data quality validation  
- Lineage tracking and observability  
- Automated scheduling with orchestration  

Designed for **scalable, reliable, and auditable data workflows**.

---

## ⚙️ Core Features

### 📥 Multi-Source Extraction
- Supports:
  - CSV files  
  - APIs (paginated collection with retries and partial-failure handling)
  - Databases  
  - Streaming sources  

#### API pagination, retries and partial-failure semantics

An `api` source can declare a `pagination` block under `source` in
`pipeline_config.yaml`:

```yaml
source:
  type: api
  url: https://api.example.com/v1/orders
  records_path: data.items          # applied independently to every page
  pagination:
    page_param: page                # query parameter holding the page number
    start_page: 1                   # first page number (default: 1)
    page_size: 100                  # per-page size (omitted from the request when absent)
    page_size_param: per_page       # size query parameter (default: per_page)
    max_pages: 50                   # hard stop even if no empty page is returned
  retry:
    max_retries: 3                  # extra attempts per page on HTTP 429/5xx and network errors
    backoff_seconds: 1              # exponential backoff base: 1s, 2s, 4s, ...
```

Behaviour:

- The extractor requests pages one at a time and stops on the **first empty
  page** or once **`max_pages`** pages have been requested — it never requests
  beyond the stopping boundary.
- Each failed page is retried up to `max_retries` times (HTTP 429, 5xx and
  connection/timeout errors; other 4xx errors fail immediately).
- If a page still fails after retries, the pages already fetched are **kept**,
  the failed page number and reason are recorded, and the run ends with a
  `PartialExtractionError` carrying the partial `DataFrame` — never a silent
  success and never a loss of collected data.
- Without a `pagination` block the source keeps its original single-GET
  behaviour; `records_path` extraction continues to apply in both modes.


### 🔄 Transformation Engine
- Data cleaning and normalization  
- Aggregation and enrichment  
- Rule-based transformations  

### 🧪 Data Quality Monitoring
- Validates:
  - Completeness  
  - Accuracy  
  - Freshness  
  - Consistency  

### 🧭 Pipeline DAG Visualization
- Directed Acyclic Graph (DAG) view  
- Clear pipeline stage dependencies  

### 🔍 Lineage Tracking
- End-to-end data provenance  
- Track data flow across pipeline stages  

### 🚨 Alert System
- Threshold-based alerts  
- Detect failures and anomalies in pipelines  

### ⚡ Throughput Metrics
- Records processed per second  
- Latency tracking  
- Error rate monitoring  

---

## 🧬 System Workflow

```text
Data Sources (CSV / API / DB / Stream)
↓
Extraction Layer
↓
Transformation Engine
↓
Data Quality Validation
↓
Load to Targets
(Warehouse / Data Lake)
↓
Monitoring & Alerting Layer
```

---

## 🗂️ Project Structure

```text
extractors/
├── csv_extractor.py
├── api_extractor.py
├── db_extractor.py
└── stream_extractor.py

transformers/
├── cleaner.py
├── normalizer.py
└── aggregator.py

loaders/
├── warehouse_loader.py
└── lake_loader.py

quality/
├── checks.py
└── lineage.py

etl/
├── config.py
├── metrics.py
├── pipeline.py
└── run.py

orchestration/
└── dag_definition.py

data/
└── sample_orders.csv

pipeline_config.yaml
Dockerfile
requirements.txt
tests/
└── test_pipeline.py
```

---

## 🚀 Quick Start

### Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run ETL pipeline

```bash id="etlrun1"
python -m etl.run --config pipeline_config.yaml --schedule hourly
```

---

## 🧪 Tech Stack

* Python 3.10+
* Apache Airflow
* Pandas / Polars
* Great Expectations
* SQLAlchemy
* Docker

---

## 📈 What This Project Demonstrates

✔ Data engineering pipeline design
✔ ETL architecture (Extract, Transform, Load)
✔ Workflow orchestration (Airflow DAGs)
✔ Data quality validation systems
✔ Pipeline observability & monitoring
✔ Scalable data infrastructure design

---

## 👨‍💻 Author

**Sai Teja Bandaru**
*Data Scientist & Data Engineer*

🌐 Portfolio
💼 LinkedIn
💻 GitHub

---

## 📄 License

MIT License — see `LICENSE` for details.

---

## ⭐ Support

If you find this useful:

⭐ Star the repo
🍴 Fork it
📢 Share it

---

> Building reliable data pipelines that power modern analytics systems.
